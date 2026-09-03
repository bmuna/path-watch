#!/usr/bin/env python3
"""
stats + a simple classifier on metrics_log.csv

    python analyze_metrics.py
    python analyze_metrics.py --save
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config as cfg

warnings.filterwarnings("ignore", category=UserWarning)


def load_df(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    for col in ("rtt_avg", "rtt_min", "rtt_max", "jitter", "loss_pct", "throughput_mbps", "elapsed_s"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def ping_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["role"].isin(["baseline", "suspect"])].copy()


def describe_groups(df: pd.DataFrame) -> pd.DataFrame:
    g = ping_rows(df)
    if g.empty:
        return g
    out = (
        g.groupby(["label", "target", "role"], dropna=False)[["rtt_avg", "jitter", "loss_pct"]]
        .agg(["mean", "std", "median", "count"])
        .round(3)
    )
    out.columns = ["_".join(c) for c in out.columns.to_flat_index()]
    return out.reset_index()


def _mw(a: np.ndarray, b: np.ndarray, alternative: str = "greater"):
    if len(a) < 5 or len(b) < 5:
        return None
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 5 or len(b) < 5:
        return None
    stat, p = stats.mannwhitneyu(a, b, alternative=alternative)
    return {
        "u_stat": float(stat),
        "p": float(p),
        "median_a": float(np.median(a)),
        "median_b": float(np.median(b)),
        "n_a": int(len(a)),
        "n_b": int(len(b)),
        "median_diff": float(np.median(a) - np.median(b)),
    }


def suspect_vs_baseline(df: pd.DataFrame, min_n: int = 8) -> pd.DataFrame:
    """suspect rtt vs pooled dns baselines, per label."""
    g = ping_rows(df)
    rows = []
    for label, sub in g.groupby("label"):
        base = sub.loc[sub["role"] == "baseline", "rtt_avg"].to_numpy()
        jit_base = sub.loc[sub["role"] == "baseline", "jitter"].to_numpy()
        for tgt, tsub in sub[sub["role"] == "suspect"].groupby("target"):
            rtt = tsub["rtt_avg"].to_numpy()
            jit = tsub["jitter"].to_numpy()
            mw_r = _mw(rtt, base, "greater")
            mw_j = _mw(jit, jit_base, "greater")
            rec = {
                "label": label,
                "target": tgt,
                "n_suspect": int(np.isfinite(rtt).sum()),
                "n_baseline": int(np.isfinite(base).sum()),
            }
            if mw_r and rec["n_suspect"] >= min_n and rec["n_baseline"] >= min_n:
                rec.update(
                    {
                        "rtt_median_suspect": mw_r["median_a"],
                        "rtt_median_baseline": mw_r["median_b"],
                        "rtt_gap_ms": mw_r["median_diff"],
                        "rtt_p": mw_r["p"],
                        "rtt_sig": mw_r["p"] < 0.05,
                    }
                )
            else:
                rec.update(
                    {
                        "rtt_median_suspect": np.nanmedian(rtt) if len(rtt) else np.nan,
                        "rtt_median_baseline": np.nanmedian(base) if len(base) else np.nan,
                        "rtt_gap_ms": np.nan,
                        "rtt_p": np.nan,
                        "rtt_sig": False,
                    }
                )
            if mw_j:
                rec["jitter_gap_ms"] = mw_j["median_diff"]
                rec["jitter_p"] = mw_j["p"]
            else:
                rec["jitter_gap_ms"] = np.nan
                rec["jitter_p"] = np.nan
            rows.append(rec)
    return pd.DataFrame(rows)


def vpn_gap_shrink(df: pd.DataFrame, min_n: int = 8) -> pd.DataFrame:
    # same (suspect - baseline) gap, vpn vs novpn, holding wifi/hotspot + tod.
    # vpn still changes the path so this isn't a controlled experiment.
    cmp_df = suspect_vs_baseline(df, min_n=min_n)
    if cmp_df.empty:
        return cmp_df

    parts = cmp_df["label"].str.split("_", expand=True)
    if parts.shape[1] < 3:
        return pd.DataFrame()
    cmp_df["connection"] = parts[0]
    cmp_df["vpn"] = parts[1]
    cmp_df["tod"] = parts[2]

    rows = []
    keys = cmp_df.groupby(["connection", "tod", "target"])
    for (conn, tod, tgt), sub in keys:
        novpn = sub[sub["vpn"] == "novpn"]
        vpn = sub[sub["vpn"] == "vpn"]
        if novpn.empty or vpn.empty:
            continue
        gap_off = novpn["rtt_gap_ms"].mean()
        gap_on = vpn["rtt_gap_ms"].mean()
        p_off = novpn["rtt_p"].mean()
        p_on = vpn["rtt_p"].mean()
        rows.append(
            {
                "connection": conn,
                "tod": tod,
                "target": tgt,
                "gap_novpn_ms": gap_off,
                "gap_vpn_ms": gap_on,
                "gap_shrink_ms": gap_off - gap_on if pd.notna(gap_off) and pd.notna(gap_on) else np.nan,
                "p_novpn": p_off,
                "p_vpn": p_on,
                "signature": bool(
                    pd.notna(gap_off)
                    and pd.notna(gap_on)
                    and p_off < 0.05
                    and (gap_off - gap_on) > max(10.0, 0.2 * abs(gap_off))
                ),
            }
        )
    return pd.DataFrame(rows)


def condition_contrast(df: pd.DataFrame, min_n: int = 8) -> pd.DataFrame:
    g = ping_rows(df)
    rows = []

    def add(axis, a_val, b_val):
        for tgt, tsub in g.groupby("target"):
            aa = tsub.loc[tsub[axis] == a_val, "rtt_avg"].to_numpy()
            bb = tsub.loc[tsub[axis] == b_val, "rtt_avg"].to_numpy()
            mw = _mw(aa, bb, alternative="two-sided")
            if mw is None or mw["n_a"] < min_n or mw["n_b"] < min_n:
                continue
            rows.append(
                {
                    "axis": axis,
                    "a": a_val,
                    "b": b_val,
                    "target": tgt,
                    "median_a": mw["median_a"],
                    "median_b": mw["median_b"],
                    "diff_ms": mw["median_diff"],
                    "p": mw["p"],
                    "n_a": mw["n_a"],
                    "n_b": mw["n_b"],
                }
            )

    add("connection", "hotspot", "wifi")
    add("vpn", "novpn", "vpn")
    add("tod", "evening", "morning")
    return pd.DataFrame(rows)


def _round_features(df: pd.DataFrame) -> pd.DataFrame:
    g = ping_rows(df)
    if g.empty:
        return g
    idx_cols = ["run_id", "round_idx", "label", "connection", "vpn", "tod"]
    pieces = []
    for metric in ("rtt_avg", "jitter", "loss_pct"):
        piv = g.pivot_table(index=idx_cols, columns="target", values=metric, aggfunc="mean")
        piv.columns = [f"{metric}__{c}" for c in piv.columns]
        pieces.append(piv)
    feat = pd.concat(pieces, axis=1).reset_index()
    return feat


def train_baseline(df: pd.DataFrame, y_col: str = "connection"):
    # default y is a binary axis; predicting the full 8-way label with a
    # handful of runs is a stretch.
    feat = _round_features(df)
    if feat.empty or y_col not in feat.columns:
        return None
    y = feat[y_col].astype(str)
    if y.nunique() < 2:
        return {"ok": False, "reason": f"only one class in {y_col}: {y.unique().tolist()}"}

    X = feat.filter(regex=r"^(rtt_avg|jitter|loss_pct)__").copy()
    X = X.fillna(X.median(numeric_only=True))
    X = X.dropna(axis=1, how="all")
    if X.shape[1] == 0 or len(X) < 20:
        return {"ok": False, "reason": f"not enough rows/features ({len(X)} x {X.shape[1]})"}

    counts = y.value_counts()
    if counts.min() < 8:
        return {"ok": False, "reason": f"class counts too small: {counts.to_dict()}"}

    n_splits = min(5, int(counts.min()))
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=0)

    pipe_lr = Pipeline(
        [
            ("sc", StandardScaler()),
            ("lr", LogisticRegression(max_iter=2000)),
        ]
    )
    rf = RandomForestClassifier(n_estimators=200, random_state=0, min_samples_leaf=3)

    lr_scores = cross_val_score(pipe_lr, X, y, cv=cv, scoring="accuracy")
    rf_scores = cross_val_score(rf, X, y, cv=cv, scoring="accuracy")

    pipe_lr.fit(X, y)
    rf.fit(X, y)

    lr = pipe_lr.named_steps["lr"]
    lr_coef = pd.Series(np.mean(np.abs(lr.coef_), axis=0), index=X.columns).sort_values(ascending=False)
    rf_imp = pd.Series(rf.feature_importances_, index=X.columns).sort_values(ascending=False)

    yhat = cross_val_predict(rf, X, y, cv=cv)
    report = classification_report(y, yhat, zero_division=0)

    maj = float(counts.max() / counts.sum())

    return {
        "ok": True,
        "y_col": y_col,
        "n": int(len(X)),
        "n_features": int(X.shape[1]),
        "classes": counts.to_dict(),
        "majority_baseline": maj,
        "logreg_acc_mean": float(lr_scores.mean()),
        "logreg_acc_std": float(lr_scores.std()),
        "rf_acc_mean": float(rf_scores.mean()),
        "rf_acc_std": float(rf_scores.std()),
        "logreg_top": lr_coef.head(8).round(4).to_dict(),
        "rf_top": rf_imp.head(8).round(4).to_dict(),
        "report": report,
    }


def gru_data_check(df: pd.DataFrame) -> dict:
    g = ping_rows(df)
    if g.empty:
        return {"justified": False, "reason": "no ping rows"}
    lengths = g.groupby("run_id")["round_idx"].nunique()
    n_long = int((lengths >= 40).sum())
    return {
        "justified": bool(n_long >= 4 and lengths.max() >= 40),
        "n_runs": int(len(lengths)),
        "median_rounds": float(lengths.median()) if len(lengths) else 0,
        "max_rounds": int(lengths.max()) if len(lengths) else 0,
        "n_runs_with_40plus": n_long,
        "reason": (
            "enough long sessions to look at onset"
            if n_long >= 4
            else "not enough long sessions, skip the gru"
        ),
    }


def run_all(path: str, min_n: int = 8) -> dict:
    df = load_df(path)
    if df.empty:
        return {"empty": True}

    desc = describe_groups(df)
    vs = suspect_vs_baseline(df, min_n=min_n)
    shrink = vpn_gap_shrink(df, min_n=min_n)
    contrast = condition_contrast(df, min_n=min_n)

    models = {}
    for y_col in ("connection", "vpn", "tod", "label"):
        models[y_col] = train_baseline(df, y_col=y_col)

    return {
        "empty": False,
        "n_rows": int(len(df)),
        "n_runs": int(df["run_id"].nunique()) if "run_id" in df else 0,
        "labels": sorted(df["label"].dropna().unique().tolist()) if "label" in df else [],
        "describe": desc,
        "suspect_vs_baseline": vs,
        "vpn_shrink": shrink,
        "contrast": contrast,
        "models": models,
        "gru_check": gru_data_check(df),
    }


def _print_df(title, d):
    print("\n" + title)
    print("-" * len(title))
    if d is None or (isinstance(d, pd.DataFrame) and d.empty):
        print("  (nothing to show yet)")
        return
    with pd.option_context("display.max_columns", 20, "display.width", 140, "display.max_rows", 80):
        print(d.to_string(index=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=cfg.CSV_FILE)
    p.add_argument("--min-n", type=int, default=8, help="min samples per group for the MW tests")
    p.add_argument("--save", action="store_true", help="write mw_results.csv and clf_report.txt")
    args = p.parse_args()

    try:
        out = run_all(args.csv, min_n=args.min_n)
    except FileNotFoundError:
        print(f"no csv at {args.csv}, run collect_metrics.py first")
        return 1

    if out.get("empty"):
        print("csv is empty")
        return 1

    print(f"rows={out['n_rows']}  runs={out['n_runs']}")
    print("labels:", ", ".join(out["labels"]) or "(none)")
    _print_df("medians / means", out["describe"])
    _print_df("suspect vs baseline (mann-whitney, one-sided)", out["suspect_vs_baseline"])
    _print_df("vpn on vs off (same connection + tod)", out["vpn_shrink"])
    _print_df("other contrasts", out["contrast"])

    print("\nbaseline classifiers")
    print("--------------------")
    report_bits = []
    for y_col, res in out["models"].items():
        print(f"\n[{y_col}]")
        if not res or not res.get("ok"):
            reason = (res or {}).get("reason", "skipped")
            print(f"  skipped: {reason}")
            report_bits.append(f"[{y_col}] skipped: {reason}\n")
            continue
        print(
            f"  n={res['n']}  majority={res['majority_baseline']:.3f}  "
            f"logreg={res['logreg_acc_mean']:.3f}±{res['logreg_acc_std']:.3f}  "
            f"rf={res['rf_acc_mean']:.3f}±{res['rf_acc_std']:.3f}"
        )
        print("  rf top features:", res["rf_top"])
        print(res["report"])
        report_bits.append(
            f"[{y_col}] n={res['n']} maj={res['majority_baseline']:.3f} "
            f"lr={res['logreg_acc_mean']:.3f} rf={res['rf_acc_mean']:.3f}\n{res['report']}\n"
        )

    print("\ngru?")
    print("----")
    gc = out["gru_check"]
    print(json.dumps(gc, indent=2))
    if not gc["justified"]:
        print("not training a gru")

    if args.save:
        if not out["suspect_vs_baseline"].empty:
            out["suspect_vs_baseline"].to_csv("mw_results.csv", index=False)
        with open("clf_report.txt", "w") as f:
            f.write("\n".join(report_bits))
            f.write("\ngru check: ")
            f.write(json.dumps(gc))
        print("\nwrote mw_results.csv and clf_report.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
