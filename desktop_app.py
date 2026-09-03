#!/usr/bin/env python3
"""
Path Watch — desktop app.

Wide readable layout. One row per app (not one row per socket).
Throttling panel on the main screen.

    .venv/bin/python desktop_app.py
"""

from __future__ import annotations

import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import config as cfg
from passive_monitor import TrafficMonitor
from traffic_analyze import throttling_report

ROOT = Path(__file__).resolve().parent
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# high contrast — readable on dark
BG = "#121418"
PANEL = "#1c2128"
ROW = "#252b33"
LINE = "#3d4654"
TEXT = "#f0f4f8"
MUTED = "#b0bcc9"
ACCENT = "#4dabf7"
GREEN = "#51cf66"
WARN = "#fcc419"


class PathWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Path Watch")
        self.geometry("1100x740")
        self.minsize(900, 620)
        self.configure(fg_color=BG)

        self.monitor = TrafficMonitor(interval=2.0, on_tick=self._on_tick)
        self._queue: deque = deque(maxlen=8)
        self._lock = threading.Lock()
        self._down = deque(maxlen=60)
        self._up = deque(maxlen=60)
        self._app_widgets: list = []

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(200, self._drain)
        self.after(1500, self._refresh_hints)
        self.monitor.start()

    def _build(self):
        # header
        head = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=56)
        head.pack(fill="x")
        head.pack_propagate(False)
        ctk.CTkLabel(head, text="Path Watch", font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT).pack(
            side="left", padx=20, pady=12
        )
        ctk.CTkLabel(
            head,
            text="Watches your apps · no website probing",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
        ).pack(side="left", padx=4)
        self.clock_lbl = ctk.CTkLabel(head, text="", font=ctk.CTkFont(size=13), text_color=MUTED)
        self.clock_lbl.pack(side="right", padx=20)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # KPIs
        kpi = ctk.CTkFrame(body, fg_color="transparent")
        kpi.pack(fill="x", pady=(0, 10))
        self.kpi = {}
        for key, title in [
            ("down", "Download"),
            ("up", "Upload"),
            ("vpn", "VPN"),
            ("link", "Connection"),
            ("tod", "Time"),
            ("ip", "Public IP"),
            ("total", "Session data"),
        ]:
            card = ctk.CTkFrame(kpi, fg_color=PANEL, corner_radius=10, border_width=1, border_color=LINE)
            card.pack(side="left", fill="x", expand=True, padx=3)
            ctk.CTkLabel(card, text=title.upper(), text_color=MUTED, font=ctk.CTkFont(size=11, weight="bold")).pack(
                anchor="w", padx=12, pady=(8, 0)
            )
            lbl = ctk.CTkLabel(card, text="—", text_color=TEXT, font=ctk.CTkFont(size=18, weight="bold"))
            lbl.pack(anchor="w", padx=12, pady=(0, 10))
            self.kpi[key] = lbl

        mid = ctk.CTkFrame(body, fg_color="transparent")
        mid.pack(fill="both", expand=True)

        left = ctk.CTkFrame(mid, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ctk.CTkLabel(left, text="Live speed", text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        self.fig = Figure(figsize=(5.5, 2.4), dpi=100, facecolor=PANEL)
        self.ax = self.fig.add_subplot(111)
        self._style_ax()
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=8)

        right = ctk.CTkFrame(mid, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE, width=380)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)

        ctk.CTkLabel(right, text="Throttling check", text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        ctk.CTkLabel(
            right,
            text="Compares speed + destinations across VPN, link, and time.",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            wraplength=340,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.hints = ctk.CTkTextbox(
            right,
            fg_color=ROW,
            text_color=TEXT,
            font=ctk.CTkFont(family="Menlo", size=12),
            wrap="word",
        )
        self.hints.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.hints.insert("1.0", "Loading analysis…")
        self.hints.configure(state="disabled")

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(btn_row, text="Refresh", width=90, height=28, fg_color=LINE, command=self._refresh_hints).pack(
            side="left"
        )
        ctk.CTkButton(btn_row, text="Reset session", width=110, height=28, fg_color=LINE, command=self._reset).pack(
            side="left", padx=6
        )
        ctk.CTkButton(btn_row, text="Open logs", width=90, height=28, fg_color=LINE, command=self._open_logs).pack(
            side="right"
        )

        # apps — one row per app
        bottom = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE)
        bottom.pack(fill="both", expand=True, pady=(10, 0))

        bh = ctk.CTkFrame(bottom, fg_color="transparent")
        bh.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(bh, text="Apps using your network", text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold")).pack(
            side="left"
        )
        ctk.CTkLabel(
            bh,
            text="One row per app · Browser ×20 sockets = one Browser row",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=12)

        self.app_list = ctk.CTkScrollableFrame(bottom, fg_color="transparent", height=200)
        self.app_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _style_ax(self):
        self.ax.set_facecolor(ROW)
        self.ax.tick_params(colors=MUTED, labelsize=9)
        for s in self.ax.spines.values():
            s.set_color(LINE)
        self.ax.set_ylabel("Mbps", color=MUTED, fontsize=10)
        self.ax.grid(True, color=LINE, alpha=0.45, linewidth=0.6)

    def _on_tick(self, state: dict):
        with self._lock:
            self._queue.append(state)

    def _drain(self):
        state = None
        with self._lock:
            while self._queue:
                state = self._queue.popleft()
        if state:
            self._apply(state)
        self.after(250, self._drain)

    def _clear_apps(self):
        for w in self._app_widgets:
            w.destroy()
        self._app_widgets.clear()

    def _apply(self, state: dict):
        net = state.get("net") or {}
        self.clock_lbl.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")

        self.kpi["down"].configure(text=f"{state.get('down_mbps', 0):.2f} Mbps")
        self.kpi["up"].configure(text=f"{state.get('up_mbps', 0):.2f} Mbps")
        vpn_on = net.get("vpn") == "vpn"
        self.kpi["vpn"].configure(text="ON" if vpn_on else "OFF", text_color=GREEN if vpn_on else TEXT)
        self.kpi["link"].configure(text=str(net.get("connection", "—")).upper())
        self.kpi["tod"].configure(text=str(net.get("tod", "—")))
        self.kpi["ip"].configure(text=str(net.get("public_ip") or "—"), font=ctk.CTkFont(size=14, weight="bold"))
        self.kpi["total"].configure(text=state.get("session_total_fmt") or "0 B")

        self._down.append(state.get("down_mbps", 0))
        self._up.append(state.get("up_mbps", 0))
        self.ax.clear()
        self._style_ax()
        xs = list(range(len(self._down)))
        self.ax.plot(xs, list(self._down), color=ACCENT, linewidth=2, label="down")
        self.ax.plot(xs, list(self._up), color=WARN, linewidth=2, label="up")
        self.ax.legend(loc="upper right", facecolor=PANEL, edgecolor=LINE, labelcolor=TEXT, fontsize=9)
        self.fig.tight_layout()
        self.canvas.draw_idle()

        apps = state.get("apps") or []
        max_b = max((a.get("total_bytes") or 0 for a in apps), default=1) or 1
        self._clear_apps()

        if not apps:
            lbl = ctk.CTkLabel(
                self.app_list,
                text="Open a browser or app — usage shows here with a blue bar.",
                text_color=MUTED,
                font=ctk.CTkFont(size=13),
            )
            lbl.pack(pady=30)
            self._app_widgets.append(lbl)
            return

        for app in apps:
            row = ctk.CTkFrame(self.app_list, fg_color=ROW, corner_radius=8)
            row.pack(fill="x", pady=3)
            self._app_widgets.append(row)

            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 4))

            name = app.get("name") or "?"
            ctk.CTkLabel(top, text=name, text_color=TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
            ctk.CTkLabel(
                top,
                text=app.get("total_fmt") or "0 B",
                text_color=TEXT,
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(side="right")

            conns = int(app.get("conns") or 0)
            hosts = app.get("top_hosts") or []
            sub = f"{conns} open connection{'s' if conns != 1 else ''}"
            if hosts:
                sub += f" · {hosts[0]}"
            ctk.CTkLabel(top, text=sub, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12)

            bar_bg = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=4, height=8)
            bar_bg.pack(fill="x", padx=12, pady=(0, 10))
            bar_bg.pack_propagate(False)
            frac = (app.get("total_bytes") or 0) / max_b
            if frac > 0:
                w = max(int(frac * 400), 6)
                ctk.CTkFrame(bar_bg, fg_color=ACCENT, corner_radius=4, width=w, height=8).pack(side="left")

    def _refresh_hints(self):
        def work():
            try:
                text = throttling_report(ROOT / cfg.TRAFFIC_CSV, ROOT / cfg.SPEED_CSV)
            except Exception as e:
                text = f"Analysis error: {e}"
            self.after(0, lambda: self._set_hints(text))

        threading.Thread(target=work, daemon=True).start()
        self.after(12000, self._refresh_hints)

    def _set_hints(self, text: str):
        self.hints.configure(state="normal")
        self.hints.delete("1.0", "end")
        self.hints.insert("1.0", text)
        self.hints.configure(state="disabled")

    def _reset(self):
        self.monitor.session.reset()
        self.kpi["total"].configure(text="0 B")
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
