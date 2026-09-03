#!/usr/bin/env python3
"""Throttling hints from speed_log + traffic_log."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _host_label(row) -> str:
    h = str(row.get("hostname") or "").strip()
    if h:
        return h
    return str(row.get("remote_ip") or "")


def _family(host: str) -> str | None:
    h = host.lower()
    if any(x in h for x in ("1e100.net", "google", "youtube", "ytimg", "ggpht", "gstatic")):
        return "Google / YouTube"
    if any(x in h for x in ("facebook", "fbcdn", "instagram", "whatsapp", "meta")):
        return "Meta"
    if any(x in h for x in ("telegram", "149.154.", "91.108.", "t.me")):
        return "Telegram"
    if "cloudflare" in h or h.startswith("104.18.") or h.startswith("104.16."):
        return "Cloudflare / CDN"
    if "github" in h or "githubusercontent" in h:
        return "GitHub"
    if "amazonaws" in h or "aws" in h:
        return "AWS"
    return None


def throttling_report(traffic_path: Path, speed_path: Path) -> str:
    traffic = _read(Path(traffic_path))
    speed = _read(Path(speed_path))
    lines: list[str] = []

    if speed.empty and traffic.empty:
        return (
            "No data yet.\n\n"
            "Leave Path Watch open and browse, download, or use Telegram. "
            "Speed and connections log automatically every few seconds."
        )

    # --- speed section ---
    if not speed.empty:
        for col in ("down_mbps", "up_mbps"):
            if col in speed.columns:
                speed[col] = pd.to_numeric(speed[col], errors="coerce")
        down = speed["down_mbps"].dropna()
        up = speed["up_mbps"].dropna()

        lines.append("LINK SPEED (this machine)")
        if len(down):
            med = down.median()
            peak = down.max()
            p95 = down.quantile(0.95)
            lines.append(f"  Download  now-ish median {med:.2f} Mbps")
            lines.append(f"            peak {peak:.2f} · 95th % {p95:.2f} Mbps")
            if peak > med * 2.5 and med > 0.05:
                lines.append("  → big spikes vs median: link may be bursty or shaped")
            elif peak < 1.0 and med < 0.5:
                lines.append("  → sustained low speeds right now (wifi or cell cap?)")
        if len(up):
            lines.append(f"  Upload    median {up.median():.2f} Mbps · peak {up.max():.2f} Mbps")

        labels = set(speed["label"].dropna()) if "label" in speed.columns else set()
        lines.append("")
        lines.append("WHAT WE CAN COMPARE")
        has_vpn = "vpn" in speed.columns and speed["vpn"].nunique() > 1
        has_conn = "connection" in speed.columns and speed["connection"].nunique() > 1
        has_tod = "tod" in speed.columns and speed["tod"].nunique() > 1

        if has_vpn:
            lines.append("  VPN on vs off:")
            for vpn, g in speed.groupby("vpn"):
                lines.append(f"    {vpn:6}  down median {g['down_mbps'].median():.2f} Mbps")
            off = speed[speed["vpn"] == "novpn"]["down_mbps"].median()
            on = speed[speed["vpn"] == "vpn"]["down_mbps"].median()
            if pd.notna(off) and pd.notna(on) and off > on * 1.25:
                lines.append("  → download slower WITH vpn (unusual — check vpn server)")
            elif pd.notna(off) and pd.notna(on) and on > off * 1.25:
                lines.append("  → download faster with vpn (path may be treated differently off-vpn)")
        else:
            lines.append("  VPN: only one state logged — turn VPN on/off while app runs")

        if has_conn:
            lines.append("  Wifi vs hotspot:")
            for conn, g in speed.groupby("connection"):
                lines.append(f"    {conn:8}  down median {g['down_mbps'].median():.2f} Mbps")
        else:
            lines.append("  Link: only one type — try a phone hotspot session too")

        if has_tod:
            lines.append("  Time of day:")
            for tod, g in speed.groupby("tod"):
                lines.append(f"    {tod:10}  down median {g['down_mbps'].median():.2f} Mbps")
        else:
            lines.append("  Time: only one bucket so far — keep app open into evening")

        if len(labels) == 1:
            lines.append(f"\n  Logged under one condition only: {list(labels)[0]}")
            lines.append("  Throttling proof needs contrast (vpn on/off, hotspot, different times).")

    # --- destinations ---
    if not traffic.empty:
        traffic = traffic.copy()
        traffic["host"] = traffic.apply(_host_label, axis=1)
        lines.append("")
        lines.append("WHERE YOUR APPS CONNECTED")
        by_app = traffic.groupby("process").size().sort_values(ascending=False).head(8)
        for app, n in by_app.items():
            lines.append(f"  {app:16}  {n} new connections")

        fam_counts: dict[str, int] = {}
        for host in traffic["host"]:
            fam = _family(host)
            if fam:
                fam_counts[fam] = fam_counts.get(fam, 0) + 1
        if fam_counts:
            lines.append("")
            lines.append("  By service (from hostnames / IPs):")
            for fam, n in sorted(fam_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    {fam:20}  {n} connects")

    lines.append("")
    lines.append("This is timing + volume metadata only. It does not prove ISP intent.")
    return "\n".join(lines)


def summarize(traffic_path: Path, speed_path: Path) -> str:
    return throttling_report(traffic_path, speed_path)


if __name__ == "__main__":
    import config as cfg

    print(throttling_report(cfg.TRAFFIC_CSV, cfg.SPEED_CSV))
