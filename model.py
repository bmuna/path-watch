from __future__ import annotations

import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score, r2_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

import config as cfg

MODEL_PATH = Path(__file__).resolve().parent / "models" / "pathwatch.joblib"
SPEED_CSV = Path(getattr(cfg, "SPEED_CSV", "speed_log.csv"))
TRAFFIC_CSV = Path(getattr(cfg, "TRAFFIC_CSV", "traffic_log.csv"))

CTX = [
    "hour_sin", "hour_cos", "dow", "conn_i", "vpn_i", "tod_i",
    "dest_lat", "dest_lon", "dest_n", "up_mbps",
]
CLF = CTX

TOD_I = {"morning": 0, "afternoon": 1, "evening": 2, "night": 3}
CONN_I = {"wifi": 0, "hotspot": 1}
HUBS = [
    (9.0245, 38.7485, "Home / center", 1.00),
    (8.9779, 38.7993, "Bole Airport", 0.95),
    (9.0350, 38.7520, "Arada / Piassa", 0.72),
    (9.0200, 38.8300, "Yeka / CMC", 0.85),
    (9.0100, 38.7600, "Kirkos", 0.70),
    (9.0200, 38.7000, "Kolfe Keranio", 0.65),
    (8.9600, 38.7600, "Nifas Silk", 0.75),
    (9.0600, 38.7300, "Gullele", 0.55),
    (8.9200, 38.7900, "Akaki Kaliti", 0.60),
    (9.0050, 38.8100, "Bole", 0.88),
    (8.9950, 38.7850, "Megenagna", 0.80),
]


def _read_speed(path: Path | None = None) -> pd.DataFrame:
    p = path or SPEED_CSV
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["down_mbps"] = pd.to_numeric(df.get("down_mbps"), errors="coerce")
    df["up_mbps"] = pd.to_numeric(df.get("up_mbps"), errors="coerce")
    df["ts"] = pd.to_datetime(df.get("ts"), utc=True, errors="coerce")
    return df.dropna(subset=["down_mbps", "ts"])


def _read_traffic(path: Path | None = None) -> pd.DataFrame:
    p = path or TRAFFIC_CSV
    if not p.exists():
        return pd.DataFrame()
    fields = [
        "ts", "event", "remote_ip", "remote_port", "local_port", "status",
        "pid", "process", "hostname",
        "connection", "vpn", "tod", "label", "public_ip", "ssid",
        "geo_lat", "geo_lon", "geo_country", "geo_country_code",
        "geo_city", "geo_region", "geo_isp", "geo_org", "geo_asn",
    ]
    rows = []
    with open(p, newline="") as f:
        import csv
        reader = csv.reader(f)
        header = next(reader, [])
        for parts in reader:
            if not parts:
                continue
            rec = dict(zip(fields, parts[: len(fields)])) if len(parts) >= 15 else dict(zip(header, parts))
            rows.append(rec)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df.get("ts"), utc=True, errors="coerce")
    for c in ("geo_lat", "geo_lon"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["ts"])


def _join_dest(speed: pd.DataFrame, traffic: pd.DataFrame, dest_geo: dict | None = None) -> pd.DataFrame:
    out = speed.copy()
    out["dest_lat"] = np.nan
    out["dest_lon"] = np.nan
    out["dest_n"] = 0.0
    if traffic.empty or "remote_ip" not in traffic.columns:
        return out
    t = traffic.dropna(subset=["ts"]).sort_values("ts").copy()
    if dest_geo:
        lats, lons = [], []
        for ip in t["remote_ip"].astype(str):
            g = dest_geo.get(ip) or {}
            lats.append(g.get("lat", np.nan))
            lons.append(g.get("lon", np.nan))
        t["geo_lat"] = pd.to_numeric(lats, errors="coerce")
        t["geo_lon"] = pd.to_numeric(lons, errors="coerce")
    left = t[["ts", "remote_ip", "geo_lat", "geo_lon"]].copy() if "geo_lat" in t.columns else t[["ts", "remote_ip"]].copy()
    if "geo_lat" not in left.columns:
        left["geo_lat"] = np.nan
        left["geo_lon"] = np.nan
    right = out[["ts"]].sort_values("ts")
    try:
        m = pd.merge_asof(left.sort_values("ts"), right, on="ts", direction="nearest", tolerance=pd.Timedelta("30s"))
    except Exception:
        return out
    if m.empty:
        return out
    g = m.groupby("ts").agg(
        dest_n=("remote_ip", "count"),
        dest_lat=("geo_lat", "mean"),
        dest_lon=("geo_lon", "mean"),
    )
    out = out.set_index("ts")
    out.update(g)
    return out.reset_index()


