#!/usr/bin/env python3
"""
FastAPI backend — serves live data via WebSocket, analysis via REST.
React frontend connects to this.

    .venv/bin/uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config as cfg
from passive_monitor import TrafficMonitor
from throttle_engine import analyze

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Path Watch")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

monitor: TrafficMonitor | None = None
latest_state: dict = {}
state_lock = asyncio.Lock()


@app.on_event("startup")
async def startup():
    global monitor
    monitor = TrafficMonitor(interval=2.0, on_tick=_on_tick)
    monitor.start()


@app.on_event("shutdown")
async def shutdown():
    if monitor:
        monitor.stop()


def _on_tick(state: dict):
    global latest_state
    latest_state = state


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            if latest_state:
                s = latest_state
                net = s.get("net") or {}
                payload = {
                    "down_mbps": s.get("down_mbps", 0),
                    "up_mbps": s.get("up_mbps", 0),
                    "vpn": net.get("vpn", "novpn"),
                    "connection": net.get("connection", "wifi"),
                    "tod": net.get("tod", ""),
                    "public_ip": net.get("public_ip") or "",
                    "label": net.get("label") or "",
                    "ssid": net.get("ssid") or "",
                    "iface": s.get("iface") or "",
                    "ip_country": net.get("ip_country") or "",
                    "active_count": s.get("active_count", 0),
                    "session_total_fmt": s.get("session_total_fmt") or "0 B",
                    "apps": s.get("apps") or [],
                    "ts": s.get("ts") or "",
                }
                await ws.send_text(json.dumps(payload))
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        pass


@app.get("/api/analysis")
async def get_analysis():
    result = analyze(
        ROOT / cfg.SPEED_CSV,
        ROOT / cfg.TRAFFIC_CSV,
    )
    return result


@app.post("/api/reset")
async def reset_session():
    if monitor:
        monitor.session.reset()
    return {"ok": True}


# serve React build if it exists
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(DIST / "index.html")
