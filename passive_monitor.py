#!/usr/bin/env python3
"""
Passive traffic monitor — no subprocesses on the hot path.

Hot path (~0.06 ms): psutil.net_io_counters only.
Every connection is geo-enriched (lat/lon/ISP/country) via ip-api.com batch
running off-thread. Geo data is stored in traffic_log.csv.
"""

from __future__ import annotations

import csv
import os
import platform
import socket
import subprocess
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

import psutil

import config as cfg
from geo_cache import geo, is_private
from network_status import snapshot as net_snapshot

TRAFFIC_CSV = getattr(cfg, "TRAFFIC_CSV", "traffic_log.csv")
SPEED_CSV   = getattr(cfg, "SPEED_CSV",   "speed_log.csv")

TRAFFIC_FIELDS = [
    "ts", "event", "remote_ip", "remote_port", "local_port", "status",
    "pid", "process", "hostname",
    "connection", "vpn", "tod", "label", "public_ip", "ssid",
    # geo fields — filled in as geo resolves
    "geo_lat", "geo_lon", "geo_country", "geo_country_code",
    "geo_city", "geo_region", "geo_isp", "geo_org", "geo_asn",
]

SPEED_FIELDS = [
    "ts", "down_mbps", "up_mbps", "bytes_recv", "bytes_sent",
    "connection", "vpn", "tod", "label", "public_ip", "iface",
]


def _ensure_csv(path: str, fields: list[str]):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()

def _append_csv(path: str, fields: list[str], rows: list[dict]):
    if not rows:
        return
    _ensure_csv(path, fields)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def fmt_bytes(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1e9:.2f} GB"
    if n >= 1_000_000:     return f"{n/1e6:.1f} MB"
    if n >= 1_000:         return f"{n/1e3:.1f} KB"
    return f"{n} B"


def _live_geo(conns: list) -> list[dict]:
    out = []
    seen = set()
    for c in conns:
        ip = c.get("remote_ip")
        if not ip or ip in seen:
            continue
        g = geo.get(ip)
        if not g:
            continue
        try:
            lat = float(g["lat"])
            lon = float(g["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        seen.add(ip)
        out.append({
            "ip": ip,
            "lat": lat,
            "lon": lon,
            "city": g.get("city") or "",
            "country": g.get("country") or "",
            "isp": g.get("isp") or "",
            "org": g.get("org") or "",
            "asn": g.get("asn") or "",
            "n": 1,
        })
    # connection counts
    counts = {}
    for c in conns:
        ip = c.get("remote_ip")
        if ip:
            counts[ip] = counts.get(ip, 0) + 1
    for row in out:
        row["n"] = counts.get(row["ip"], 1)
    return out

def pretty_name(raw: str) -> str:
    for s in (" Helper (Renderer)", " Helper (GPU)", " Helper"):
        if raw.endswith(s):
            raw = raw[:-len(s)]
    if raw.endswith(")"):
        raw = raw.split("(")[0].strip()
    return raw.strip() or "unknown"


class HostCache:
    def __init__(self, maxsize: int = 1024):
        self._c: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="rdns")

    def get(self, ip: str) -> str:
        with self._lock:
            if ip in self._c:
                self._c.move_to_end(ip)
                return self._c[ip]
        return ""

    def prefetch(self, ip: str):
        with self._lock:
            if ip in self._c:
                return
            self._c[ip] = ""
        self._pool.submit(self._resolve, ip)

    def _resolve(self, ip: str):
        try:
            name = socket.gethostbyaddr(ip)[0]
        except Exception:
            name = ""
        with self._lock:
            self._c[ip] = name
            self._c.move_to_end(ip)
            while len(self._c) > 1024:
                self._c.popitem(last=False)


def _best_iface() -> tuple[str, int, int]:
    per = psutil.net_io_counters(pernic=True)
    best, best_total, best_name = None, -1, "all"
    skip = ("lo", "awdl", "llw", "utun", "bridge", "ap", "lo0")
    for name, c in per.items():
        if any(name.startswith(s) for s in skip):
            continue
        t = c.bytes_recv + c.bytes_sent
        if t > best_total:
            best_total, best, best_name = t, c, name
    if best is None:
        c = psutil.net_io_counters()
        return "all", c.bytes_recv, c.bytes_sent
    return best_name, best.bytes_recv, best.bytes_sent


def _connections_fast() -> list[dict]:
    try:
        rows = []
        for c in psutil.net_connections(kind="inet"):
            if not c.raddr:
                continue
            ip = c.raddr.ip
            if is_private(ip):
                continue
            try:
                proc = psutil.Process(c.pid).name() if c.pid else ""
            except Exception:
                proc = ""
            rows.append({
                "remote_ip":   ip,
                "remote_port": int(c.raddr.port),
                "local_port":  int(c.laddr.port) if c.laddr else 0,
                "status":      c.status,
                "pid":         c.pid or 0,
                "process":     pretty_name(proc),
            })
        return rows
    except (psutil.AccessDenied, PermissionError):
        pass
    if platform.system() != "Darwin":
        return []
    try:
        p = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"],
            capture_output=True, text=True, timeout=6,
        )
    except Exception:
        return []
    rows = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name_f = next((x for x in parts if "->" in x), "")
        if not name_f:
            continue
        left, right = name_f.split("->", 1)
        if ":" not in right:
            continue
        rip, rport_s = right.rsplit(":", 1)
        rip = rip.strip("[]")
        if is_private(rip):
            continue
        try:
            rport = int(rport_s)
            lport = int(left.rsplit(":", 1)[-1]) if ":" in left else 0
            pid   = int(parts[1])
        except ValueError:
            continue
        rows.append({
            "remote_ip":   rip,
            "remote_port": rport,
            "local_port":  lport,
            "status":      "ESTABLISHED",
            "pid":         pid,
            "process":     pretty_name(parts[0]),
        })
    return rows


