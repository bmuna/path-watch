#!/usr/bin/env python3
"""
Path Watch — desktop app.

Hot path: psutil only (~0.06 ms). Subprocesses run off-thread every 10 s.
UI updates in-place — no widget creation on each tick.
Sparkline is a native tk.Canvas (no matplotlib).
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
ACCENT = "#4dabf7"
WARN   = "#fcc419"
GREEN  = "#51cf66"
RED    = "#ff6b6b"


# ── sparkline ────────────────────────────────────────────────────────────────

class Sparkline(tk.Canvas):
    def __init__(self, master, **kw):
        super().__init__(master, bg=ROW, highlightthickness=0, **kw)
        self._d: deque[float] = deque(maxlen=90)
        self._u: deque[float] = deque(maxlen=90)

    def push(self, down: float, up: float):
        self._d.append(down)
        self._u.append(up)
        self.after_idle(self._draw)

    def _draw(self):
        self.delete("all")
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 20 or H < 20:
            return
        PAD_L, PAD_B = 38, 18

        peak = max(max(self._d, default=0), max(self._u, default=0), 0.5)
        draw_w = W - PAD_L - 4
        draw_h = H - PAD_B - 6

        def pts(series):
            data = list(series)
            n = len(data)
            if n < 2:
                return []
            step = draw_w / max(n - 1, 1)
            return [
                coord
                for i, v in enumerate(data)
                for coord in (PAD_L + i * step, (H - PAD_B) - v / peak * draw_h)
            ]

        # grid lines
        for frac in (0.25, 0.5, 0.75, 1.0):
            y = (H - PAD_B) - frac * draw_h
            self.create_line(PAD_L, y, W - 4, y, fill="#2e3740", width=1)
            self.create_text(PAD_L - 4, y, anchor="e",
                             text=f"{peak*frac:.1f}", fill=MUTED, font=("Helvetica", 9))

        for series, color in ((self._d, ACCENT), (self._u, WARN)):
            p = pts(series)
            if len(p) >= 4:
                self.create_line(p, fill=color, width=2, smooth=True)

        # x-axis
        self.create_line(PAD_L, H - PAD_B, W - 4, H - PAD_B, fill=LINE, width=1)
        self.create_text(PAD_L,     H - 4, anchor="w", text="90 s ago", fill=MUTED, font=("Helvetica", 9))
        self.create_text(W - 4,     H - 4, anchor="e", text="now",       fill=MUTED, font=("Helvetica", 9))

        # legend
        self.create_text(W - 90, 8, anchor="w", text="▬ down", fill=ACCENT, font=("Helvetica", 9))
        self.create_text(W - 45, 8, anchor="w", text="▬ up",   fill=WARN,   font=("Helvetica", 9))
        self.create_text(8, 8, anchor="nw",
                         text=f"Mbps", fill=MUTED, font=("Helvetica", 9))


# ── app row (created once, updated in-place) ─────────────────────────────────

class AppRow:
    """A fixed-height frame representing one app. Never re-created."""

    HEIGHT = 52

    def __init__(self, parent):
        self.frame = tk.Frame(parent, bg=ROW, height=self.HEIGHT)
        self.frame.pack(fill="x", pady=2)
        self.frame.pack_propagate(False)

        # icon placeholder
        self._icon = tk.Label(self.frame, text="●", fg=ACCENT, bg=ROW,
                              font=("Helvetica", 13))
        self._icon.place(x=10, y=14)

        self._name = tk.Label(self.frame, text="", fg=TEXT, bg=ROW,
                              font=("Helvetica", 14, "bold"), anchor="w")
        self._name.place(x=36, y=8, width=220)

        self._size = tk.Label(self.frame, text="", fg=TEXT, bg=ROW,
                              font=("Helvetica", 13, "bold"), anchor="e")
        self._size.place(relx=1.0, x=-14, y=8, anchor="ne", width=120)

        self._sub = tk.Label(self.frame, text="", fg=MUTED, bg=ROW,
                             font=("Helvetica", 10), anchor="w")
        self._sub.place(x=36, y=30, width=340)

        # bar bg
        self._bar_bg = tk.Frame(self.frame, bg=PANEL, height=4)
        self._bar_bg.place(x=36, rely=1.0, y=-8, relwidth=1.0, width=-50, height=4)
        self._bar = tk.Frame(self._bar_bg, bg=ACCENT, height=4)
        self._bar.place(x=0, y=0, height=4, width=0)

    def update(self, name: str, size_fmt: str, sub: str, frac: float):
        self._name.configure(text=name)
        self._size.configure(text=size_fmt)
        self._sub.configure(text=sub)
        # bar width: update after geometry is resolved
        self.frame.update_idletasks()
        bar_w = max(int(frac * self._bar_bg.winfo_width()), 0)
        self._bar.place(width=bar_w)

    def show(self): self.frame.pack(fill="x", pady=2)
    def hide(self): self.frame.pack_forget()


# ── KPI card ─────────────────────────────────────────────────────────────────

class KpiCard:
    def __init__(self, parent, title: str):
        f = tk.Frame(parent, bg=PANEL, bd=0)
        f.pack(side="left", fill="x", expand=True, padx=3)
        tk.Label(f, text=title.upper(), fg=MUTED, bg=PANEL,
                 font=("Helvetica", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        self._val = tk.Label(f, text="—", fg=TEXT, bg=PANEL,
                             font=("Helvetica", 18, "bold"))
        self._val.pack(anchor="w", padx=10, pady=(0, 10))

    def set(self, text: str, color: str = TEXT):
        self._val.configure(text=text, fg=color)


# ── main window ──────────────────────────────────────────────────────────────

class PathWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Path Watch")
        self.geometry("1120x760")
        self.minsize(900, 640)
        self.configure(fg_color=BG)

        self.monitor = TrafficMonitor(interval=2.0, on_tick=self._on_tick)
        self._queue: deque = deque(maxlen=4)
        self._lock  = threading.Lock()
        self._rows: dict[str, AppRow] = {}    # name → row; never re-created

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain)
        self.after(2000, self._refresh_hints)
        self.monitor.start()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self):
        # header
        hdr = tk.Frame(self, bg=PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Path Watch", fg=TEXT, bg=PANEL,
                 font=("Helvetica", 20, "bold")).pack(side="left", padx=18, pady=12)
        tk.Label(hdr, text="Watches your apps · no website probing",
                 fg=MUTED, bg=PANEL, font=("Helvetica", 12)).pack(side="left", padx=4)
        self._clock = tk.Label(hdr, text="", fg=MUTED, bg=PANEL, font=("Helvetica", 12))
        self._clock.pack(side="right", padx=18)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # KPI row
        kpi_row = tk.Frame(body, bg=BG)
        kpi_row.pack(fill="x", pady=(0, 8))
        self._kpi: dict[str, KpiCard] = {}
        for key, title in [("down","Download"),("up","Upload"),("vpn","VPN"),
                            ("link","Connection"),("tod","Time"),
                            ("ip","Public IP"),("total","Session data")]:
            self._kpi[key] = KpiCard(kpi_row, title)

        # mid: sparkline + hints
        mid = tk.Frame(body, bg=BG)
        mid.pack(fill="both", expand=True)

        # sparkline panel
        lp = tk.Frame(mid, bg=PANEL, bd=0)
        lp.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(lp, text="Live speed", fg=TEXT, bg=PANEL,
                 font=("Helvetica", 14, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        self._spark = Sparkline(lp, height=130)
        self._spark.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        # hints panel
        rp = tk.Frame(mid, bg=PANEL, bd=0, width=390)
        rp.pack(side="right", fill="both")
        rp.pack_propagate(False)
        tk.Label(rp, text="Throttling check", fg=TEXT, bg=PANEL,
                 font=("Helvetica", 14, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(rp,
                 text="Compares speed across VPN on/off · wifi vs hotspot · time of day.",
                 fg=MUTED, bg=PANEL, font=("Helvetica", 11),
                 wraplength=360, justify="left").pack(anchor="w", padx=12, pady=(0, 4))
        self._hints = tk.Text(rp, bg=ROW, fg=TEXT, font=("Menlo", 11),
                              relief="flat", wrap="word", state="disabled",
                              padx=10, pady=8, cursor="arrow")
        self._hints.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        btn_r = tk.Frame(rp, bg=PANEL)
        btn_r.pack(fill="x", padx=10, pady=(0, 10))
        for lbl, cmd in [("Refresh", self._refresh_hints),
                         ("Reset",   self._reset),
                         ("Open logs", self._open_logs)]:
            tk.Button(btn_r, text=lbl, command=cmd,
                      bg=LINE, fg=TEXT, relief="flat",
                      font=("Helvetica", 12), padx=10, pady=4,
                      activebackground="#4a5568", activeforeground=TEXT,
                      cursor="hand2").pack(side="left", padx=(0, 6))

        # app list
        ap = tk.Frame(body, bg=PANEL, bd=0)
        ap.pack(fill="both", expand=True, pady=(8, 0))
        ah = tk.Frame(ap, bg=PANEL)
        ah.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(ah, text="Apps using your network", fg=TEXT, bg=PANEL,
                 font=("Helvetica", 14, "bold")).pack(side="left")
        self._conn_lbl = tk.Label(ah, text="", fg=MUTED, bg=PANEL,
                                  font=("Helvetica", 11))
        self._conn_lbl.pack(side="left", padx=10)

        canvas = tk.Canvas(ap, bg=PANEL, highlightthickness=0)
        vsb = tk.Scrollbar(ap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        self._list = tk.Frame(canvas, bg=PANEL)
        self._list_id = canvas.create_window((0, 0), window=self._list, anchor="nw")
        self._list.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(
            self._list_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-e.delta / 60), "units"))
        self._scroll_canvas = canvas

    # ── threading ────────────────────────────────────────────────────────────

    def _on_tick(self, state: dict):
        with self._lock:
            self._queue.append(state)

    def _drain(self):
        state = None
        with self._lock:
            if self._queue:
                state = self._queue[-1]
                self._queue.clear()
        if state:
            self._apply(state)
        self.after(180, self._drain)

    # ── in-place update ───────────────────────────────────────────────────────

    def _apply(self, state: dict):
        net = state.get("net") or {}
        self._clock.configure(text=datetime.now().strftime("%H:%M:%S"))

        self._kpi["down"].set(f"{state.get('down_mbps', 0):.2f} Mbps")
        self._kpi["up"].set(f"{state.get('up_mbps', 0):.2f} Mbps")
        vpn_on = net.get("vpn") == "vpn"
        self._kpi["vpn"].set("ON" if vpn_on else "OFF",
                             GREEN if vpn_on else TEXT)
        self._kpi["link"].set(str(net.get("connection", "—")).upper())
        self._kpi["tod"].set(str(net.get("tod", "—")))
        self._kpi["ip"].set(str(net.get("public_ip") or "—"))
        self._kpi["total"].set(state.get("session_total_fmt") or "0 B")

        self._spark.push(state.get("down_mbps", 0), state.get("up_mbps", 0))

        apps = state.get("apps") or []
        n_sockets = state.get("active_count", 0)
        self._conn_lbl.configure(
            text=f"{n_sockets} sockets · {len(apps)} apps"
        )

        max_b = max((a.get("total_bytes") or 0 for a in apps), default=1) or 1
        seen: set[str] = set()

        for app in apps:
            name = app.get("name") or "?"
            seen.add(name)
            conns = int(app.get("conns") or 0)
            hosts = app.get("top_hosts") or []
            sub = f"{conns} socket{'s' if conns != 1 else ''}"
            if hosts:
                sub += f" · {hosts[0]}"
            frac = (app.get("total_bytes") or 0) / max_b

            if name not in self._rows:
                self._rows[name] = AppRow(self._list)
            self._rows[name].show()
            self._rows[name].update(name, app.get("total_fmt") or "0 B", sub, frac)

        for name, row in self._rows.items():
            if name not in seen:
                row.hide()

    # ── hints (off-thread) ───────────────────────────────────────────────────

    def _refresh_hints(self):
        def work():
            try:
                text = throttling_report(ROOT / cfg.TRAFFIC_CSV, ROOT / cfg.SPEED_CSV)
            except Exception as e:
                text = f"Error: {e}"
            self.after(0, self._set_hints, text)

        threading.Thread(target=work, daemon=True, name="hints").start()
        self.after(15_000, self._refresh_hints)

    def _set_hints(self, text: str):
        self._hints.configure(state="normal")
        self._hints.delete("1.0", "end")
        self._hints.insert("1.0", text)
        self._hints.configure(state="disabled")

    # ── misc ─────────────────────────────────────────────────────────────────

    def _reset(self):
        self.monitor.session.reset()
        self._kpi["total"].set("0 B")

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
