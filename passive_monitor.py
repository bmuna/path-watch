#!/usr/bin/env python3
"""
Passive traffic watcher for this machine.

Does NOT visit YouTube/Facebook/etc. It watches connections YOUR apps already
make (browser, telegram, downloads…) and records timing + interface speeds.

Metadata only: remote IP/port, process name, hostname if reverse-DNS works,
upload/download Mbps on the active interface. No payloads.
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
from app_usage import SessionUsage, fmt_bytes
from network_status import snapshot

TRAFFIC_CSV = getattr(cfg, "TRAFFIC_CSV", "traffic_log.csv")
SPEED_CSV = getattr(cfg, "SPEED_CSV", "speed_log.csv")

TRAFFIC_FIELDS = [
    "ts",
    "event",
    "remote_ip",
    "remote_port",
    "local_port",
    "status",
    "pid",
    "process",
    "hostname",
    "connection",
    "vpn",
    "tod",
    "label",
    "public_ip",
    "ssid",
]

SPEED_FIELDS = [
    "ts",
    "down_mbps",
    "up_mbps",
    "bytes_recv",
    "bytes_sent",
    "connection",
    "vpn",
    "tod",
    "label",
    "public_ip",
    "iface",
]


def _ensure(path: str, fields: list[str]):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()


def _append(path: str, fields: list[str], rows: list[dict]):
    if not rows:
        return
    _ensure(path, fields)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


class HostCache:
    """Reverse-DNS with a small LRU so we don't stall the sampler."""

    def __init__(self, maxsize: int = 512):
        self.maxsize = maxsize
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rdns")

    def get(self, ip: str) -> str:
        with self._lock:
            if ip in self._cache:
                self._cache.move_to_end(ip)
                return self._cache[ip]
        return ""

    def prefetch(self, ip: str):
        if not ip or self.get(ip):
            return

        def work():
            try:
                name = socket.gethostbyaddr(ip)[0]
            except Exception:
                name = ""
            with self._lock:
                self._cache[ip] = name
                self._cache.move_to_end(ip)
                while len(self._cache) > self.maxsize:
                    self._cache.popitem(last=False)

        self._pool.submit(work)


def _proc_name(pid: int | None) -> str:
    if not pid:
        return ""
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _iface_counters():
    """Pick the busiest non-loopback NIC (or sum of all)."""
    per = psutil.net_io_counters(pernic=True)
    best_name, best = None, None
    best_total = -1
    for name, c in per.items():
        if name.startswith(("lo", "awdl", "llw", "utun", "bridge", "ap")):
            continue
        total = c.bytes_recv + c.bytes_sent
        if total > best_total:
            best_total = total
            best_name, best = name, c
    if best is None:
        all_c = psutil.net_io_counters()
        return "all", all_c.bytes_recv, all_c.bytes_sent
    return best_name, best.bytes_recv, best.bytes_sent



def _conn_key(c) -> tuple | None:
    if not c.raddr:
        return None
    rip = c.raddr.ip
    if rip in ("127.0.0.1", "::1") or rip.startswith("127."):
        return None
    if rip.startswith("169.254.") or rip.startswith("fe80:"):
        return None
    return (rip, int(c.raddr.port), int(c.laddr.port) if c.laddr else 0, c.status, c.pid)


def _list_connections() -> list[dict]:
    """Return active inet connections. Prefer psutil; on macOS fall back to lsof."""
    rows = []
    try:
        for c in psutil.net_connections(kind="inet"):
            key = _conn_key(c)
            if key is None:
                continue
            rip, rport, lport, status, pid = key
            rows.append(
                {
                    "remote_ip": rip,
                    "remote_port": rport,
                    "local_port": lport,
                    "status": status,
                    "pid": pid,
                    "process": _proc_name(pid),
                }
            )
    except (psutil.AccessDenied, PermissionError):
        rows = []

    if rows:
        return rows

    # macOS without root: lsof still shows this user's sockets
    if platform.system() == "Darwin":
        return _lsof_connections()
    return rows


def _lsof_connections() -> list[dict]:
    try:
        p = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    out = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        proc, pid = parts[0], parts[1]
        name = next((p for p in parts if "->" in p), "")
        if not name:
            continue
        left, right = name.split("->", 1)
        if ":" not in right:
            continue
        rip, rport_s = right.rsplit(":", 1)
        rip = rip.strip("[]")
        try:
            rport = int(rport_s)
            lport = int(left.rsplit(":", 1)[-1]) if ":" in left else 0
            pid_i = int(pid)
        except ValueError:
            continue
        if rip in ("127.0.0.1", "::1") or rip.startswith("127."):
            continue
        out.append(
            {
                "remote_ip": rip,
                "remote_port": rport,
                "local_port": lport,
                "status": "ESTABLISHED",
                "pid": pid_i,
                "process": proc,
            }
        )
    return out


def _conn_key_from_row(row: dict) -> tuple | None:
    rip = row.get("remote_ip")
    if not rip or rip in ("127.0.0.1", "::1") or str(rip).startswith("127."):
        return None
    if str(rip).startswith("169.254.") or str(rip).startswith("fe80:"):
        return None
    return (
        rip,
        int(row.get("remote_port") or 0),
        int(row.get("local_port") or 0),
        row.get("status") or "",
        row.get("pid"),
    )


