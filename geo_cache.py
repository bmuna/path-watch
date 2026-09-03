#!/usr/bin/env python3
"""
IP geolocation cache. Uses ip-api.com batch endpoint (free, 100 req/min).
Runs off the main thread so it never blocks the monitor.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import OrderedDict

GEO_URL = "http://ip-api.com/batch?fields=status,country,countryCode,regionName,city,lat,lon,isp,org,as,query"
PRIVATE_PREFIXES = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.", "192.168.", "127.", "169.254.", "::1", "fc", "fd",
)

def is_private(ip: str) -> bool:
    return any(ip.startswith(p) for p in PRIVATE_PREFIXES)


class GeoCache:
    """Thread-safe LRU geo cache. Batches pending lookups every 3 s."""

    def __init__(self, maxsize: int = 2048):
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._last_batch = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="geo-cache")
        self._thread.start()

    def enqueue(self, ip: str):
        if is_private(ip):
            return
        with self._lock:
            if ip not in self._cache:
                self._pending.add(ip)

    def get(self, ip: str) -> dict | None:
        with self._lock:
            v = self._cache.get(ip)
            if v:
                self._cache.move_to_end(ip)
            return v

    def all_known(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._cache)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(3.0)
            with self._lock:
                batch = list(self._pending)[:100]
                self._pending -= set(batch)
            if not batch:
                continue
            try:
                self._fetch(batch)
            except Exception:
                pass

    def _fetch(self, ips: list[str]):
        body = json.dumps([{"query": ip} for ip in ips]).encode()
        req = urllib.request.Request(
            GEO_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            results = json.loads(resp.read())
        with self._lock:
            for r in results:
                ip = r.get("query")
                if not ip or r.get("status") != "success":
                    continue
                entry = {
                    "ip": ip,
                    "country": r.get("country", ""),
                    "country_code": r.get("countryCode", ""),
                    "region": r.get("regionName", ""),
                    "city": r.get("city", ""),
                    "lat": r.get("lat"),
                    "lon": r.get("lon"),
                    "isp": r.get("isp", ""),
                    "org": r.get("org", ""),
                    "asn": r.get("as", ""),
                }
                self._cache[ip] = entry
                self._cache.move_to_end(ip)
                while len(self._cache) > self._maxsize:
                    self._cache.popitem(last=False)


# module-level singleton
geo = GeoCache()
