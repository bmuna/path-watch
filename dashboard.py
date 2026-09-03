#!/usr/bin/env python3
# streamlit run dashboard.py
#
# reads the csv the collector writes. not a live sniffer.

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

import config as cfg
from analyze_metrics import (
    condition_contrast,
    describe_groups,
    gru_data_check,
    load_df,
    ping_rows,
    suspect_vs_baseline,
    train_baseline,
    vpn_gap_shrink,
)

st.set_page_config(page_title="isp timing", layout="wide")


@st.cache_data(ttl=20)
def get_df(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return load_df(path)


def csv_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def fmt_p(p):
    if p is None or pd.isna(p):
        return "—"
    if p < 0.001:
        return f"{p:.1e}"
    return f"{p:.3f}"


st.sidebar.title("log")
csv_path = st.sidebar.text_input("csv", cfg.CSV_FILE)
if st.sidebar.button("reload"):
    st.cache_data.clear()

mtime = csv_mtime(csv_path)
df = get_df(csv_path, mtime)

page = st.sidebar.radio(
    "page",
    ["latest", "time series", "tests", "classifier", "notes"],
)

if df.empty:
    st.warning(
        f"empty csv (`{csv_path}`). run the collector first:\n\n"
        "`python collect_metrics.py --connection wifi --vpn novpn --tod evening`"
    )
    if page != "notes":
        st.stop()

if not df.empty:
    n_runs = df["run_id"].nunique() if "run_id" in df else 0
    st.sidebar.caption(f"{len(df)} rows · {n_runs} runs")
    if mtime:
        st.sidebar.caption("file updated " + datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


if page == "latest":
    st.header("latest round")
    ping = ping_rows(df)
    last_run = ping["run_id"].iloc[-1]
    last_round = ping.loc[ping["run_id"] == last_run, "round_idx"].max()
    snap = ping[(ping["run_id"] == last_run) & (ping["round_idx"] == last_round)]
    label = snap["label"].iloc[0] if len(snap) else "?"
    st.caption(f"run `{last_run}` · round {int(last_round)} · {label}")

    show = snap[["target", "role", "rtt_avg", "rtt_min", "rtt_max", "jitter", "loss_pct", "method"]].copy()
    st.dataframe(show, use_container_width=True, hide_index=True)

    tp = df[(df["role"] == "throughput") & (df["run_id"] == last_run)]
    if len(tp):
        last_tp = tp.iloc[-1]
        st.metric("last 1MB sample (Mbps)", last_tp.get("throughput_mbps"))

    c1, c2, c3 = st.columns(3)
    ping_ok = ping.dropna(subset=["rtt_avg"])
    with c1:
        st.metric("runs logged", int(n_runs))
    with c2:
        st.metric("median RTT (ms), all ping rows", f"{ping_ok['rtt_avg'].median():.1f}" if len(ping_ok) else "—")
    with c3:
        st.metric("labels", ping["label"].nunique() if "label" in ping else 0)

    st.subheader("runs")
    summary = (
        ping.groupby(["run_id", "label"], dropna=False)
        .agg(rounds=("round_idx", "nunique"), start=("ts", "min"), median_rtt=("rtt_avg", "median"))
        .reset_index()
        .sort_values("start", ascending=False)
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)

    missing = []
    for conn in cfg.CONNECTION_CHOICES:
        for vpn in cfg.VPN_CHOICES:
            for tod in ("morning", "evening"):
                lab = f"{conn}_{vpn}_{tod}"
                if lab not in set(ping["label"].unique()):
                    missing.append(lab)
    if missing:
        st.info("haven't run these yet: " + ", ".join(missing))


elif page == "time series":
    st.header("RTT over time")
    ping = ping_rows(df).dropna(subset=["rtt_avg", "ts"])
    if ping.empty:
        st.write("no RTT values")
        st.stop()

    targets = st.multiselect("targets", sorted(ping["target"].unique()), default=sorted(ping["target"].unique()))
    labels = st.multiselect("labels", sorted(ping["label"].unique()), default=sorted(ping["label"].unique()))
    metric = st.radio("metric", ["rtt_avg", "jitter", "loss_pct"], horizontal=True)
    view = ping[ping["target"].isin(targets) & ping["label"].isin(labels)]

    fig = px.scatter(
        view,
        x="ts",
        y=metric,
        color="label",
        symbol="target",
        hover_data=["run_id", "method", "connection", "vpn", "tod"],
        labels={"ts": "time (UTC)", metric: metric.replace("_", " ") + (" (ms)" if metric != "loss_pct" else " (%)")},
        title=None,
    )
    fig.update_traces(marker=dict(size=7, opacity=0.75))
    fig.update_layout(height=480, legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)

    st.caption("one point = one target in one round. color is the condition.")

    st.subheader("one run")
    runs = ping["run_id"].unique().tolist()
    pick = st.selectbox("run", runs, index=len(runs) - 1)
    one = ping[ping["run_id"] == pick]
    fig2 = px.line(
        one,
        x="elapsed_s",
        y="rtt_avg",
        color="target",
        labels={"elapsed_s": "seconds into run", "rtt_avg": "RTT (ms)"},
    )
    fig2.update_layout(height=360, legend_title_text="")
    st.plotly_chart(fig2, use_container_width=True)

    tp = df[(df["role"] == "throughput") & (df["run_id"] == pick)].dropna(subset=["throughput_mbps"])
    if len(tp):
        fig3 = px.scatter(
            tp,
            x="elapsed_s",
            y="throughput_mbps",
            labels={"elapsed_s": "seconds into run", "throughput_mbps": "Mbps"},
            title="1MB samples, this run",
        )
        fig3.update_layout(height=280)
        st.plotly_chart(fig3, use_container_width=True)


elif page == "tests":
    st.header("comparisons")
    st.caption(
        "mann-whitney U (one-sided: is the suspect slower than the dns baselines) "
        "inside each label. I didn't correct for multiple tests."
    )
    min_n = st.sidebar.number_input("min n", 5, 50, 8)

    vs = suspect_vs_baseline(df, min_n=min_n)
    if vs.empty:
        st.write("not enough rows for the tests yet.")
    else:
        vs = vs.copy()
        vs["rtt_p"] = vs["rtt_p"].map(fmt_p)
        vs["jitter_p"] = vs["jitter_p"].map(fmt_p)
        st.subheader("suspect vs dns baseline")
        st.dataframe(vs, use_container_width=True, hide_index=True)

    shrink = vpn_gap_shrink(df, min_n=min_n)
    st.subheader("vpn on vs off")
    st.caption(
        "gap = median suspect rtt minus median baseline rtt. "
        "`signature` is just a flag: off-vpn gap has p<0.05 and shrinks by >10ms (or 20%) with vpn. "
        "not the same as proving anyone targeted a service."
    )
    if shrink.empty:
        st.write("need a vpn run and a no-vpn run on the same connection + time of day.")
    else:
        st.dataframe(shrink, use_container_width=True, hide_index=True)

    con = condition_contrast(df, min_n=min_n)
    st.subheader("other contrasts")
    if con.empty:
        st.write("need both sides (wifi and hotspot, or vpn and not, or morning and evening).")
    else:
        con = con.copy()
        con["p"] = con["p"].map(fmt_p)
        st.dataframe(con, use_container_width=True, hide_index=True)

    st.subheader("group means")
    desc = describe_groups(df)
    st.dataframe(desc, use_container_width=True, hide_index=True)


elif page == "classifier":
    st.header("classifier")
    st.caption(
        "logreg and a random forest predicting wifi/hotspot (or vpn, or time of day) "
        "from rtt/jitter/loss. just checking if there's any signal. "
        "cv accuracy vs majority class. majority class is the thing to beat."
    )
    y_col = st.selectbox("predict", ["connection", "vpn", "tod", "label"])
    res = train_baseline(df, y_col=y_col)
    if not res or not res.get("ok"):
        st.write("skipped:", (res or {}).get("reason", "not enough data"))
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("n (rounds)", res["n"])
        c2.metric("majority class", f"{res['majority_baseline']:.2f}")
        c3.metric("logreg CV acc", f"{res['logreg_acc_mean']:.2f}")
        c4.metric("random forest CV acc", f"{res['rf_acc_mean']:.2f}")
        st.write("classes:", res["classes"])

        left, right = st.columns(2)
        with left:
            st.subheader("logreg |coef|")
            st.dataframe(
                pd.Series(res["logreg_top"], name="abs_coef").rename_axis("feature").reset_index(),
                hide_index=True,
                use_container_width=True,
            )
        with right:
            st.subheader("rf importance")
            st.dataframe(
                pd.Series(res["rf_top"], name="importance").rename_axis("feature").reset_index(),
                hide_index=True,
                use_container_width=True,
            )
        st.text(res["report"])

        if res["rf_acc_mean"] <= res["majority_baseline"] + 0.03:
            st.info(
                "forest is basically guessing the majority class. either the timing "
                "doesn't separate these conditions, or I need more runs."
            )

    gc = gru_data_check(df)
    st.subheader("sequence model")
    st.write(gc)
    if not gc["justified"]:
        st.write(
            "not training a gru on this csv, runs aren't long enough for "
            "onset-during-session to be a real thing to model."
        )


elif page == "notes":
    st.header("notes")
    st.markdown(
        """
One home network. ICMP (or TCP connect if ping is blocked) to a few hosts,
plus a 1MB download sample, tagged with wifi/hotspot, vpn or not, time of day.

Not looking at payloads. Not trying to get around anything. Also can't show
that an ISP *meant* to slow something down, only that some destinations
look different from 1.1.1.1 / 8.8.8.8 under some conditions.

What I actually care about:

1. Under a given condition, is youtube/facebook/telegram slower than the dns boxes?
2. Does that gap go away (or get a lot smaller) when I turn a vpn on?

A gap that's specific to a service *and* disappears on vpn is the usual
circumstantial thing people point at. Still not proof. Vpn also changes
the path and dns. I'm treating it as something that needs more runs.

I read OONI's throttling work (Kazakhstan 2023, Russia 2022, Türkiye 2023)
before writing this. They compare potentially targeted services against a
baseline using TLS/download timing. This is a much smaller version of that
idea, using ping because I can leave it running for half an hour.

A handful of 25-minute sessions from one apartment is a pilot. Don't overread
a p-value from 200 pings on a Tuesday.

Pinging youtube.com is not watching youtube. Download-shaped throttling
(speed drops after a few MB, or tethering gets a different bucket) often
won't show up in RTT. That's why the 1MB sample is there; it's still a weak
stand-in. Also youtube.com is not googlevideo.com.

Several targets × several labels. I didn't Bonferroni anything. If one
p-value is 0.04 that is not a finding by itself.

GRU only makes sense if something changes *during* a session. If the csv
is independent rounds, a classifier on the aggregates is enough.
        """
    )
