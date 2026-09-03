#!/usr/bin/env python3
"""Figure out what network this machine is on right now.

Used by the collector (--auto) and the dashboard. Best-effort heuristics —
not magic. Override manually if it guesses wrong.
"""

from __future__ import annotations

import json
import platform
import re
import socket
import subprocess
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import config as cfg


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def time_of_day(now: datetime | None = None) -> str:
    tz = ZoneInfo(getattr(cfg, "VANTAGE_TZ", "UTC"))
    now = now or datetime.now(tz)
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def detect_vpn() -> tuple[str, str]:
    """Return ('vpn'|'novpn', reason)."""
    system = platform.system()

    if system == "Darwin":
        # Connected entries in Network Extension / scutil
        sc = _run(["scutil", "--nc", "list"])
        for line in sc.splitlines():
            if "(Connected)" in line or "Connected" in line and "PPP" in line:
                return "vpn", "scutil shows a connected VPN service"
        # utun interfaces with an inet address are the usual WireGuard/OpenVPN tell
        ifc = _run(["ifconfig"])
        for block in re.split(r"\n(?=\w)", ifc):
            if not block.startswith("utun"):
                continue
            if re.search(r"\binet\s+\d", block):
                name = block.split(":", 1)[0]
                return "vpn", f"{name} has an address"
        # common VPN apps running
        ps = _run(["ps", "-A", "-o", "comm="])
        for needle in ("openvpn", "wireguard", "wg-quick", "nordvpn", "expressvpn", "surfshark", "mullvad", "cloudflared"):
            if needle.lower() in ps.lower():
                return "vpn", f"process looks like {needle}"
        return "novpn", "no utun address / connected scutil VPN"

    # linux: tun/wg interfaces
    ip_out = _run(["ip", "-br", "addr"])
    for line in ip_out.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.startswith(("tun", "wg", "ppp")) and "UP" in line:
            return "vpn", f"{name} is up"
    return "novpn", "no tun/wg interface up"


def _wifi_ssid() -> str | None:
    if platform.system() != "Darwin":
        return None
    # modern macos
    out = _run(["ipconfig", "getsummary", "en0"])
    m = re.search(r"SSID\s*:\s*(.+)", out)
    if m:
        return m.group(1).strip()
    out = _run(["networksetup", "-getairportnetwork", "en0"])
    if "Current Wi-Fi Network:" in out:
        return out.split("Current Wi-Fi Network:", 1)[1].strip()
    return None


def _default_iface() -> str | None:
    if platform.system() == "Darwin":
        out = _run(["route", "-n", "get", "default"])
        m = re.search(r"interface:\s*(\S+)", out)
        return m.group(1) if m else None
    out = _run(["ip", "route", "show", "default"])
    m = re.search(r"dev\s+(\S+)", out)
    return m.group(1) if m else None


def detect_connection() -> tuple[str, str]:
    """Return ('wifi'|'hotspot'|'ethernet', reason)."""
    iface = _default_iface() or ""
    ssid = _wifi_ssid()

    # ethernet-ish names
    if iface.startswith("en") and platform.system() == "Darwin":
        # en0 is usually wifi on laptops; en1+ often thunderbolt/ethernet
        # if we have an SSID on the default iface path, call it wifi/hotspot
        pass

    if ssid:
        low = ssid.lower()
        hotspot_hints = (
            "iphone",
            "android",
            "hotspot",
            "galaxy",
            "pixel",
            "oneplus",
            "personal hotspot",
        )
        if any(h in low for h in hotspot_hints):
            return "hotspot", f"wifi SSID looks like a phone hotspot ({ssid})"
        return "wifi", f"wifi SSID={ssid}"

    if iface.startswith(("eth", "en")) and not ssid:
        # no SSID — likely wired, map to wifi bucket? keep ethernet as wifi for labels
        # our labels only allow wifi|hotspot — treat wired as wifi (home lan)
        if iface and not iface.startswith("en0"):
            return "wifi", f"wired/default iface {iface} (logged as wifi)"
        return "wifi", f"default iface {iface or 'unknown'}, no SSID"

    if iface.startswith(("wwan", "pdp", "rmnet", "cellular")):
        return "hotspot", f"cellular iface {iface}"

    return "wifi", f"default iface {iface or 'unknown'}"


def public_ip(timeout: float = 3.0) -> dict:
    """Best-effort public IP + org via Cloudflare trace (no API key)."""
    info = {"ip": None, "loc": None, "colo": None, "org": None}
    try:
        req = urllib.request.Request(
            "https://cloudflare.com/cdn-cgi/trace",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k == "ip":
                info["ip"] = v
            elif k == "loc":
                info["loc"] = v
            elif k == "colo":
                info["colo"] = v
    except Exception:
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as resp:
                info["ip"] = resp.read().decode().strip()
        except Exception:
            pass
    return info


def snapshot() -> dict:
    """Everything useful about 'right now' on this machine."""
    vpn, vpn_why = detect_vpn()
    conn, conn_why = detect_connection()
    tod = time_of_day()
    pub = public_ip()
    ssid = _wifi_ssid()
    iface = _default_iface()
    label = f"{conn}_{vpn}_{tod}"
    return {
        "connection": conn,
        "vpn": vpn,
        "tod": tod,
        "label": label,
        "ssid": ssid,
        "iface": iface,
        "vpn_reason": vpn_why,
        "connection_reason": conn_why,
        "public_ip": pub.get("ip"),
        "ip_country": pub.get("loc"),
        "cf_colo": pub.get("colo"),
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "vantage_city": getattr(cfg, "VANTAGE_CITY", ""),
        "tz": getattr(cfg, "VANTAGE_TZ", "UTC"),
        "detected_at": datetime.now(ZoneInfo(getattr(cfg, "VANTAGE_TZ", "UTC"))).isoformat(timespec="seconds"),
    }


def main():
    print(json.dumps(snapshot(), indent=2))


if __name__ == "__main__":
    main()
