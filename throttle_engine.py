#!/usr/bin/env python3
"""
Throttling detection engine.

Learns from speed_log.csv + traffic_log.csv. Scores destinations, times,
and conditions. Detects anomalies — drops, shaping, VPN-vs-direct gaps.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


def _read(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        for col in ("down_mbps", "up_mbps"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def _family(host: str) -> str:
    h = str(host).lower()
    if any(x in h for x in ("1e100.net", "google", "youtube", "ytimg", "ggpht", "gstatic")):
        return "Google / YouTube"
    if any(x in h for x in ("facebook", "fbcdn", "instagram", "whatsapp", "meta")):
        return "Meta (Facebook)"
    if any(x in h for x in ("telegram", "149.154.", "91.108.", "t.me")):
        return "Telegram"
    if any(x in h for x in ("cloudflare", "104.18.", "104.16.")):
        return "Cloudflare"
    if "github" in h or "githubusercontent" in h:
        return "GitHub"
    if any(x in h for x in ("amazonaws", "aws", "ec2-")):
        return "AWS"
    if any(x in h for x in ("apple.com", "icloud", "mzstatic")):
        return "Apple"
    if any(x in h for x in ("microsoft", "azure", "office", "live.com")):
        return "Microsoft"
    return "Other"


def analyze(speed_path, traffic_path) -> dict:
    speed = _read(speed_path)
    traffic = _read(traffic_path)
    result = {
        "status": "ok",
        "samples": 0,
        "speed": {},
        "conditions": [],
        "heatmap": [],
        "destinations": [],
        "alerts": [],
        "vpn_comparison": None,
        "time_comparison": [],
        "connection_comparison": None,
    }

    if speed.empty:
        result["status"] = "no_data"
        result["alerts"].append({
            "level": "info",
            "title": "No data yet",
            "detail": "Leave the app open and browse normally. Speed and connections log every few seconds."
        })
        return result

    down = speed["down_mbps"].dropna()
    up = speed["up_mbps"].dropna()
    result["samples"] = int(len(speed))

    result["speed"] = {
        "down_median": round(float(down.median()), 3) if len(down) else 0,
        "down_mean": round(float(down.mean()), 3) if len(down) else 0,
        "down_peak": round(float(down.max()), 3) if len(down) else 0,
        "down_p95": round(float(down.quantile(0.95)), 3) if len(down) else 0,
        "down_p5": round(float(down.quantile(0.05)), 3) if len(down) else 0,
        "up_median": round(float(up.median()), 3) if len(up) else 0,
        "up_peak": round(float(up.max()), 3) if len(up) else 0,
    }

    # bursty / shaped detection
    if len(down) > 10:
        med = down.median()
        peak = down.max()
        p5 = down.quantile(0.05)
        if peak > med * 3 and med > 0.05:
            result["alerts"].append({
                "level": "warning",
                "title": "Bursty download pattern",
                "detail": f"Peak {peak:.2f} Mbps vs median {med:.2f} Mbps. Link may be shaped or congested."
            })
        if p5 < med * 0.1 and med > 0.1:
            result["alerts"].append({
                "level": "warning",
                "title": "Frequent speed drops",
                "detail": f"Bottom 5% is {p5:.3f} Mbps while median is {med:.2f}. Possible throttling or interference."
            })
        if med < 0.5 and peak < 2.0:
            result["alerts"].append({
                "level": "warning",
                "title": "Consistently slow",
                "detail": f"Median {med:.2f} Mbps, peak {peak:.2f}. May be a slow plan, congested cell, or throttled."
            })

    # VPN comparison
    if "vpn" in speed.columns and speed["vpn"].nunique() > 1:
        groups = {}
        for vpn_val, g in speed.groupby("vpn"):
            groups[vpn_val] = g["down_mbps"].dropna()
        if "vpn" in groups and "novpn" in groups and len(groups["vpn"]) >= 5 and len(groups["novpn"]) >= 5:
            on = groups["vpn"]
            off = groups["novpn"]
            stat_result = stats.mannwhitneyu(off.values, on.values, alternative="two-sided")
            result["vpn_comparison"] = {
                "vpn_on_median": round(float(on.median()), 3),
                "vpn_off_median": round(float(off.median()), 3),
                "vpn_on_n": int(len(on)),
                "vpn_off_n": int(len(off)),
                "p_value": round(float(stat_result.pvalue), 4),
                "significant": bool(stat_result.pvalue < 0.05),
            }
            diff = off.median() - on.median()
            if stat_result.pvalue < 0.05 and diff < -on.median() * 0.2:
                result["alerts"].append({
                    "level": "danger",
                    "title": "Faster with VPN",
                    "detail": f"VPN on: {on.median():.2f} Mbps vs off: {off.median():.2f} Mbps (p={stat_result.pvalue:.3f}). "
                              "Traffic may be treated differently without VPN."
                })
            elif stat_result.pvalue < 0.05 and diff > on.median() * 0.2:
                result["alerts"].append({
                    "level": "info",
                    "title": "Slower with VPN",
                    "detail": f"VPN adds overhead. Off: {off.median():.2f} vs on: {on.median():.2f} Mbps. Normal if VPN server is distant."
                })
    else:
        result["alerts"].append({
            "level": "info",
            "title": "Need VPN contrast",
            "detail": "Turn VPN on/off while the app runs to detect differential treatment."
        })

    # Time of day comparison
    if "tod" in speed.columns and speed["tod"].nunique() > 1:
        for tod, g in speed.groupby("tod"):
            d = g["down_mbps"].dropna()
            if len(d) < 3:
                continue
            result["time_comparison"].append({
                "tod": str(tod),
                "median": round(float(d.median()), 3),
                "mean": round(float(d.mean()), 3),
                "p95": round(float(d.quantile(0.95)), 3),
                "p5": round(float(d.quantile(0.05)), 3),
                "n": int(len(d)),
            })
        if len(result["time_comparison"]) >= 2:
            meds = [t["median"] for t in result["time_comparison"]]
            if max(meds) > min(meds) * 2 and min(meds) > 0.01:
                slow = min(result["time_comparison"], key=lambda x: x["median"])
                fast = max(result["time_comparison"], key=lambda x: x["median"])
                result["alerts"].append({
                    "level": "warning",
                    "title": f"Slower at {slow['tod']}",
                    "detail": f"{slow['tod']}: {slow['median']:.2f} Mbps vs {fast['tod']}: {fast['median']:.2f} Mbps. "
                              "Could be congestion or time-based shaping."
                })

    # Connection type
    if "connection" in speed.columns and speed["connection"].nunique() > 1:
        conn_data = {}
        for conn, g in speed.groupby("connection"):
            d = g["down_mbps"].dropna()
            conn_data[conn] = {"median": round(float(d.median()), 3), "n": int(len(d))}
        result["connection_comparison"] = conn_data

    # Heatmap: condition × metric
    conditions = []
    if "label" in speed.columns:
        for label, g in speed.groupby("label"):
            d = g["down_mbps"].dropna()
            u = g["up_mbps"].dropna()
            if len(d) < 2:
                continue
            conditions.append({
                "label": str(label),
                "down_median": round(float(d.median()), 3),
                "up_median": round(float(u.median()), 3),
                "down_p5": round(float(d.quantile(0.05)), 3),
                "n": int(len(d)),
            })
    result["conditions"] = conditions

    # Heatmap data: hour × day-of-week
    if "ts" in speed.columns and not speed["ts"].isna().all():
        speed = speed.copy()
        speed["hour"] = speed["ts"].dt.hour
        speed["dow"] = speed["ts"].dt.day_name()
        hm = speed.pivot_table(index="hour", columns="dow", values="down_mbps", aggfunc="median")
        heatmap = []
        for hour in sorted(hm.index):
            for dow in hm.columns:
                val = hm.loc[hour, dow]
                if pd.notna(val):
                    heatmap.append({"hour": int(hour), "day": dow, "value": round(float(val), 3)})
        result["heatmap"] = heatmap

    # Destinations from traffic log
    if not traffic.empty:
        traffic = traffic.copy()
        host_col = traffic["hostname"].fillna("").astype(str)
        host_col = host_col.where(host_col.str.len() > 0, traffic["remote_ip"].astype(str))
        traffic["host_label"] = host_col
        traffic["family"] = traffic["host_label"].map(_family)

        by_family = traffic.groupby("family").agg(
            connections=("family", "size"),
        ).reset_index().sort_values("connections", ascending=False)
        result["destinations"] = by_family.to_dict("records")

        by_app = traffic.groupby("process").size().sort_values(ascending=False).head(10)
        result["top_apps"] = [{"app": k, "connections": int(v)} for k, v in by_app.items()]

    if not result["alerts"]:
        result["alerts"].append({
            "level": "success",
            "title": "Looking normal so far",
            "detail": "No obvious throttling patterns yet. Keep logging across different conditions."
        })

    return result
