#!/usr/bin/env python3
"""
sequence model over a window of rtts.

only useful if throttling kicks in after you've been downloading for a bit.
if the csv is short / independent snapshots, skip this. analyze_metrics
already has a check.

    python model_gru.py
    python model_gru.py --force
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config as cfg
from analyze_metrics import gru_data_check, load_df, ping_rows


WINDOW = 20
MIN_ROUNDS = 40


def build_sequences(df: pd.DataFrame, y_col: str = "connection"):
    g = ping_rows(df)
    feat_cols = None
    Xs, ys, run_ids = [], [], []

    for run_id, sub in g.groupby("run_id"):
        piv = (
            sub.sort_values("round_idx")
            .pivot_table(index="round_idx", columns="target", values="rtt_avg", aggfunc="mean")
            .sort_index()
        )
        if piv.shape[0] < MIN_ROUNDS:
            continue
        piv = piv.interpolate(limit_direction="both").fillna(piv.median())
        if feat_cols is None:
            feat_cols = list(piv.columns)
        else:
            for c in feat_cols:
                if c not in piv.columns:
                    piv[c] = np.nan
            piv = piv[feat_cols].interpolate(limit_direction="both").fillna(0)

        y = sub[y_col].iloc[0]
        arr = piv.to_numpy(dtype=np.float32)
        # sliding windows along the session
        for i in range(0, len(arr) - WINDOW + 1, 5):
            Xs.append(arr[i : i + WINDOW])
            ys.append(y)
            run_ids.append(run_id)

    if not Xs:
        return None
    X = np.stack(Xs)
    y = np.array(ys)
    return X, y, np.array(run_ids), feat_cols


def train_gru(X, y, run_ids, epochs=25, lr=1e-3):
    try:
        import torch
        import torch.nn as nn
        from sklearn.preprocessing import LabelEncoder
    except ImportError:
        print("need pytorch: pip install torch")
        return None

    enc = LabelEncoder()
    y_i = enc.fit_transform(y)
    n_classes = len(enc.classes_)
    if n_classes < 2:
        print("only one class, nothing to train")
        return None

    # split on run_id, not on windows
    uniq = np.unique(run_ids)
    rng = np.random.default_rng(0)
    rng.shuffle(uniq)
    n_val = max(1, len(uniq) // 4)
    val_runs = set(uniq[:n_val])
    train_m = np.array([r not in val_runs for r in run_ids])
    val_m = ~train_m
    if train_m.sum() == 0 or val_m.sum() == 0:
        print("not enough distinct runs to split")
        return None

    Xt = torch.tensor(X[train_m])
    yt = torch.tensor(y_i[train_m], dtype=torch.long)
    Xv = torch.tensor(X[val_m])
    yv = torch.tensor(y_i[val_m], dtype=torch.long)

    class TinyGRU(nn.Module):
        def __init__(self, n_feat, n_hidden=32):
            super().__init__()
            self.gru = nn.GRU(n_feat, n_hidden, batch_first=True)
            self.fc = nn.Linear(n_hidden, n_classes)

        def forward(self, x):
            # (batch, time, features)
            _, h = self.gru(x)
            return self.fc(h[-1])

    model = TinyGRU(X.shape[-1])
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss()

    model.train()
    for ep in range(epochs):
        opt.zero_grad()
        logits = model(Xt)
        loss = loss_fn(logits, yt)
        loss.backward()
        opt.step()
        if ep % 5 == 0 or ep == epochs - 1:
            model.eval()
            with torch.no_grad():
                pred = model(Xv).argmax(1)
                acc = (pred == yv).float().mean().item()
            print(f"  epoch {ep:3d}  loss={loss.item():.4f}  val_acc={acc:.3f}")
            model.train()

    model.eval()
    with torch.no_grad():
        val_acc = (model(Xv).argmax(1) == yv).float().mean().item()
        tr_acc = (model(Xt).argmax(1) == yt).float().mean().item()

    return {
        "classes": list(enc.classes_),
        "train_acc": tr_acc,
        "val_acc": val_acc,
        "n_train": int(train_m.sum()),
        "n_val": int(val_m.sum()),
        "val_runs": sorted(val_runs),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=cfg.CSV_FILE)
    p.add_argument("--y", default="connection", choices=["connection", "vpn", "tod", "label"])
    p.add_argument("--force", action="store_true")
    p.add_argument("--epochs", type=int, default=25)
    args = p.parse_args()

    try:
        df = load_df(args.csv)
    except FileNotFoundError:
        print(f"no csv at {args.csv}")
        return 1

    check = gru_data_check(df)
    print("data check:", check)
    if not check["justified"] and not args.force:
        print(
            "\nnot training, csv doesn't have enough long sessions.\n"
            "use --force if you want to anyway."
        )
        return 0

    packed = build_sequences(df, y_col=args.y)
    if packed is None:
        print("couldn't build sequences (need runs with >= "
              f"{MIN_ROUNDS} rounds)")
        return 1
    X, y, run_ids, cols = packed
    print(f"windows={len(X)}  T={X.shape[1]}  F={X.shape[2]}  features={cols}")
    print(f"predicting {args.y}")
    result = train_gru(X, y, run_ids, epochs=args.epochs)
    if result:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
