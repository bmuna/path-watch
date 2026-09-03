#!/usr/bin/env python3
# streamlit run dashboard.py
#
# Live view of what this PC is doing on the network + the metrics log.

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import config as cfg
from analyze_metrics import load_df, ping_rows, suspect_vs_baseline, train_baseline, vpn_gap_shrink
from network_status import snapshot

ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / ".live_collector.pid"

st.set_page_config(page_title="path watch", layout="wide")


@st.cache_data(ttl=8)
def get_df(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return load_df(path)


def csv_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def collector_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def start_collector(csv_path: str, interval: float = 12.0) -> str:
    if collector_running():
        return "already running"
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    log = open(ROOT / ".live_collector.log", "a")
    proc = subprocess.Popen(
        [str(py), str(ROOT / "collect_metrics.py"), "--auto", "--watch", "--interval", str(interval), "--csv", csv_path],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    return f"started pid {proc.pid}"


def stop_collector() -> str:
    if not PID_FILE.exists():
        return "not running"
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except Exception as e:
        PID_FILE.unlink(missing_ok=True)
        return f"cleared ({e})"
    PID_FILE.unlink(missing_ok=True)
    return "stopped"


def dest_name(host: str) -> str:
    return cfg.TARGET_META.get(host, {}).get("label", host)


def probe_once(csv_path: str) -> str:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    r = subprocess.run(
        [str(py), str(ROOT / "collect_metrics.py"), "--auto", "--once", "--csv", csv_path],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        return (r.stderr or r.stdout or "probe failed")[-400:]
    return "ok"


# --- sidebar -----------------------------------------------------------------
st.sidebar.title("path watch")
csv_path = st.sidebar.text_input("data file", cfg.CSV_FILE)
auto_refresh = st.sidebar.toggle("auto-refresh", value=True)
st.sidebar.caption("refreshes about every 8s while open")

page = st.sidebar.radio("page", ["live", "history", "learn", "notes"])

mtime = csv_mtime(csv_path)
df = get_df(csv_path, mtime)
ping = ping_rows(df) if not df.empty else pd.DataFrame()

# detect now (always)
with st.spinner("checking this PC…"):
    now = snapshot()

# --- LIVE --------------------------------------------------------------------
if page == "live":
    st.title("What's happening now")
    st.caption("Auto-detects wifi/hotspot, VPN, and time of day on this machine, then logs timing to the CSV.")

    a, b, c, d = st.columns(4)
    a.metric("Connection", now["connection"])
    b.metric("VPN", "ON" if now["vpn"] == "vpn" else "OFF")
    c.metric("Time of day", now["tod"])
    d.metric("Public IP", now.get("public_ip") or "—")

    st.write(
        f"**Label this round would use:** `{now['label']}`  ·  "
        f"SSID `{now.get('ssid') or '—'}`  ·  iface `{now.get('iface') or '—'}`  ·  "
        f"country `{now.get('ip_country') or '—'}`"
    )
    st.caption(f"why connection: {now['connection_reason']}  |  why vpn: {now['vpn_reason']}")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Probe once now", type="primary", use_container_width=True):
            with st.spinner("pinging targets…"):
                msg = probe_once(csv_path)
            st.cache_data.clear()
            if msg == "ok":
                st.success("Saved one round to the log.")
            else:
                st.error(msg)
            time.sleep(0.3)
            st.rerun()
    with c2:
        running = collector_running()
        if running:
            if st.button("Stop live logging", use_container_width=True):
                st.info(stop_collector())
                st.rerun()
        else:
            if st.button("Start live logging", use_container_width=True):
                st.info(start_collector(csv_path))
                st.rerun()
    with c3:
        st.metric("Logger", "RUNNING" if collector_running() else "stopped")

    st.divider()

    if ping.empty:
        st.warning("No measurements yet. Hit **Probe once now** or **Start live logging**.")
    else:
        last_run = ping["run_id"].iloc[-1]
        last_round = int(ping.loc[ping["run_id"] == last_run, "round_idx"].max())
        snap = ping[(ping["run_id"] == last_run) & (ping["round_idx"] == last_round)].copy()
        snap["destination"] = snap["target"].map(dest_name)

        st.subheader("Latest measurements")
        st.caption(
            f"run `{last_run}` · round {last_round} · label `{snap['label'].iloc[0]}` · "
            f"{snap['ts'].iloc[0]}"
        )

        show = snap[["destination", "role", "rtt_avg", "jitter", "loss_pct", "method"]].rename(
            columns={"rtt_avg": "rtt_ms", "jitter": "jitter_ms", "loss_pct": "loss_%"}
        )
        st.dataframe(show, use_container_width=True, hide_index=True)

        m1, m2, m3, m4 = st.columns(4)
        ok = snap.dropna(subset=["rtt_avg"])
        base = ok[ok["role"] == "baseline"]["rtt_avg"]
        sus = ok[ok["role"] == "suspect"]["rtt_avg"]
        m1.metric("Baseline median RTT", f"{base.median():.0f} ms" if len(base) else "—")
        m2.metric("Suspect median RTT", f"{sus.median():.0f} ms" if len(sus) else "—")
        gap = (sus.median() - base.median()) if len(base) and len(sus) else None
        m3.metric("Gap (suspect − DNS)", f"{gap:+.0f} ms" if gap is not None else "—")
        m4.metric("Rows in log", len(df))

        st.subheader("This session")
        one = ping[ping["run_id"] == last_run].copy()
        one["destination"] = one["target"].map(dest_name)
        fig = px.line(
            one.dropna(subset=["rtt_avg"]),
            x="elapsed_s",
            y="rtt_avg",
            color="destination",
            markers=True,
            labels={"elapsed_s": "seconds into run", "rtt_avg": "RTT (ms)"},
        )
        fig.update_layout(height=360, margin=dict(l=20, r=20, t=30, b=20), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

        tp = df[(df["role"] == "throughput") & (df["run_id"] == last_run)].dropna(subset=["throughput_mbps"])
        if len(tp):
            st.metric("Last 1MB download", f"{tp.iloc[-1]['throughput_mbps']} Mbps")

    if auto_refresh:
        time.sleep(8)
        st.rerun()


# --- HISTORY -----------------------------------------------------------------
elif page == "history":
    st.title("History")
    if ping.empty:
        st.info("Nothing logged yet.")
        st.stop()

    ping = ping.copy()
    ping["destination"] = ping["target"].map(dest_name)

    st.subheader("By condition")
    heat = ping.pivot_table(index="destination", columns="label", values="rtt_avg", aggfunc="median")
    if not heat.empty:
        fig = px.imshow(
            heat,
            text_auto=".0f",
            aspect="auto",
            color_continuous_scale="Tealgrn",
            labels=dict(color="ms"),
        )
        fig.update_layout(height=360, margin=dict(l=20, r=20, t=30, b=20))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("RTT over time")
    fig = px.scatter(
        ping.dropna(subset=["rtt_avg", "ts"]),
        x="ts",
        y="rtt_avg",
        color="label",
        symbol="destination",
        labels={"ts": "time", "rtt_avg": "RTT (ms)"},
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=30, b=20), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Sessions")
    summary = (
        ping.groupby(["run_id", "label"], dropna=False)
        .agg(rounds=("round_idx", "nunique"), start=("ts", "min"), median_rtt=("rtt_avg", "median"))
        .reset_index()
        .sort_values("start", ascending=False)
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)


# --- LEARN -------------------------------------------------------------------
elif page == "learn":
    st.title("What the data says")
    st.caption("Same checks as analyze_metrics.py. Needs contrasting runs (vpn on/off, wifi/hotspot, morning/evening).")

    if ping.empty or ping["run_id"].nunique() < 1:
        st.info("Collect more first.")
        st.stop()

    vs = suspect_vs_baseline(df, min_n=5)
    st.subheader("Suspect vs DNS baseline")
    if vs.empty:
        st.write("Not enough samples yet.")
    else:
        st.dataframe(vs, use_container_width=True, hide_index=True)

    st.subheader("Does the gap shrink on VPN?")
    shrink = vpn_gap_shrink(df, min_n=5)
    if shrink.empty:
        st.write("Need matched vpn + no-vpn runs on the same connection and time of day.")
    else:
        st.dataframe(shrink, use_container_width=True, hide_index=True)

    st.subheader("Can a simple model tell wifi from hotspot?")
    res = train_baseline(df, y_col="connection")
    if not res or not res.get("ok"):
        st.write("Skipped:", (res or {}).get("reason", "need more data"))
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("rounds", res["n"])
        c2.metric("majority baseline", f"{res['majority_baseline']:.2f}")
        c3.metric("random forest CV", f"{res['rf_acc_mean']:.2f}")
        st.write("Top features:", res["rf_top"])


# --- NOTES -------------------------------------------------------------------
else:
    st.title("How this works")
    st.markdown(
        f"""
1. **Detect** — this PC's wifi/hotspot, VPN, and time of day (`network_status.py`).
2. **Measure** — ping DNS + YouTube/Facebook/Telegram, plus a 1MB download sample.
3. **Log** — everything goes into `{cfg.CSV_FILE}` with an auto label like `wifi_novpn_evening`.
4. **Learn** — after you have contrasting conditions, the Learn page runs the stats / classifier.

**Live logging** (recommended):

```
.venv/bin/python collect_metrics.py --auto --watch
```

Or use the buttons on the Live page.

VPN detection looks for connected macOS VPN services / `utun` interfaces / common VPN apps.
Hotspot is guessed from the Wi‑Fi name (iPhone, Android, …). If it guesses wrong, run with
manual flags: `--connection hotspot --vpn novpn --tod evening`.

Vantage is set in `config.py` as **{cfg.VANTAGE_CITY}**. Timing only — no payloads, no bypass.
        """
    )
