#!/usr/bin/env python3
"""Per-app network usage via macOS nettop (no root)."""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class AppUsage:
    name: str
    pid: int
    bytes_in: int = 0
    bytes_out: int = 0
    conns: int = 0
    top_hosts: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return self.bytes_in + self.bytes_out


def _parse_nettop_line(line: str) -> tuple[str, int, int, int] | None:
    line = line.strip()
    if not line or line.startswith(",") or line.startswith("time"):
        return None
    parts = line.split(",")
    if len(parts) < 3:
        return None
    proc_field = parts[0].strip()
    try:
        bytes_in = int(parts[1].strip() or 0)
        bytes_out = int(parts[2].strip() or 0)
    except ValueError:
        return None
    if "." not in proc_field:
        return None
    name, pid_s = proc_field.rsplit(".", 1)
    try:
        pid = int(pid_s)
    except ValueError:
        return None
    name = pretty_name(name)
    return name, pid, bytes_in, bytes_out


def pretty_name(raw: str) -> str:
    raw = raw.strip()
    for suffix in (" Helper (Renderer)", " Helper (GPU)", " Helper", " Helper)"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    if raw.endswith(")"):
        raw = raw.split("(")[0].strip()
    return raw or "unknown"


def nettop_snapshot() -> dict[tuple[str, int], tuple[int, int]]:
    """Return {(display_name, pid): (bytes_in, bytes_out)} since process start (nettop counters)."""
    if platform.system() != "Darwin":
        return {}
    try:
        p = subprocess.run(
            ["nettop", "-P", "-L", "1", "-s", "1", "-J", "bytes_in,bytes_out"],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return {}
    out: dict[tuple[str, int], tuple[int, int]] = {}
    for line in p.stdout.splitlines():
        parsed = _parse_nettop_line(line)
        if not parsed:
            continue
        name, pid, bi, bo = parsed
        key = (name, pid)
        # nettop can repeat; keep max (counters are cumulative)
        prev = out.get(key, (0, 0))
        out[key] = (max(prev[0], bi), max(prev[1], bo))
    return out


class SessionUsage:
    """Track how much each app moved since the monitor started (or last reset)."""

    def __init__(self):
        self._baseline: dict[tuple[str, int], tuple[int, int]] = {}
        self._started = False

    def reset(self):
        self._baseline = nettop_snapshot()
        self._started = True

    def tick(self, conn_counts: dict[str, int], host_by_proc: dict[str, list[str]]) -> list[AppUsage]:
        cur = nettop_snapshot()
        if not self._started:
            self._baseline = dict(cur)
            self._started = True
            return []

        # merge rows that share the same display name (multiple pids)
        merged: dict[str, AppUsage] = {}
        for (name, pid), (bi, bo) in cur.items():
            base_bi, base_bo = self._baseline.get((name, pid), (bi, bo))
            din = max(0, bi - base_bi)
            dout = max(0, bo - base_bo)
            if name not in merged:
                merged[name] = AppUsage(name=name, pid=pid)
            u = merged[name]
            u.bytes_in += din
            u.bytes_out += dout
            u.conns = max(u.conns, conn_counts.get(name, 0))
            hosts = host_by_proc.get(name, [])
            u.top_hosts = hosts[:4]

        apps = sorted(merged.values(), key=lambda a: a.total_bytes, reverse=True)
        # drop noise under 1 KB unless it has connections
        return [a for a in apps if a.total_bytes >= 1024 or a.conns > 0]


def fmt_bytes(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1000:
        return f"{n / 1000:.1f} KB"
    return f"{n} B"
