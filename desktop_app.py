#!/usr/bin/env python3
"""
Path Watch — desktop app.

Wide, readable. Smooth: monitor runs in background thread, UI only updates
labels in-place (no widget rebuilds). Speed chart is a lightweight canvas
sparkline — no matplotlib overhead.

    .venv/bin/python desktop_app.py
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

import config as cfg
from passive_monitor import TrafficMonitor
from traffic_analyze import throttling_report

ROOT = Path(__file__).resolve().parent
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

BG     = "#121418"
PANEL  = "#1c2128"
ROW    = "#252b33"
LINE   = "#3d4654"
TEXT   = "#f0f4f8"
MUTED  = "#9eafc0"
ACCENT = "#4dabf7"   # blue — download
WARN   = "#fcc419"   # amber — upload
GREEN  = "#51cf66"


# ---------------------------------------------------------------------------
# Lightweight canvas sparkline (no matplotlib)
# ---------------------------------------------------------------------------

class Sparkline(tk.Canvas):
    """Draw two line series directly on a tk.Canvas. Fast — no Matplotlib."""

    def __init__(self, master, bg: str = ROW, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, **kw)
        self._d: deque[float] = deque(maxlen=80)
        self._u: deque[float] = deque(maxlen=80)
        self._legend_drawn = False

    def push(self, down: float, up: float):
        self._d.append(down)
        self._u.append(up)
        self._redraw()

    def _redraw(self):
        self.delete("data")
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 10 or H < 10:
            return
        pad = 28
        w = W - pad
        h = H - 24

        peak = max(max(self._d, default=0), max(self._u, default=0), 0.5)

        def pts(series):
            n = len(series)
            if n < 2:
                return []
            step = w / max(n - 1, 1)
            out = []
            for i, v in enumerate(series):
                x = pad + i * step
                y = h - (v / peak) * (h - 4)
                out += [x, y]
            return out

        for series, color in [(self._d, ACCENT), (self._u, WARN)]:
            p = pts(series)
            if len(p) >= 4:
                self.create_line(p, fill=color, width=2, smooth=True, tags="data")

        # y-axis labels
        self.delete("axis")
        self.create_text(2, 4,  anchor="nw", text=f"{peak:.1f}", fill=MUTED, font=("Helvetica", 9), tags="axis")
        self.create_text(2, h//2, anchor="w",  text=f"{peak/2:.1f}", fill=MUTED, font=("Helvetica", 9), tags="axis")
        self.create_text(2, h-4, anchor="sw", text="0", fill=MUTED, font=("Helvetica", 9), tags="axis")

        if not self._legend_drawn:
            self.create_text(W - 80, H - 14, anchor="w", text="▬ down", fill=ACCENT, font=("Helvetica", 9))
            self.create_text(W - 30, H - 14, anchor="w", text="▬ up", fill=WARN, font=("Helvetica", 9))
            self._legend_drawn = True


# ---------------------------------------------------------------------------
# App row — created once, updated in-place
# ---------------------------------------------------------------------------

class AppRow(ctk.CTkFrame):
    def __init__(self, master, name: str):
        super().__init__(master, fg_color=ROW, corner_radius=8)
        self.pack(fill="x", pady=3)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8, 2))

        self._name_lbl = ctk.CTkLabel(
            top, text=name, text_color=TEXT,
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
        )
        self._name_lbl.pack(side="left", fill="x", expand=True)

        self._size_lbl = ctk.CTkLabel(
            top, text="0 B", text_color=TEXT,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self._size_lbl.pack(side="right")

        self._sub_lbl = ctk.CTkLabel(
            self, text="", text_color=MUTED,
            font=ctk.CTkFont(size=11), anchor="w"
        )
        self._sub_lbl.pack(fill="x", padx=12)

        self._bar_bg = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=3, height=6)
        self._bar_bg.pack(fill="x", padx=12, pady=(4, 10))
        self._bar_bg.pack_propagate(False)
        self._bar = ctk.CTkFrame(self._bar_bg, fg_color=ACCENT, corner_radius=3, width=0, height=6)
        self._bar.pack(side="left")

    def update(self, size_fmt: str, frac: float, sub: str, bar_w: int):
        self._size_lbl.configure(text=size_fmt)
        self._sub_lbl.configure(text=sub)
        self._bar.configure(width=max(bar_w, 0))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class PathWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Path Watch")
        self.geometry("1100x740")
        self.minsize(900, 620)
        self.configure(fg_color=BG)

        self.monitor = TrafficMonitor(interval=3.0, on_tick=self._on_tick)
        self._queue: deque = deque(maxlen=4)
        self._lock  = threading.Lock()
        # rows keyed by app name — created once, updated in-place
        self._rows: dict[str, AppRow] = {}
        self._row_order: list[str] = []

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain)
        self.after(1500, self._refresh_hints)
        self.monitor.start()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        # header
        head = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        ctk.CTkLabel(
            head, text="Path Watch",
            font=ctk.CTkFont(size=21, weight="bold"), text_color=TEXT
        ).pack(side="left", padx=18, pady=12)
        ctk.CTkLabel(
            head, text="Watches your apps · no website probing",
            font=ctk.CTkFont(size=13), text_color=MUTED
        ).pack(side="left", padx=4)
        self._clock = ctk.CTkLabel(head, text="", font=ctk.CTkFont(size=13), text_color=MUTED)
        self._clock.pack(side="right", padx=18)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        # KPI row
        kpi_row = ctk.CTkFrame(body, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, 10))
        self._kpi: dict[str, ctk.CTkLabel] = {}
        for key, title in [
            ("down",  "Download"),
            ("up",    "Upload"),
            ("vpn",   "VPN"),
            ("link",  "Connection"),
            ("tod",   "Time"),
            ("ip",    "Public IP"),
            ("total", "Session data"),
        ]:
            c = ctk.CTkFrame(kpi_row, fg_color=PANEL, corner_radius=10,
                             border_width=1, border_color=LINE)
            c.pack(side="left", fill="x", expand=True, padx=3)
            ctk.CTkLabel(c, text=title.upper(), text_color=MUTED,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=12, pady=(8, 0))
            v = ctk.CTkLabel(c, text="—", text_color=TEXT,
                             font=ctk.CTkFont(size=18, weight="bold"))
            v.pack(anchor="w", padx=12, pady=(0, 10))
            self._kpi[key] = v

        # mid row: sparkline + hints
        mid = ctk.CTkFrame(body, fg_color="transparent")
        mid.pack(fill="both", expand=True)

        left = ctk.CTkFrame(mid, fg_color=PANEL, corner_radius=12,
                            border_width=1, border_color=LINE)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ctk.CTkLabel(left, text="Live speed",
                     text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=14, pady=(12, 4))
        self._spark = Sparkline(left, bg=ROW, height=130)
        self._spark.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        right = ctk.CTkFrame(mid, fg_color=PANEL, corner_radius=12,
                             border_width=1, border_color=LINE, width=380)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)
        ctk.CTkLabel(right, text="Throttling check",
                     text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(right,
                     text="Compares speed across VPN on/off · wifi vs hotspot · time of day.",
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     wraplength=350, justify="left"
                     ).pack(anchor="w", padx=14, pady=(0, 6))
        self._hints = ctk.CTkTextbox(right, fg_color=ROW, text_color=TEXT,
                                     font=ctk.CTkFont(family="Menlo", size=12), wrap="word")
        self._hints.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._hints.insert("1.0", "Loading…")
        self._hints.configure(state="disabled")
        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(btn_row, text="Refresh", width=90, height=28,
                      fg_color=LINE, command=self._refresh_hints).pack(side="left")
        ctk.CTkButton(btn_row, text="Reset", width=80, height=28,
                      fg_color=LINE, command=self._reset).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Open logs", width=90, height=28,
                      fg_color=LINE, command=self._open_logs).pack(side="right")

        # app list
        bottom = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=12,
                              border_width=1, border_color=LINE)
        bottom.pack(fill="both", expand=True, pady=(10, 0))
        bh = ctk.CTkFrame(bottom, fg_color="transparent")
        bh.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(bh, text="Apps using your network",
                     text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(side="left")
        self._conn_count = ctk.CTkLabel(bh, text="", text_color=MUTED,
                                        font=ctk.CTkFont(size=12))
        self._conn_count.pack(side="left", padx=12)
        self._list = ctk.CTkScrollableFrame(bottom, fg_color="transparent", height=180)
        self._list.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ------------------------------------------------------------------
    # Threading
    # ------------------------------------------------------------------

    def _on_tick(self, state: dict):
        with self._lock:
            self._queue.append(state)

    def _drain(self):
        state = None
        with self._lock:
            if self._queue:
                state = self._queue[-1]   # only latest
                self._queue.clear()
        if state:
            self._apply(state)
        self.after(200, self._drain)

    # ------------------------------------------------------------------
    # UI update — in-place only, no widget creation on the hot path
    # ------------------------------------------------------------------

    def _apply(self, state: dict):
        net = state.get("net") or {}

        # clock
        self._clock.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")

        # KPIs
        down = state.get("down_mbps", 0)
        up   = state.get("up_mbps",   0)
        self._kpi["down"].configure(text=f"{down:.2f} Mbps")
        self._kpi["up"].configure(text=f"{up:.2f} Mbps")
        vpn_on = net.get("vpn") == "vpn"
        self._kpi["vpn"].configure(text="ON" if vpn_on else "OFF",
                                   text_color=GREEN if vpn_on else TEXT)
        self._kpi["link"].configure(text=str(net.get("connection", "—")).upper())
        self._kpi["tod"].configure(text=str(net.get("tod", "—")))
        self._kpi["ip"].configure(text=str(net.get("public_ip") or "—"),
                                  font=ctk.CTkFont(size=14, weight="bold"))
        self._kpi["total"].configure(text=state.get("session_total_fmt") or "0 B")

        # sparkline — one call, all math is inside the canvas
        self._spark.push(down, up)

        # app rows — create missing ones, update existing ones in-place
        apps = state.get("apps") or []
        n_conns = state.get("active_count", 0)
        self._conn_count.configure(
            text=f"{n_conns} open socket{'s' if n_conns != 1 else ''}"
                 f" across {len(apps)} app{'s' if len(apps) != 1 else ''}"
        )

        if not apps:
            for w in self._rows.values():
                w.pack_forget()
            return

        max_b = max((a.get("total_bytes") or 0 for a in apps), default=1) or 1
        bar_max = self._list.winfo_width() - 30

        seen = set()
        for app in apps:
            name = app.get("name") or "?"
            seen.add(name)
            frac = (app.get("total_bytes") or 0) / max_b
            bar_w = max(int(frac * bar_max), 0)
            conns = int(app.get("conns") or 0)
            hosts = app.get("top_hosts") or []
            sub = f"{conns} connection{'s' if conns != 1 else ''}"
            if hosts:
                sub += f" · {hosts[0]}"

            if name not in self._rows:
                row = AppRow(self._list, name)
                self._rows[name] = row
                self._row_order.append(name)

            self._rows[name].update(
                size_fmt=app.get("total_fmt") or "0 B",
                frac=frac,
                sub=sub,
                bar_w=bar_w,
            )
            self._rows[name].pack(fill="x", pady=3)

        # hide rows for apps no longer active
        for name, row in self._rows.items():
            if name not in seen:
                row.pack_forget()

    # ------------------------------------------------------------------
    # Hints (background thread, slow OK)
    # ------------------------------------------------------------------

    def _refresh_hints(self):
        def work():
            try:
                text = throttling_report(ROOT / cfg.TRAFFIC_CSV, ROOT / cfg.SPEED_CSV)
            except Exception as e:
                text = f"Analysis error: {e}"
            self.after(0, self._set_hints, text)

        threading.Thread(target=work, daemon=True, name="hints").start()
        self.after(15_000, self._refresh_hints)

    def _set_hints(self, text: str):
        self._hints.configure(state="normal")
        self._hints.delete("1.0", "end")
        self._hints.insert("1.0", text)
        self._hints.configure(state="disabled")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _reset(self):
        self.monitor.session.reset()
        self._kpi["total"].configure(text="0 B")
        self._refresh_hints()

    def _open_logs(self):
        os.system(f'open "{ROOT}"')

    def _close(self):
        self.monitor.stop()
        self.destroy()


def main():
    os.chdir(ROOT)
    app = PathWatchApp()
    app.mainloop()


if __name__ == "__main__":
    main()
