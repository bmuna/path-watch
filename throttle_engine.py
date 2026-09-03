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

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import config as cfg

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
    try:
        df = pd.read_csv(TRAFFIC_CSV)
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────

def score_current(current_down: float, current_up: float, label: str) -> dict:
    """
    Score the current moment vs the historical baseline for this label.
    Returns a 0-100 throttle score plus a text reason.
    """
    df = _read_speed()
    if df.empty or len(df) < 10:
        return {"score": 0, "reason": "Not enough history yet", "baseline_down": None}

    # baseline: all rows with the same connection+tod (ignoring vpn for baseline)
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
    p10 = float(base.quantile(0.10))
    p90 = float(base.quantile(0.90))

    if med < 0.001:
        return {"score": 0, "reason": "Baseline too small to score", "baseline_down": med}

    ratio = current_down / med if med > 0 else 1.0
    if ratio >= 0.8:
        score = 0
        reason = f"Normal — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    elif ratio >= 0.5:
        score = int((1 - ratio) / 0.3 * 40)
        reason = f"Slightly slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    elif ratio >= 0.2:
        score = 40 + int((0.5 - ratio) / 0.3 * 40)
        reason = f"Noticeably slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"
    else:
        score = min(100, 80 + int((0.2 - ratio) / 0.2 * 20))
        reason = f"Very slow — {current_down:.2f} Mbps vs baseline {med:.2f} Mbps"

    return {
        "score": score,
        "reason": reason,
        "baseline_down": round(med, 3),
        "baseline_p10": round(p10, 3),
        "baseline_p90": round(p90, 3),
        "current_down": round(current_down, 3),
        "ratio": round(ratio, 3),
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
    }

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

    # Traffic geo analysis
    if not traffic.empty and "geo_lat" in traffic.columns:
        geo_rows = traffic.dropna(subset=["geo_lat", "geo_lon"])
        if not geo_rows.empty:
            # map points: unique destination IPs with their geo
            by_ip = geo_rows.groupby("remote_ip").agg(
                lat=("geo_lat", "first"),
                lon=("geo_lon", "first"),
                country=("geo_country", "first"),
                city=("geo_city", "first"),
                isp=("geo_isp", "first"),
                connections=("remote_ip", "count"),
                processes=("process", lambda x: ", ".join(x.dropna().unique()[:3])),
            ).reset_index()
            result["map_points"] = by_ip.where(pd.notna(by_ip), None).to_dict("records")

            # ISP analysis
            if "geo_isp" in traffic.columns:
                by_isp = (traffic.dropna(subset=["geo_isp"])
                    .groupby("geo_isp")
                    .agg(connections=("remote_ip", "count"),
                         countries=("geo_country", lambda x: ", ".join(x.dropna().unique()[:3])))
                    .reset_index()
                    .sort_values("connections", ascending=False)
                    .head(12))
                result["isp_analysis"] = by_isp.to_dict("records")

    if not result["alerts"]:
        result["alerts"].append({"level": "success", "title": "No throttling detected yet",
            "detail": "Keep logging. Switch VPN and hotspot to generate contrasting conditions."})

    return result