def _nettop_bytes() -> dict[str, tuple[int, int]]:
    if platform.system() != "Darwin":
        return {}
    try:
        p = subprocess.run(
            ["nettop", "-P", "-L", "1", "-s", "0", "-J", "bytes_in,bytes_out"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(",") or line.startswith("time"):
            continue
        parts = line.split(",")
        if len(parts) < 3 or "." not in parts[0]:
            continue
        name = pretty_name(parts[0].rsplit(".", 1)[0])
        try:
            bi, bo = int(parts[1] or 0), int(parts[2] or 0)
        except ValueError:
            continue
        prev = out.get(name, (0, 0))
        out[name] = (max(prev[0], bi), max(prev[1], bo))
    return out


class SessionUsage:
    def __init__(self):
        self._base: dict[str, tuple[int, int]] = {}
        self._started = False
        self._cur: dict[str, tuple[int, int]] = {}

    def reset(self):
        self._base = dict(self._cur)
        self._started = True

    def update_nettop(self, cur: dict[str, tuple[int, int]]):
        self._cur = cur
        if not self._started:
            self._base = dict(cur)
            self._started = True

    def delta(self, name: str) -> tuple[int, int]:
        bi, bo = self._cur.get(name, (0, 0))
        bbi, bbo = self._base.get(name, (bi, bo))
        return max(0, bi - bbi), max(0, bo - bbo)


class TrafficMonitor:
    def __init__(self, interval: float = 2.0, on_tick: Callable | None = None):
        self.interval = interval
        self.on_tick  = on_tick
        self.hosts    = HostCache()
        self.session  = SessionUsage()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._nettop_lock  = threading.Lock()
        self._nettop_cache: dict[str, tuple[int, int]] = {}
        self._nettop_time  = 0.0
        self._last_recv    = 0
        self._last_sent    = 0
        self._last_t       = 0.0
        self._seen: set[tuple] = set()

        self.latest: dict = {
            "down_mbps": 0.0, "up_mbps": 0.0,
            "active_count": 0, "apps": [], "net": {},
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        iface, recv, sent = _best_iface()
        self._last_recv, self._last_sent, self._last_t = recv, sent, time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="traffic-mon")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _refresh_nettop(self):
        nb = _nettop_bytes()
        with self._nettop_lock:
            self._nettop_cache = nb
            self._nettop_time  = time.time()
        self.session.update_nettop(nb)

    def _loop(self):
        threading.Thread(target=self._refresh_nettop, daemon=True, name="nettop-init").start()
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self._sample()
            except Exception as e:
                self.latest["error"] = str(e)
            if time.time() - self._nettop_time > 10.0:
                self._nettop_time = time.time()
                threading.Thread(target=self._refresh_nettop, daemon=True).start()
            elapsed = time.perf_counter() - t0
            self._stop.wait(max(0.0, self.interval - elapsed))

    def _sample(self):
        net = net_snapshot()
        now = time.time()
        iface, recv, sent = _best_iface()
        dt   = max(now - self._last_t, 1e-6)
        down = max(0.0, min((recv - self._last_recv) * 8 / dt / 1e6, 5000.0))
        up   = max(0.0, min((sent - self._last_sent) * 8 / dt / 1e6, 5000.0))
        self._last_recv, self._last_sent, self._last_t = recv, sent, now

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _append_csv(SPEED_CSV, SPEED_FIELDS, [{
            "ts": ts, "down_mbps": round(down, 3), "up_mbps": round(up, 3),
            "bytes_recv": recv, "bytes_sent": sent,
            "connection": net["connection"], "vpn": net["vpn"],
            "tod": net["tod"], "label": net["label"],
            "public_ip": net.get("public_ip") or "", "iface": iface,
        }])

        conns = _connections_fast()
        new_rows = []
        current_keys: set[tuple] = set()
        conn_by_app: dict[str, int] = {}
        hosts_by_app: dict[str, list[str]] = {}

        for c in conns:
            rip  = c["remote_ip"]
            key  = (rip, c["remote_port"], c["local_port"], c["status"], c["pid"])
            current_keys.add(key)

            # enqueue geo lookup (non-blocking)
            geo.enqueue(rip)
            self.hosts.prefetch(rip)

            host = self.hosts.get(rip) or ""
            proc = c.get("process") or "unknown"
            conn_by_app[proc] = conn_by_app.get(proc, 0) + 1
            if host:
                lst = hosts_by_app.setdefault(proc, [])
                if host not in lst:
                    lst.append(host)

            if key not in self._seen and c["status"] in ("ESTABLISHED", "SYN_SENT"):
                self._seen.add(key)
                g = geo.get(rip) or {}
                new_rows.append({
                    "ts": ts, "event": "connect",
                    "remote_ip": rip, "remote_port": c["remote_port"],
                    "local_port": c["local_port"], "status": c["status"],
                    "pid": c["pid"], "process": proc, "hostname": host,
                    "connection": net["connection"], "vpn": net["vpn"],
                    "tod": net["tod"], "label": net["label"],
                    "public_ip": net.get("public_ip") or "",
                    "ssid": net.get("ssid") or "",
                    "geo_lat":          g.get("lat", ""),
                    "geo_lon":          g.get("lon", ""),
                    "geo_country":      g.get("country", ""),
                    "geo_country_code": g.get("country_code", ""),
                    "geo_city":         g.get("city", ""),
                    "geo_region":       g.get("region", ""),
                    "geo_isp":          g.get("isp", ""),
                    "geo_org":          g.get("org", ""),
                    "geo_asn":          g.get("asn", ""),
                })

        self._seen &= current_keys
        if len(self._seen) > 4000:
            self._seen = set(list(self._seen)[-2000:])

        _append_csv(TRAFFIC_CSV, TRAFFIC_FIELDS, new_rows)

        with self._nettop_lock:
            nb = dict(self._nettop_cache)

        apps = []
        for name in set(conn_by_app) | set(nb):
            din, dout = self.session.delta(name)
            total = din + dout
            conns_n = conn_by_app.get(name, 0)
            if total < 512 and conns_n == 0:
                continue
            apps.append({
                "name": name,
                "bytes_in": din, "bytes_out": dout,
                "total_bytes": total,
                "total_fmt": fmt_bytes(total),
                "conns": conns_n,
                "top_hosts": hosts_by_app.get(name, [])[:4],
            })
        apps.sort(key=lambda a: a["total_bytes"], reverse=True)

        total_session = sum(a["total_bytes"] for a in apps)

        # throttle score for this moment
        from throttle_engine import score_current
        ts_score = score_current(down, up, net.get("label", ""), _live_geo(conns))

        self.latest = {
            "down_mbps":          round(down, 2),
            "up_mbps":            round(up, 2),
            "active_count":       len(conns),
            "apps":               apps[:25],
            "session_total_bytes": total_session,
            "session_total_fmt":  fmt_bytes(total_session),
            "net":                net,
            "iface":              iface,
            "ts":                 ts,
            "throttle_score":     ts_score,
            # live geo: only IPs with real lat/lng
            "live_geo":           _live_geo(conns),
        }
        if self.on_tick:
            try:
                self.on_tick(self.latest)
            except Exception:
                pass


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--seconds",  type=float, default=0)
    args = p.parse_args()

    mon = TrafficMonitor(interval=args.interval)
    def show(s):
        n = s["net"]
        sc = s.get("throttle_score", {})
        print(f"\r↓{s['down_mbps']:5.2f} ↑{s['up_mbps']:5.2f} Mbps "
              f"conns={s['active_count']:3d} score={sc.get('score',0):3d} "
              f"{n.get('label')}   ", end="", flush=True)
    mon.on_tick = show
    mon.start()
    try:
        deadline = time.time() + args.seconds if args.seconds > 0 else float("inf")
        while time.time() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        mon.stop()


if __name__ == "__main__":
    main()
