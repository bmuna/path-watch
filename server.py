#!/usr/bin/env python3
"""FastAPI backend — WebSocket live stream + REST analysis."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config as cfg
from passive_monitor import TrafficMonitor
from throttle_engine import analyze_all
from geo_cache import geo

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Path Watch")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

monitor: TrafficMonitor | None = None
latest: dict = {}


@app.on_event("startup")
async def startup():
    global monitor
    monitor = TrafficMonitor(interval=2.0, on_tick=lambda s: _on_tick(s))
    monitor.start()


@app.on_event("shutdown")
async def shutdown():
    if monitor:
        monitor.stop()
    geo.stop()


def _on_tick(state: dict):
    global latest
    latest = state


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            s = latest
            if s:
                net = s.get("net") or {}
                score = s.get("throttle_score") or {}
                payload = {
                    "down_mbps":          s.get("down_mbps", 0),
                    "up_mbps":            s.get("up_mbps",   0),
                    "vpn":                net.get("vpn",        "novpn"),
                    "connection":         net.get("connection", "wifi"),
                    "tod":                net.get("tod",        ""),
                    "public_ip":          net.get("public_ip")  or "",
                    "ip_country":         net.get("ip_country") or "",
                    "label":              net.get("label")      or "",
                    "ssid":               net.get("ssid")       or "",
                    "iface":              s.get("iface")        or "",
                    "active_count":       s.get("active_count", 0),
                    "session_total_fmt":  s.get("session_total_fmt") or "0 B",
                    "apps":               s.get("apps") or [],
                    "ts":                 s.get("ts")   or "",
                    "local_time":         net.get("detected_at") or "",
                    "throttle_score":     score.get("score", 0),
                    "throttle_reason":    score.get("reason", ""),
                    "baseline_down":      score.get("baseline_down"),
                    "live_geo":           s.get("live_geo") or [],
                    "vantage_lat":        getattr(cfg, "VANTAGE_LAT", 9.0245),
                    "vantage_lon":        getattr(cfg, "VANTAGE_LON", 38.7485),
                    "vantage_city":       getattr(cfg, "VANTAGE_CITY", "Addis Ababa"),
                }
                await ws.send_text(json.dumps(payload))
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        pass


@app.get("/api/analysis")
async def get_analysis():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, analyze_all)


@app.get("/api/geo/all")
async def get_all_geo():
    """Return all known IP geo data."""
    return geo.all_known()


@app.post("/api/reset")
async def reset():
    if monitor:
        monitor.session.reset()
    return {"ok": True}


# serve React build if it exists
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        f = DIST / full_path
        if f.exists() and f.is_file():
            return FileResponse(str(f))
        return FileResponse(str(DIST / "index.html"))
