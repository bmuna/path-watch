#!/usr/bin/env python3
"""
Throttle scoring model.

Learns from every connection your machine makes — not from probing specific
sites. Detects anomalies in:
  - per-ISP/ASN speed drops
  - time-of-day patterns
  - VPN vs no-VPN gaps
  - speed degradation over time within a session

Produces a 0-100 throttle score: 0 = fine, 100 = strong throttling signal.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import config as cfg
from model import map_layers, model_hour_heatmap, score_live

SPEED_CSV   = Path(getattr(cfg, "SPEED_CSV",   "speed_log.csv"))
TRAFFIC_CSV = Path(getattr(cfg, "TRAFFIC_CSV", "traffic_log.csv"))


def _read_speed() -> pd.DataFrame:
    if not SPEED_CSV.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(SPEED_CSV)
        for c in ("down_mbps", "up_mbps"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        return df.dropna(subset=["down_mbps"])
    except Exception:
        return pd.DataFrame()


def _read_traffic() -> pd.DataFrame:
    if not TRAFFIC_CSV.exists():
        return pd.DataFrame()
    fields = [
        "ts", "event", "remote_ip", "remote_port", "local_port", "status",
        "pid", "process", "hostname",
        "connection", "vpn", "tod", "label", "public_ip", "ssid",
        "geo_lat", "geo_lon", "geo_country", "geo_country_code",
        "geo_city", "geo_region", "geo_isp", "geo_org", "geo_asn",
    ]
    try:
        import csv
        rows = []
        with open(TRAFFIC_CSV, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            for parts in reader:
                if not parts:
                    continue
                if len(parts) >= len(fields):
                    rec = dict(zip(fields, parts[:len(fields)]))
                else:
                    rec = dict(zip(header, parts))
                rows.append(rec)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        for c in ("geo_lat", "geo_lon"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _py(v):
    if v is None:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        if np.isnan(v):
            return None
        return float(v)
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def _vantage():
    return (
        float(getattr(cfg, "VANTAGE_LAT", 9.0245)),
        float(getattr(cfg, "VANTAGE_LON", 38.7485)),
        str(getattr(cfg, "VANTAGE_CITY", "Addis Ababa")),
    )


def _backfill_traffic_geo(traffic: pd.DataFrame) -> pd.DataFrame:
    """Fill missing lat/lng from the live geo cache (connect often logs before lookup returns)."""
    if traffic.empty or "remote_ip" not in traffic.columns:
        return traffic
    df = traffic.copy()
    ips = [str(x) for x in df["remote_ip"].dropna().unique().tolist()]
    try:
        from geo_cache import geo
        if ips:
            geo.resolve_many(ips, limit=100)
        known = geo.all_known()
    except Exception:
        known = {}
    for col in ("geo_lat", "geo_lon", "geo_country", "geo_city", "geo_isp", "geo_region"):
        if col not in df.columns:
            df[col] = np.nan
    df["geo_lat"] = pd.to_numeric(df["geo_lat"], errors="coerce")
    df["geo_lon"] = pd.to_numeric(df["geo_lon"], errors="coerce")

    if not known:
        return df

    def fill_row(row):
        if pd.notna(row["geo_lat"]) and pd.notna(row["geo_lon"]):
            return row
        g = known.get(str(row.get("remote_ip") or ""))
        if not g:
            return row
        if g.get("lat") is not None:
            row["geo_lat"] = g["lat"]
        if g.get("lon") is not None:
            row["geo_lon"] = g["lon"]
        for src, dst in (
            ("country", "geo_country"),
            ("city", "geo_city"),
            ("isp", "geo_isp"),
            ("region", "geo_region"),
        ):
            if (pd.isna(row.get(dst)) or row.get(dst) == "") and g.get(src):
                row[dst] = g[src]
        return row

    return df.apply(fill_row, axis=1)


# Addis Ababa metro — heat is painted HERE (where you feel throttle),
# not on remote CDN cities. All sockets from this machine feed intensity.
_ADDIS_HUBS = [
    # lat, lon, name, weight (1 = full intensity)
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


def _path_intensity(speed: pd.DataFrame) -> tuple[float, dict | None, dict]:
    """
    One intensity for the whole path out of this machine.
    Uses ALL logged traffic conditions (tod / vpn / wifi), not one site.
    """
    meta = {"samples": 0, "destinations": 0}
    if speed.empty:
        return 0.22, None, meta

    meta["samples"] = int(len(speed))
    med = float(speed["down_mbps"].median())
    # recent window vs same-condition baseline (not peak — bursty peaks lie)
    recent = speed.tail(min(40, max(8, len(speed) // 8)))
    recent_med = float(recent["down_mbps"].median())

    intensity = 0.25
    tod_slow = None

    if "label" in speed.columns and len(recent):
        lbl = str(recent["label"].mode().iloc[0]) if recent["label"].notna().any() else ""
        base = speed.loc[speed["label"] == lbl, "down_mbps"] if lbl else speed["down_mbps"]
        if len(base) < 8:
            base = speed["down_mbps"]
        bmed = float(base.median())
        if bmed > 0.05:
            ratio = recent_med / bmed
            if ratio >= 0.85:
                intensity = 0.18
            elif ratio >= 0.55:
                intensity = 0.35 + (0.85 - ratio) * 0.8
            elif ratio >= 0.30:
                intensity = 0.55 + (0.55 - ratio) * 0.9
            else:
                intensity = min(0.95, 0.78 + (0.30 - ratio))

    if "tod" in speed.columns and speed["tod"].nunique() > 1:
        tod_meds = speed.groupby("tod")["down_mbps"].median()
        if len(tod_meds) >= 2 and float(tod_meds.max()) > 0:
            worst = tod_meds.idxmin()
            ratio = float(tod_meds.min() / max(float(tod_meds.max()), 0.01))
            tod_slow = {"tod": str(worst), "ratio": round(ratio, 3)}
            # evening (etc.) historically slower → raise city heat
            if ratio < 0.75:
                intensity = float(np.clip(intensity + (1 - ratio) * 0.45, 0.15, 0.98))

    if "vpn" in speed.columns and speed["vpn"].nunique() > 1:
        groups = {k: g["down_mbps"] for k, g in speed.groupby("vpn") if len(g) >= 5}
        if "vpn" in groups and "novpn" in groups:
            on, off = float(groups["vpn"].median()), float(groups["novpn"].median())
            if on > 0.05 and off < on * 0.75:
                # faster with VPN → stronger throttle signal on the local path
                intensity = float(np.clip(intensity + 0.25, 0.15, 0.98))

    meta["median_down"] = round(med, 3)
    meta["recent_down"] = round(recent_med, 3)
    return float(np.clip(intensity, 0.12, 0.98)), tod_slow, meta


def _city_heat_field(vlat: float, vlon: float, intensity: float) -> list[dict]:
    """
    Dense Addis Ababa heat field (traffic-map style).
    Throttle is felt on the local path — paint the metro, not Ashburn/Singapore.
    """
    rng = np.random.RandomState(int(abs(vlat * 1000 + vlon * 100)) % 10_000)
    points = []

    for hlat, hlon, name, w in _ADDIS_HUBS:
        hub_i = float(np.clip(intensity * w, 0.08, 0.98))
        # denser cloud when hotter (looks like the purple metro blobs)
        n = 18 + int(hub_i * 28)
        spread = 0.018 + hub_i * 0.022
        for _ in range(n):
            # elongated / irregular — not a perfect circle spotlight
            ang = rng.uniform(0, 2 * np.pi)
            rad = abs(rng.normal(0, spread))
            stretch = 1.0 + 0.35 * np.sin(ang * 2)
            lat = hlat + rad * np.cos(ang) * stretch
            lon = hlon + rad * np.sin(ang) * (1.15 / stretch)
            # falloff from hub center
            dist = np.hypot(lat - hlat, lon - hlon)
            fall = float(np.exp(-dist / max(spread, 0.01)))
            points.append({
                "lat": round(float(lat), 5),
                "lon": round(float(lon), 5),
                "intensity": round(float(np.clip(hub_i * (0.45 + 0.55 * fall), 0.08, 0.98)), 3),
                "kind": "city",
                "label": name,
            })

    # light fill grid so the city reads as one connected field
    for lat in np.linspace(8.90, 9.08, 10):
        for lon in np.linspace(38.70, 38.86, 10):
            # distance to nearest hub
            dmin = min(np.hypot(lat - h[0], lon - h[1]) for h in _ADDIS_HUBS)
            if dmin > 0.055:
                continue
            fall = float(np.exp(-dmin / 0.04))
            points.append({
                "lat": round(float(lat + rng.normal(0, 0.003)), 5),
                "lon": round(float(lon + rng.normal(0, 0.003)), 5),
                "intensity": round(float(np.clip(intensity * 0.35 * fall, 0.06, 0.7)), 3),
                "kind": "city",
                "label": "Addis Ababa",
            })

    # always pin the machine
    points.append({
        "lat": vlat, "lon": vlon,
        "intensity": round(float(np.clip(intensity, 0.15, 0.98)), 3),
        "kind": "vantage", "label": "Your machine",
    })
    return points


def _throttle_blobs(intensity: float, tod_slow: dict | None) -> list[dict]:
    """Soft metro zones (the purple overlays in traffic apps)."""
    blobs = []
    for hlat, hlon, name, w in _ADDIS_HUBS:
        i = float(np.clip(intensity * w, 0.1, 0.98))
        if i < 0.28:
            continue
        blobs.append({
            "lat": hlat,
            "lon": hlon,
            "radius_m": int(2200 + i * 5500),
            "intensity": round(i, 3),
            "city": "Addis Ababa",
            "label": name,
            "tod_slow": tod_slow if w >= 0.9 else None,
        })
    return blobs or [{
        "lat": _ADDIS_HUBS[0][0], "lon": _ADDIS_HUBS[0][1],
        "radius_m": 3500, "intensity": round(intensity, 3),
        "city": "Addis Ababa", "label": "Your path", "tod_slow": tod_slow,
    }]


def _attach_geo(result: dict, speed: pd.DataFrame, traffic: pd.DataFrame) -> None:
    """
    Map model:
      - city_heat / throttle_areas → Addis Ababa (where throttle is felt)
      - map_points → every remote IP this machine reached (all sockets)
    Intensity is learned from ALL destinations + conditions, not one site.
    """
    vlat, vlon, vcity = _vantage()
    traffic = _backfill_traffic_geo(traffic)

    intensity, tod_slow, meta = _path_intensity(speed)
    if not traffic.empty and "remote_ip" in traffic.columns:
        meta["destinations"] = int(traffic["remote_ip"].nunique())

    city_heat = _city_heat_field(vlat, vlon, intensity)
    throttle_areas = _throttle_blobs(intensity, tod_slow)

    map_points = []
    dest_heat = []
    if not traffic.empty and "geo_lat" in traffic.columns:
        geo_rows = traffic.copy()
        geo_rows["geo_lat"] = pd.to_numeric(geo_rows["geo_lat"], errors="coerce")
        geo_rows["geo_lon"] = pd.to_numeric(geo_rows["geo_lon"], errors="coerce")
        geo_rows = geo_rows.dropna(subset=["geo_lat", "geo_lon"])

        if not geo_rows.empty:
            med = float(speed["down_mbps"].median()) if not speed.empty else 0.0
            dest = geo_rows
            if not speed.empty and "ts" in speed.columns and "ts" in dest.columns:
                left = dest.dropna(subset=["ts"]).sort_values("ts")
                right = speed[["ts", "down_mbps"]].dropna(subset=["ts", "down_mbps"]).sort_values("ts")
                if not left.empty and not right.empty:
                    try:
                        dest = pd.merge_asof(
                            left, right, on="ts",
                            direction="nearest",
                            tolerance=pd.Timedelta("25s"),
                        )
                    except Exception:
                        dest = geo_rows.copy()
                        dest["down_mbps"] = med
            else:
                dest = dest.copy()
                dest["down_mbps"] = med

            ip_down = (
                dest.groupby("remote_ip")["down_mbps"].median().to_dict()
                if "remote_ip" in dest.columns and "down_mbps" in dest.columns
                else {}
            )

            by_ip = geo_rows.groupby("remote_ip").agg(
                lat=("geo_lat", "first"),
                lon=("geo_lon", "first"),
                country=("geo_country", "first") if "geo_country" in geo_rows.columns else ("geo_lat", "first"),
                city=("geo_city", "first") if "geo_city" in geo_rows.columns else ("geo_lat", "first"),
                isp=("geo_isp", "first") if "geo_isp" in geo_rows.columns else ("geo_lat", "first"),
                connections=("remote_ip", "count"),
            ).reset_index()
            for rec in by_ip.to_dict("records"):
                rid = rec.get("remote_ip")
                if rid in ip_down:
                    rec["down_median"] = round(float(ip_down[rid]), 3)
                map_points.append({k: _py(v) for k, v in rec.items()})

            # world view only — destinations this machine reached
            gmed = med if med > 0.01 else 1.0
            dest["lat_r"] = dest["geo_lat"].round(1)
            dest["lon_r"] = dest["geo_lon"].round(1)
            agg = dest.groupby(["lat_r", "lon_r"]).agg(
                down=("down_mbps", "median"),
                n=("remote_ip", "count") if "remote_ip" in dest.columns else ("lat_r", "count"),
                city=("geo_city", "first") if "geo_city" in dest.columns else ("lat_r", "first"),
                country=("geo_country", "first") if "geo_country" in dest.columns else ("lat_r", "first"),
            ).reset_index()
            for _, row in agg.iterrows():
                lat, lon = _num(row["lat_r"]), _num(row["lon_r"])
                if lat is None or lon is None:
                    continue
                d = _num(row.get("down"))
                if d is None:
                    inten = float(np.clip((row["n"] or 1) / 25.0, 0.12, 0.85))
                else:
                    inten = float(np.clip(1.0 - (d / gmed), 0.12, 0.9))
                dest_heat.append({
                    "lat": lat, "lon": lon,
                    "intensity": round(inten, 3),
                    "kind": "dest",
                    "n": int(row["n"]),
                    "label": str(row.get("city") or row.get("country") or ""),
                    "down": round(d, 3) if d is not None else None,
                })

            if "geo_isp" in traffic.columns:
                by_isp = (
                    traffic.dropna(subset=["geo_isp"])
                    .groupby("geo_isp")
                    .agg(
                        connections=("remote_ip", "count"),
                        countries=("geo_country", lambda x: ", ".join(x.dropna().unique()[:3])) if "geo_country" in traffic.columns else ("geo_isp", "count"),
                    )
                    .reset_index()
                    .sort_values("connections", ascending=False)
                    .head(12)
                )
                result["isp_analysis"] = [{k: _py(v) for k, v in rec.items()} for rec in by_isp.to_dict("records")]

    result["city_heat"] = city_heat
    result["geo_heat"] = city_heat  # default map = Addis path
    result["dest_heat"] = dest_heat
    result["throttle_areas"] = throttle_areas
    result["map_points"] = map_points
    result["path_meta"] = {
        **meta,
        "intensity": round(intensity, 3),
        "city": vcity,
        "tod_slow": tod_slow,
        "note": "Heat = path quality from this machine across all destinations",
    }


# ─────────────────────────────────────────────────────────────────────────────

def score_current(current_down: float, current_up: float, label: str, dests: list | None = None) -> dict:
    out = score_live(current_down, current_up, label, dests)
    if out.get("source") == "model":
        return out
    df = _read_speed()
    if df.empty or len(df) < 10:
        return {"score": 0, "reason": "Not enough history yet", "baseline_down": None}

    parts = label.split("_") if label else []
    conn = parts[0] if len(parts) > 0 else ""
    tod  = parts[2] if len(parts) > 2 else ""

    mask = pd.Series([True] * len(df))
    if "connection" in df.columns and conn:
        mask &= df["connection"] == conn
    if "tod" in df.columns and tod:
        mask &= df["tod"] == tod

    base = df.loc[mask, "down_mbps"].dropna()
    if len(base) < 5:
        base = df["down_mbps"].dropna()

    med = float(base.median())
    if med < 0.001:
        return {"score": 0, "reason": "Baseline too small to score", "baseline_down": med}

    ratio = current_down / med if med > 0 else 1.0
    if ratio >= 0.8:
        score, reason = 0, f"Normal — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    elif ratio >= 0.5:
        score, reason = int((1 - ratio) / 0.3 * 40), f"Slightly slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    elif ratio >= 0.2:
        score, reason = 40 + int((0.5 - ratio) / 0.3 * 40), f"Noticeably slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    else:
        score, reason = min(100, 80 + int((0.2 - ratio) / 0.2 * 20)), f"Very slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"

    return {
        "score": score,
        "reason": reason,
        "baseline_down": round(med, 3),
        "current_down": round(current_down, 3),
        "ratio": round(ratio, 3),
        "source": "heuristic",
    }


def analyze_all() -> dict:
    """Full analysis — runs off-thread every 15 s."""
    df = _read_speed()
    traffic = _read_traffic()
    result = {
        "samples": 0,
        "alerts": [],
        "speed": {},
        "vpn_comparison": None,
        "time_comparison": [],
        "connection_comparison": None,
        "conditions": [],
        "heatmap": [],
        "isp_analysis": [],
        "session_trend": [],
        "map_points": [],
        "destinations_geo": [],
        "geo_heat": [],
        "city_heat": [],
        "dest_heat": [],
        "throttle_areas": [],
        "path_meta": {},
        "model": {},
        "model_heatmap": [],
    }

    _attach_geo(result, df, traffic)
    last = df.iloc[-1].to_dict() if not df.empty else {}
    layers = map_layers(df, traffic, {
        "connection": last.get("connection"),
        "vpn": last.get("vpn"),
        "tod": last.get("tod"),
        "down_mbps": last.get("down_mbps"),
        "up_mbps": last.get("up_mbps"),
    })
    result.update({k: layers[k] for k in (
        "city_heat", "geo_heat", "dest_heat", "throttle_areas",
        "map_points", "path_meta", "model",
    ) if k in layers})
    mh = model_hour_heatmap(df)
    if mh:
        result["model_heatmap"] = mh

    if df.empty:
        result["alerts"].append({"level": "info", "title": "No data yet",
            "detail": "Leave the app open and use the internet. Speed and connections log every 2 seconds."})
        return result

    result["samples"] = int(len(df))
    down = df["down_mbps"].dropna()
    up   = df["up_mbps"].dropna() if "up_mbps" in df.columns else pd.Series(dtype=float)

    result["speed"] = {
        "down_median": round(float(down.median()), 3),
        "down_mean":   round(float(down.mean()),   3),
        "down_peak":   round(float(down.max()),    3),
        "down_p95":    round(float(down.quantile(0.95)), 3),
        "down_p5":     round(float(down.quantile(0.05)), 3),
        "up_median":   round(float(up.median()), 3) if len(up) else 0,
        "up_peak":     round(float(up.max()), 3)    if len(up) else 0,
    }

    med  = float(down.median())
    peak = float(down.max())
    p5   = float(down.quantile(0.05))
    p95  = float(down.quantile(0.95))

    # Alert: bursty
    if len(down) > 10 and peak > med * 3 and med > 0.05:
        result["alerts"].append({"level": "warning", "title": "Bursty / shaped link",
            "detail": f"Peak {peak:.2f} vs median {med:.2f} Mbps. Large variance suggests traffic shaping."})

    # Alert: frequent drops
    if len(down) > 10 and p5 < med * 0.1 and med > 0.1:
        result["alerts"].append({"level": "warning", "title": "Frequent speed drops",
            "detail": f"Bottom 5% is {p5:.3f} Mbps, median {med:.2f} Mbps. Possible intermittent throttling."})

    # VPN comparison
    if "vpn" in df.columns and df["vpn"].nunique() > 1:
        groups = {v: g["down_mbps"].dropna() for v, g in df.groupby("vpn") if len(g) >= 5}
        if "vpn" in groups and "novpn" in groups:
            on, off = groups["vpn"], groups["novpn"]
            mw = stats.mannwhitneyu(off.values, on.values, alternative="two-sided")
            vpn_c = {
                "vpn_on_median":  round(float(on.median()), 3),
                "vpn_off_median": round(float(off.median()), 3),
                "vpn_on_p5":      round(float(on.quantile(0.05)), 3),
                "vpn_off_p5":     round(float(off.quantile(0.05)), 3),
                "vpn_on_n":  int(len(on)),
                "vpn_off_n": int(len(off)),
                "p_value":   round(float(mw.pvalue), 4),
                "significant": bool(mw.pvalue < 0.05),
            }
            result["vpn_comparison"] = vpn_c
            diff = off.median() - on.median()
            if mw.pvalue < 0.05 and diff < -on.median() * 0.2:
                result["alerts"].append({"level": "danger", "title": "Faster with VPN ON",
                    "detail": f"VPN: {on.median():.2f} Mbps, no-VPN: {off.median():.2f} Mbps "
                              f"(p={mw.pvalue:.3f}). Strong evidence of differential treatment without VPN."})
            elif mw.pvalue < 0.05 and diff > on.median() * 0.2:
                result["alerts"].append({"level": "info", "title": "VPN adds overhead",
                    "detail": f"Normal: VPN server distance slows things slightly. Off: {off.median():.2f} vs on: {on.median():.2f} Mbps."})
            else:
                result["alerts"].append({"level": "success", "title": "VPN makes no significant difference",
                    "detail": f"No significant speed gap (p={mw.pvalue:.3f}). Traffic appears treated equally."})
    else:
        result["alerts"].append({"level": "info", "title": "Turn VPN on/off to detect throttling",
            "detail": "The key test: is your link faster or slower with VPN? Switch while the app runs."})

    # Time of day
    if "tod" in df.columns and df["tod"].nunique() > 1:
        for tod, g in df.groupby("tod"):
            d = g["down_mbps"].dropna()
            if len(d) < 2:
                continue
            result["time_comparison"].append({
                "tod": str(tod), "median": round(float(d.median()), 3),
                "p5": round(float(d.quantile(0.05)), 3),
                "p95": round(float(d.quantile(0.95)), 3), "n": int(len(d)),
            })
        if len(result["time_comparison"]) >= 2:
            meds = [t["median"] for t in result["time_comparison"]]
            if max(meds) > min(meds) * 1.5:
                slow = min(result["time_comparison"], key=lambda x: x["median"])
                fast = max(result["time_comparison"], key=lambda x: x["median"])
                result["alerts"].append({"level": "warning", "title": f"Slower at {slow['tod']}",
                    "detail": f"{slow['tod']}: {slow['median']:.2f} Mbps vs {fast['tod']}: {fast['median']:.2f} Mbps."})

    # Connection type
    if "connection" in df.columns and df["connection"].nunique() > 1:
        conn_d = {}
        for c, g in df.groupby("connection"):
            d = g["down_mbps"].dropna()
            conn_d[c] = {"median": round(float(d.median()), 3), "n": int(len(d))}
        result["connection_comparison"] = conn_d

    # Conditions
    if "label" in df.columns:
        for lbl, g in df.groupby("label"):
            d = g["down_mbps"].dropna()
            u = g["up_mbps"].dropna() if "up_mbps" in g.columns else pd.Series(dtype=float)
            if len(d) < 2:
                continue
            result["conditions"].append({
                "label": str(lbl),
                "down_median": round(float(d.median()), 3),
                "up_median":   round(float(u.median()), 3) if len(u) else 0,
                "down_p5":     round(float(d.quantile(0.05)), 3),
                "n": int(len(d)),
            })

    # Heatmap: hour × day
    if "ts" in df.columns and not df["ts"].isna().all():
        df2 = df.copy()
        df2["hour"] = df2["ts"].dt.hour
        df2["dow"]  = df2["ts"].dt.day_name()
        hm = df2.pivot_table(index="hour", columns="dow", values="down_mbps", aggfunc="median")
        for h in sorted(hm.index):
            for d_name in hm.columns:
                v = hm.loc[h, d_name]
                if pd.notna(v):
                    result["heatmap"].append({"hour": int(h), "day": str(d_name), "value": round(float(v), 3)})

    # Session trend (is speed degrading within a session?)
    if "ts" in df.columns and len(df) > 20:
        df_s = df.sort_values("ts").copy()
        df_s["seq"] = range(len(df_s))
        window = min(20, len(df_s) // 4)
        rolling = df_s["down_mbps"].rolling(window, min_periods=3).median()
        result["session_trend"] = [
            {"seq": int(i), "down": round(float(v), 3)}
            for i, v in zip(df_s["seq"].values[-60:], rolling.values[-60:])
            if pd.notna(v)
        ]
        # detect degradation: last 10 much worse than first 10
        if len(df_s) > 30:
            early = df_s["down_mbps"].iloc[:10].median()
            late  = df_s["down_mbps"].iloc[-10:].median()
            if early > 0.05 and late < early * 0.5:
                result["alerts"].append({"level": "warning", "title": "Speed degrading over time",
                    "detail": f"Early: {early:.2f} Mbps → recent: {late:.2f} Mbps. Could be session-based shaping."})

    if not result["alerts"]:
        result["alerts"].append({"level": "success", "title": "No throttling detected yet",
            "detail": "Keep logging. Switch VPN and hotspot to generate contrasting conditions."})

    return result