def encode(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    hour = df["ts"].dt.hour.astype(float) if "ts" in df.columns else pd.Series(0.0, index=df.index)
    x["hour_sin"] = np.sin(2 * math.pi * hour / 24.0)
    x["hour_cos"] = np.cos(2 * math.pi * hour / 24.0)
    x["dow"] = df["ts"].dt.dayofweek.astype(float) if "ts" in df.columns else 0.0
    x["conn_i"] = df.get("connection", pd.Series(index=df.index)).map(CONN_I).astype(float)
    x["vpn_i"] = (df.get("vpn", pd.Series(index=df.index)) == "vpn").astype(float)
    x["tod_i"] = df.get("tod", pd.Series(index=df.index)).map(TOD_I).astype(float)
    x["dest_lat"] = pd.to_numeric(df.get("dest_lat"), errors="coerce")
    x["dest_lon"] = pd.to_numeric(df.get("dest_lon"), errors="coerce")
    x["dest_n"] = pd.to_numeric(df.get("dest_n"), errors="coerce").fillna(0.0)
    x["up_mbps"] = pd.to_numeric(df.get("up_mbps"), errors="coerce")
    return x


def weak_labels(df: pd.DataFrame) -> pd.Series:
    y = pd.Series(np.nan, index=df.index, dtype=float)
    if df.empty:
        return y
    cells = df.groupby(["connection", "tod", "vpn"])["down_mbps"].median()

    for (conn, tod), sub in df.groupby(["connection", "tod"]):
        try:
            on = float(cells[(conn, tod, "vpn")])
            off = float(cells[(conn, tod, "novpn")])
        except KeyError:
            continue
        if on < 0.05 or off >= on * 0.85:
            continue
        nov = sub["vpn"] == "novpn"
        y.loc[sub.index[nov & (sub["down_mbps"] < on * 0.85)]] = 1.0
        y.loc[sub.index[nov & (sub["down_mbps"] > on * 0.95)]] = 0.0
        y.loc[sub.index[(sub["vpn"] == "vpn") & (sub["down_mbps"] > on * 0.65)]] = 0.0

    for (conn, vpn), sub in df.groupby(["connection", "vpn"]):
        meds = sub.groupby("tod")["down_mbps"].median()
        if len(meds) < 2:
            continue
        best = float(meds.max())
        if best < 0.08:
            continue
        for tod, m in meds.items():
            if float(m) >= best * 0.65:
                continue
            sl = sub[sub["tod"] == tod]
            y.loc[sl.index[sl["down_mbps"] < best * 0.50]] = 1.0
            y.loc[sl.index[sl["down_mbps"] > best * 0.90]] = 0.0

    return y


def _bundle_dests(traffic: pd.DataFrame) -> dict:
    dests = {}
    if traffic.empty or "remote_ip" not in traffic.columns:
        return dests
    t = traffic.copy()
    if "geo_lat" not in t.columns:
        t["geo_lat"] = np.nan
        t["geo_lon"] = np.nan
    t["geo_lat"] = pd.to_numeric(t["geo_lat"], errors="coerce")
    t["geo_lon"] = pd.to_numeric(t["geo_lon"], errors="coerce")
    prefix_hint = {
        "1.1.1.": (37.386, -122.084, "Los Angeles", "United States", "Cloudflare"),
        "8.8.8.": (37.4056, -122.0775, "Mountain View", "United States", "Google"),
        "8.8.4.": (37.4056, -122.0775, "Mountain View", "United States", "Google"),
        "104.16.": (37.762, -122.414, "San Francisco", "United States", "Cloudflare"),
        "104.17.": (37.762, -122.414, "San Francisco", "United States", "Cloudflare"),
        "104.18.": (37.762, -122.414, "San Francisco", "United States", "Cloudflare"),
        "104.21.": (37.762, -122.414, "San Francisco", "United States", "Cloudflare"),
        "13.32.": (47.61, -122.33, "Seattle", "United States", "Amazon"),
        "13.33.": (47.61, -122.33, "Seattle", "United States", "Amazon"),
        "13.107.": (47.642, -122.137, "Redmond", "United States", "Microsoft"),
        "52.1.": (39.0438, -77.4874, "Ashburn", "United States", "Amazon"),
        "52.84.": (39.0438, -77.4874, "Ashburn", "United States", "Amazon"),
        "142.250.": (37.4056, -122.0775, "Mountain View", "United States", "Google"),
        "142.251.": (37.4056, -122.0775, "Mountain View", "United States", "Google"),
        "157.240.": (37.4847, -122.1477, "Menlo Park", "United States", "Facebook"),
        "31.13.": (37.4847, -122.1477, "Menlo Park", "United States", "Facebook"),
        "149.154.": (51.5074, -0.1278, "London", "United Kingdom", "Telegram"),
        "91.108.": (51.5074, -0.1278, "London", "United Kingdom", "Telegram"),
        "196.188.": (9.03, 38.74, "Addis Ababa", "Ethiopia", "Ethio Telecom"),
        "196.189.": (9.03, 38.74, "Addis Ababa", "Ethiopia", "Ethio Telecom"),
        "196.191.": (9.03, 38.74, "Addis Ababa", "Ethiopia", "Ethio Telecom"),
    }
    try:
        from geo_cache import geo, is_private
        ips = [str(i) for i in t["remote_ip"].dropna().unique() if not is_private(str(i))]
        geo.resolve_many(ips, limit=150)
        known = geo.all_known()
    except Exception:
        known = {}
        def is_private(ip: str) -> bool:
            return ip.startswith(("10.", "172.20.", "172.16.", "192.168.", "127."))
    for ip, g in t.groupby("remote_ip"):
        ip = str(ip)
        row = g.iloc[0]
        lat = row.get("geo_lat")
        lon = row.get("geo_lon")
        city = str(row.get("geo_city") or "")
        country = str(row.get("geo_country") or "")
        isp = str(row.get("geo_isp") or "")
        if (pd.isna(lat) or pd.isna(lon)) and ip in known:
            lat = known[ip].get("lat")
            lon = known[ip].get("lon")
            city = city or known[ip].get("city") or ""
            country = country or known[ip].get("country") or ""
            isp = isp or known[ip].get("isp") or ""
        if pd.isna(lat) or pd.isna(lon):
            for pref, hint in prefix_hint.items():
                if ip.startswith(pref):
                    lat, lon, city, country, isp = hint[0], hint[1], city or hint[2], country or hint[3], isp or hint[4]
                    break
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
            continue
        dests[ip] = {
            "lat": lat_f,
            "lon": lon_f,
            "city": city,
            "country": country,
            "isp": isp,
            "n": int(len(g)),
        }
    return dests


def train(speed_path: Path | None = None, traffic_path: Path | None = None, out: Path | None = None) -> dict:
    speed = _read_speed(speed_path)
    traffic = _read_traffic(traffic_path)
    dests = _bundle_dests(traffic)
    df = _join_dest(speed, traffic, dests)
    if len(df) < 80:
        raise ValueError(f"not enough speed rows ({len(df)})")

    Xc = encode(df)
    y_reg = np.log1p(df["down_mbps"].clip(lower=0).to_numpy())
    reg = HistGradientBoostingRegressor(max_depth=6, max_iter=180, learning_rate=0.06, min_samples_leaf=20)
    reg.fit(Xc[CTX], y_reg)
    expected = np.expm1(reg.predict(Xc[CTX]))
    residual = expected - df["down_mbps"].to_numpy()
    r2 = float(r2_score(y_reg, reg.predict(Xc[CTX])))

    y = weak_labels(df)
    mask = y.notna()
    if int(mask.sum()) < 40 or y[mask].nunique() < 2:
        raise ValueError("weak labels did not produce two classes")

    Xf = encode(df)
    yb = y[mask].astype(int)
    Xb = Xf.loc[mask, CLF]
    clf = HistGradientBoostingClassifier(max_depth=5, max_iter=160, learning_rate=0.08, min_samples_leaf=25)
    clf.fit(Xb, yb)
    proba = clf.predict_proba(Xb)[:, 1]
    auc = float(roc_auc_score(yb, proba))

    n_splits = min(5, int(yb.value_counts().min()))
    cv_auc = auc
    if n_splits >= 2:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=0)
        cv_auc = float(cross_val_score(clf, Xb, yb, cv=cv, scoring="roc_auc").mean())

    n_perm = min(len(Xb), 2500)
    rng = np.random.RandomState(0)
    idx = rng.choice(len(Xb), size=n_perm, replace=False)
    perm = permutation_importance(
        clf, Xb.iloc[idx], yb.iloc[idx], n_repeats=3, random_state=0, scoring="roc_auc",
    )
    imp = {n: float(v) for n, v in zip(CLF, perm.importances_mean)}

    report = {
        "n": int(len(df)),
        "n_labeled": int(mask.sum()),
        "n_pos": int((yb == 1).sum()),
        "n_neg": int((yb == 0).sum()),
        "auc": round(auc, 4),
        "cv_auc": round(cv_auc, 4),
        "r2": round(r2, 4),
        "importances": {k: round(v, 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])},
        "destinations": len(dests),
        "labels": sorted(df["label"].dropna().unique().tolist()) if "label" in df.columns else [],
    }
    payload = {
        "reg": reg,
        "clf": clf,
        "dests": dests,
        "report": report,
        "vantage": (
            float(getattr(cfg, "VANTAGE_LAT", 9.0245)),
            float(getattr(cfg, "VANTAGE_LON", 38.7485)),
            str(getattr(cfg, "VANTAGE_CITY", "Addis Ababa")),
        ),
    }
    path = out or MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, path)
    return report


