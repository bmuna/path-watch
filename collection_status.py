#!/usr/bin/env python3
"""Print contrast / CSV status for Path Watch collection."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
MIN_SPEED_ROWS = 500  # ~17 min at 2s


def speed_df() -> pd.DataFrame:
    p = ROOT / "speed_log.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    for c in ("down_mbps", "up_mbps"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def traffic_counts() -> dict:
    p = ROOT / "traffic_log.csv"
    if not p.exists():
        return {}
    vpn = Counter()
    conn = Counter()
    label = Counter()
    with open(p, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            vpn[row.get("vpn") or ""] += 1
            conn[row.get("connection") or ""] += 1
            label[row.get("label") or ""] += 1
    return {"vpn": dict(vpn), "connection": dict(conn), "label": dict(label), "n": sum(vpn.values())}


def metrics_summary() -> dict:
    p = ROOT / "metrics_log.csv"
    if not p.exists():
        return {"exists": False}
    df = pd.read_csv(p)
    out = {"exists": True, "rows": len(df)}
    if "label" in df.columns:
        out["labels"] = df["label"].value_counts().to_dict()
    return out


def main():
    df = speed_df()
    print("=== speed_log.csv ===")
    print(f"rows: {len(df)}")
    if not df.empty:
        print("labels:", df["label"].value_counts().to_dict() if "label" in df.columns else {})
        print("vpn:", df["vpn"].value_counts().to_dict() if "vpn" in df.columns else {})
        print("connection:", df["connection"].value_counts().to_dict() if "connection" in df.columns else {})
        print("tod:", df["tod"].value_counts().to_dict() if "tod" in df.columns else {})
        vpn_n = int((df["vpn"] == "vpn").sum()) if "vpn" in df.columns else 0
        hot_n = int((df["connection"] == "hotspot").sum()) if "connection" in df.columns else 0
        print(f"vpn rows: {vpn_n}  (need >= {MIN_SPEED_ROWS} for ~20–30 min)")
        print(f"hotspot rows: {hot_n}  (need >= {MIN_SPEED_ROWS})")
        print(f"vpn_ready: {vpn_n >= MIN_SPEED_ROWS}")
        print(f"hotspot_ready: {hot_n >= MIN_SPEED_ROWS}")

    print("\n=== traffic_log.csv ===")
    t = traffic_counts()
    print(t)

    print("\n=== metrics_log.csv (legacy ping) ===")
    print(metrics_summary())


if __name__ == "__main__":
    main()