class TrafficMonitor:
    def __init__(
        self,
        traffic_csv: str = TRAFFIC_CSV,
        speed_csv: str = SPEED_CSV,
        interval: float = 2.0,
        on_tick: Callable | None = None,
    ):
        self.traffic_csv = traffic_csv
        self.speed_csv = speed_csv
        self.interval = interval
        self.on_tick = on_tick
        self.hosts = HostCache()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: set[tuple] = set()
        self._last_recv = 0
        self._last_sent = 0
        self._last_t = 0.0
        self.session = SessionUsage()
        self.latest = {
            "down_mbps": 0.0,
            "up_mbps": 0.0,
            "active": [],
            "net": {},
            "new_events": [],
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        iface, recv, sent = _iface_counters()
        self._last_recv, self._last_sent = recv, sent
        self._last_t = time.time()
        self._thread = threading.Thread(target=self._loop, name="traffic-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception as e:
                self.latest["error"] = str(e)
            self._stop.wait(self.interval)

    def _sample(self):
        net = snapshot()
        now = time.time()
        iface, recv, sent = _iface_counters()
        dt = max(now - self._last_t, 1e-6)
        down = (recv - self._last_recv) * 8 / dt / 1e6
        up = (sent - self._last_sent) * 8 / dt / 1e6
        # clamp noise / counter resets
        if down < 0 or down > 5000:
            down = 0.0
        if up < 0 or up > 5000:
            up = 0.0
        self._last_recv, self._last_sent, self._last_t = recv, sent, now

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        speed_row = {
            "ts": ts,
            "down_mbps": round(down, 3),
            "up_mbps": round(up, 3),
            "bytes_recv": recv,
            "bytes_sent": sent,
            "connection": net["connection"],
            "vpn": net["vpn"],
            "tod": net["tod"],
            "label": net["label"],
            "public_ip": net.get("public_ip") or "",
            "iface": iface,
        }
        _append(self.speed_csv, SPEED_FIELDS, [speed_row])

        active = []
        new_rows = []
        current_keys = set()
        for c in _list_connections():
            key = _conn_key_from_row(c)
            if key is None:
                continue
            current_keys.add(key)
            rip, rport, lport, status, pid = key
            self.hosts.prefetch(rip)
            host = self.hosts.get(rip) or ""
            proc = c.get("process") or _proc_name(pid)
            item = {
                "remote_ip": rip,
                "remote_port": rport,
                "local_port": lport,
                "status": status,
                "pid": pid or "",
                "process": proc,
                "hostname": host,
            }
            active.append(item)
            if key not in self._seen and status in ("ESTABLISHED", "SYN_SENT"):
                self._seen.add(key)
                row = {
                    "ts": ts,
                    "event": "connect",
                    "remote_ip": rip,
                    "remote_port": rport,
                    "local_port": lport,
                    "status": status,
                    "pid": pid or "",
                    "process": proc,
                    "hostname": host,
                    "connection": net["connection"],
                    "vpn": net["vpn"],
                    "tod": net["tod"],
                    "label": net["label"],
                    "public_ip": net.get("public_ip") or "",
                    "ssid": net.get("ssid") or "",
                }
                new_rows.append(row)

        # forget closed sockets so reconnects log again
        self._seen &= current_keys
        # bound memory
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])

        _append(self.traffic_csv, TRAFFIC_FIELDS, new_rows)

        # group connections by app for UI + session usage
        conn_counts: dict[str, int] = {}
        hosts_by_proc: dict[str, list[str]] = {}
        for item in active:
            proc = item.get("process") or "unknown"
            from app_usage import pretty_name

            proc = pretty_name(proc)
            conn_counts[proc] = conn_counts.get(proc, 0) + 1
            host = item.get("hostname") or item.get("remote_ip") or ""
            if host and host not in hosts_by_proc.get(proc, []):
                hosts_by_proc.setdefault(proc, []).append(host)

        apps = self.session.tick(conn_counts, hosts_by_proc)
        total_session = sum(a.total_bytes for a in apps)

        active.sort(key=lambda x: (x["process"] or "", x["remote_ip"], x["remote_port"]))
        self.latest = {
            "down_mbps": round(down, 2),
            "up_mbps": round(up, 2),
            "active": active[:200],
            "active_count": len(active),
            "apps": [
                {
                    "name": a.name,
                    "bytes_in": a.bytes_in,
                    "bytes_out": a.bytes_out,
                    "total_bytes": a.total_bytes,
                    "total_fmt": fmt_bytes(a.total_bytes),
                    "conns": a.conns,
                    "top_hosts": a.top_hosts,
                }
                for a in apps[:30]
            ],
            "session_total_bytes": total_session,
            "session_total_fmt": fmt_bytes(total_session),
            "net": net,
            "new_events": new_rows,
            "iface": iface,
            "ts": ts,
        }
        if self.on_tick:
            try:
                self.on_tick(self.latest)
            except Exception:
                pass


def main():
    import argparse

    p = argparse.ArgumentParser(description="watch this machine's connections (no site probing)")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--seconds", type=float, default=0, help="0 = until ctrl-c")
    args = p.parse_args()

    mon = TrafficMonitor(interval=args.interval)

    def show(state):
        n = state["net"]
        print(
            f"\r↓ {state['down_mbps']:6.2f} Mbps  ↑ {state['up_mbps']:6.2f} Mbps  "
            f"conns={state.get('active_count', 0):3d}  "
            f"{n.get('label')}  ip={n.get('public_ip')}   ",
            end="",
            flush=True,
        )

    mon.on_tick = show
    print("watching local connections (ctrl-c to stop). not probing any websites.")
    mon.start()
    try:
        if args.seconds > 0:
            time.sleep(args.seconds)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        mon.stop()


if __name__ == "__main__":
    main()