_cache = {"mtime": None, "bundle": None}


def load(path: Path | None = None) -> dict | None:
    p = path or MODEL_PATH
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _cache["bundle"] is not None and _cache["mtime"] == mt:
        return _cache["bundle"]
    bundle = joblib.load(p)
    _cache["bundle"] = bundle
    _cache["mtime"] = mt
    return bundle


def ensure_trained() -> dict | None:
    if load() is not None:
        return load()
    if not SPEED_CSV.exists():
        return None
    try:
        train()
    except Exception:
        return None
    return load()


def _row(ts, connection, vpn, tod, up, dest_lat=np.nan, dest_lon=np.nan, dest_n=0.0, down=np.nan, residual=np.nan):
    rec = {
        "ts": pd.Timestamp(ts, tz="UTC") if not isinstance(ts, pd.Timestamp) else ts,
        "connection": connection,
        "vpn": vpn,
        "tod": tod,
        "up_mbps": up,
        "dest_lat": dest_lat,
        "dest_lon": dest_lon,
        "dest_n": dest_n,
        "down_mbps": down,
        "residual": residual,
    }
    return pd.DataFrame([rec])


def _predict_heads(bundle: dict, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    Xc = encode(frame)
    expected = np.expm1(bundle["reg"].predict(Xc[CTX]))
    expected = np.clip(expected, 0.0, None)
    p = bundle["clf"].predict_proba(Xc[CLF])[:, 1]
    return expected, p


def _blend(p: float, expected: float, down: float) -> int:
    if expected <= 0.02:
        ratio = 1.0
    else:
        ratio = max(0.0, min(2.0, down / expected))
    resid_term = 1.0 / (1.0 + math.exp((ratio - 0.55) * 8.0))
    raw = 0.55 * float(p) + 0.45 * resid_term
    return int(max(0, min(100, round(raw * 100))))


def score_live(down: float, up: float, label: str, dests: list | None = None) -> dict:
    bundle = ensure_trained()
    parts = (label or "").split("_")
    conn = parts[0] if parts else "wifi"
    vpn = parts[1] if len(parts) > 1 else "novpn"
    tod = parts[2] if len(parts) > 2 else "evening"
    now = pd.Timestamp.now(tz="UTC")
    dlat = dlon = np.nan
    dn = 0.0
    if dests:
        lats = [float(d["lat"]) for d in dests if d.get("lat") is not None]
        lons = [float(d["lon"]) for d in dests if d.get("lon") is not None]
        if lats:
            dlat, dlon = float(np.mean(lats)), float(np.mean(lons))
        dn = float(len(dests))
    if bundle is None:
        return {"score": 0, "reason": "Model not trained yet", "baseline_down": None, "p_throttled": 0.0, "expected_mbps": None, "source": "none"}
    frame = _row(now, conn, vpn, tod, up, dlat, dlon, dn, down)
    expected, proba = _predict_heads(bundle, frame)
    exp, p = float(expected[0]), float(proba[0])
    score = _blend(p, exp, float(down))
    flip = _row(now, conn, "vpn" if vpn == "novpn" else "novpn", tod, up, dlat, dlon, dn, down)
    exp_cf, p_cf = _predict_heads(bundle, flip)
    if p >= 0.62:
        reason = f"Model {p:.0%} throttle risk — {down:.2f} Mbps vs expected {exp:.2f}"
    elif p >= 0.35:
        reason = f"Context looks degraded ({p:.0%}) — {down:.2f} vs {exp:.2f} Mbps"
    else:
        reason = f"In-distribution — {down:.2f} Mbps vs expected {exp:.2f}"
    return {
        "score": score,
        "reason": reason,
        "baseline_down": round(exp, 3),
        "p_throttled": round(p, 4),
        "expected_mbps": round(exp, 3),
        "p_counterfactual": round(float(p_cf[0]), 4),
        "expected_counterfactual": round(float(exp_cf[0]), 3),
        "source": "model",
        "cv_auc": bundle["report"].get("cv_auc"),
    }


def _field(vlat: float, vlon: float, intensity: float) -> list[dict]:
    pts = []
    for i, (hlat, hlon, name, w) in enumerate(HUBS):
        hub_i = float(np.clip(intensity * w, 0.08, 0.98))
        n = 16 + int(hub_i * 26)
        for k in range(n):
            ang = (2 * math.pi * k / n) + (i * 0.31)
            rad = 0.010 + hub_i * 0.024 * (0.35 + (k % 7) / 7)
            stretch = 1.0 + 0.32 * math.sin(ang * 2 + i)
            lat = hlat + rad * math.cos(ang) * stretch
            lon = hlon + rad * math.sin(ang) * (1.12 / stretch)
            dist = math.hypot(lat - hlat, lon - hlon)
            fall = math.exp(-dist / max(0.012 + hub_i * 0.02, 0.01))
            pts.append({
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "intensity": round(float(np.clip(hub_i * (0.42 + 0.58 * fall), 0.08, 0.98)), 3),
                "kind": "city",
                "label": name,
            })
    for yi, lat in enumerate(np.linspace(8.90, 9.08, 10)):
        for xi, lon in enumerate(np.linspace(38.70, 38.86, 10)):
            dmin = min(math.hypot(lat - h[0], lon - h[1]) for h in HUBS)
            if dmin > 0.055:
                continue
            fall = math.exp(-dmin / 0.04)
            jitter_lat = ((yi * 17 + xi * 9) % 7 - 3) * 0.0008
            jitter_lon = ((yi * 11 + xi * 5) % 7 - 3) * 0.0008
            pts.append({
                "lat": round(float(lat + jitter_lat), 5),
                "lon": round(float(lon + jitter_lon), 5),
                "intensity": round(float(np.clip(intensity * 0.34 * fall, 0.06, 0.7)), 3),
                "kind": "city",
                "label": "Addis Ababa",
            })
    pts.append({
        "lat": vlat, "lon": vlon,
        "intensity": round(float(np.clip(intensity, 0.15, 0.98)), 3),
        "kind": "vantage", "label": "Your machine",
    })
    return pts


def _zones(intensity: float, tod_slow: dict | None) -> list[dict]:
    out = []
    for hlat, hlon, name, w in HUBS:
        i = float(np.clip(intensity * w, 0.1, 0.98))
        if i < 0.28:
            continue
        out.append({
            "lat": hlat, "lon": hlon,
            "radius_m": int(2200 + i * 5500),
            "intensity": round(i, 3),
            "city": "Addis Ababa",
            "label": name,
            "tod_slow": tod_slow if w >= 0.9 else None,
        })
    return out or [{
        "lat": HUBS[0][0], "lon": HUBS[0][1],
        "radius_m": 3500, "intensity": round(intensity, 3),
        "city": "Addis Ababa", "label": "Your path", "tod_slow": tod_slow,
    }]


def map_layers(speed: pd.DataFrame, traffic: pd.DataFrame, live: dict | None = None) -> dict:
    bundle = ensure_trained()
    vlat = float(getattr(cfg, "VANTAGE_LAT", 9.0245))
    vlon = float(getattr(cfg, "VANTAGE_LON", 38.7485))
    vcity = str(getattr(cfg, "VANTAGE_CITY", "Addis Ababa"))
    live = live or {}
    conn = live.get("connection") or (speed["connection"].mode().iloc[0] if not speed.empty and "connection" in speed.columns else "wifi")
    vpn = live.get("vpn") or (speed["vpn"].mode().iloc[0] if not speed.empty and "vpn" in speed.columns else "novpn")
    tod = live.get("tod") or (speed["tod"].mode().iloc[0] if not speed.empty and "tod" in speed.columns else "evening")
    up = float(live.get("up_mbps") or 0.0)
    down = float(live.get("down_mbps") or (speed["down_mbps"].iloc[-1] if not speed.empty else 0.0))
    now = pd.Timestamp.now(tz="UTC")
    if not speed.empty and "ts" in speed.columns:
        now = speed["ts"].iloc[-1]

    tod_slow = None
    if bundle is None:
        intensity = 0.22
        dest_heat, map_points = [], []
        report = {"trained": False}
    else:
        frame = _row(now, conn, vpn, tod, up, dest_n=float(len(bundle.get("dests") or {})), down=down)
        expected, proba = _predict_heads(bundle, frame)
        intensity = float(np.clip(proba[0], 0.12, 0.98))
        report = {**bundle["report"], "trained": True, "p_now": round(float(proba[0]), 4), "expected_now": round(float(expected[0]), 3)}

        grid = []
        for h in range(24):
            ts = now.normalize() + pd.Timedelta(hours=h)
            bucket = "night" if h < 6 else "morning" if h < 12 else "afternoon" if h < 17 else "evening" if h < 22 else "night"
            fr = _row(ts, conn, vpn, bucket, up, dest_n=float(len(bundle.get("dests") or {})), down=np.nan)
            _, ph = _predict_heads(bundle, fr)
            grid.append({"hour": h, "tod": bucket, "p": round(float(ph[0]), 4)})
        report["hour_curve"] = grid
        if grid:
            worst = max(grid, key=lambda r: r["p"])
            best = min(grid, key=lambda r: r["p"])
            if worst["p"] > best["p"] * 1.25:
                tod_slow = {"tod": worst["tod"], "ratio": round(best["p"] / max(worst["p"], 1e-6), 3)}

        dest_heat, map_points = [], []
        dests = dict(bundle.get("dests") or {})
        if not traffic.empty and "remote_ip" in traffic.columns:
            try:
                from geo_cache import geo, is_private
                ips = [str(i) for i in traffic["remote_ip"].dropna().unique() if not is_private(str(i))]
                geo.resolve_many(ips[:80], limit=80)
                for ip, g in geo.all_known().items():
                    if ip in dests or g.get("lat") is None:
                        continue
                    dests[ip] = {
                        "lat": g["lat"], "lon": g["lon"],
                        "city" : g.get("city") or "",
                        "country": g.get("country") or "",
                        "isp": g.get("isp") or "",
                        "n": 1,
                    }
            except Exception:
                pass
        by_cell: dict[tuple, list] = {}
        for ip, d in dests.items():
            fr = _row(now, conn, vpn, tod, up, d["lat"], d["lon"], float(d.get("n") or 1), down)
            exp_d, p_d = _predict_heads(bundle, fr)
            rec = {
                "remote_ip": ip,
                "lat": d["lat"],
                "lon": d["lon"],
                "city": d.get("city") or "",
                "country": d.get("country") or "",
                "isp": d.get("isp") or "",
                "connections": int(d.get("n") or 1),
                "p_throttled": round(float(p_d[0]), 4),
                "expected_mbps": round(float(exp_d[0]), 3),
            }
            map_points.append(rec)
            key = (round(d["lat"], 1), round(d["lon"], 1))
            by_cell.setdefault(key, []).append(rec)
        for (lat, lon), recs in by_cell.items():
            dest_heat.append({
                "lat": lat,
                "lon": lon,
                "intensity": round(float(np.mean([r["p_throttled"] for r in recs])), 3),
                "kind": "dest",
                "n": sum(r["connections"] for r in recs),
                "label": recs[0].get("city") or recs[0].get("country") or "",
                "p_throttled": round(float(np.mean([r["p_throttled"] for r in recs])), 4),
            })

    city_heat = _field(vlat, vlon, intensity)
    return {
        "city_heat": city_heat,
        "geo_heat": city_heat,
        "dest_heat": dest_heat,
        "throttle_areas": _zones(intensity, tod_slow),
        "map_points": map_points,
        "path_meta": {
            "samples": int(len(speed)),
            "destinations": len(map_points),
            "intensity": round(intensity, 3),
            "city": vcity,
            "tod_slow": tod_slow,
            "note": "Heat from the trained path model (time · link · VPN · destination)",
        },
        "model": report,
    }


def model_hour_heatmap(speed: pd.DataFrame) -> list[dict]:
    bundle = load()
    if bundle is None or speed.empty or "ts" not in speed.columns:
        return []
    out = []
    for (dow_i, hour), g in speed.groupby([speed["ts"].dt.dayofweek, speed["ts"].dt.hour]):
        conn = g["connection"].mode().iloc[0] if "connection" in g.columns else "wifi"
        vpn = g["vpn"].mode().iloc[0] if "vpn" in g.columns else "novpn"
        tod = g["tod"].mode().iloc[0] if "tod" in g.columns else "evening"
        ts = g["ts"].iloc[0]
        fr = _row(ts, conn, vpn, tod, float(g["up_mbps"].median()) if "up_mbps" in g.columns else 0.0, dest_n=1.0)
        _, p = _predict_heads(bundle, fr)
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        out.append({
            "hour": int(hour),
            "day": names[int(dow_i)] if 0 <= int(dow_i) < 7 else str(dow_i),
            "value": round(float(p[0]), 3),
            "p_throttled": round(float(p[0]), 4),
            "n": int(len(g)),
        })
    return out
