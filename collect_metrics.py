#!/usr/bin/env python3
"""
    python collect_metrics.py --auto --watch

Ping every target each round (tcp connect if icmp is blocked). Every few
rounds, pull 1MB and record Mbps. Timing only, no payloads.

--auto detects wifi/hotspot + vpn + time-of-day from this machine.
--watch keeps logging until you hit ctrl-c.
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import config as cfg

CSV_FIELDS = [
    "ts",
    "run_id",
    "round_idx",
    "elapsed_s",
    "label",
    "connection",
    "vpn",
    "tod",
    "note",
    "target",
    "role",
    "rtt_min",
    "rtt_avg",
    "rtt_max",
    "jitter",
    "loss_pct",
    "n_sent",
    "n_recv",
    "method",
    "throughput_mbps",
]


TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.I)
LOSS_RE = re.compile(r"([\d.]+)%\s+packet loss", re.I)
# macos: min/avg/max/stddev   linux: min/avg/max/mdev
SUMMARY_RE = re.compile(
    r"min/avg/max/(?:stddev|mdev)\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
    re.I,
)


def all_targets():
    out = [(h, "baseline") for h in cfg.BASELINE_TARGETS]
    out += [(h, "suspect") for h in cfg.SUSPECT_TARGETS]
    return out


def ping_cmd(host: str) -> list[str]:
    # -W is milliseconds on darwin, seconds on linux. easy to mix up.
    if platform.system() == "Darwin":
        return ["ping", "-c", str(cfg.PING_COUNT), "-W", str(cfg.PING_TIMEOUT_MS), host]
    return ["ping", "-c", str(cfg.PING_COUNT), "-W", str(max(1, cfg.PING_TIMEOUT_MS // 1000)), host]


def parse_ping(text: str) -> dict | None:
    times = [float(x) for x in TIME_RE.findall(text)]
    loss_m = LOSS_RE.search(text)
    if loss_m is None:
        return None
    loss = float(loss_m.group(1))
    sent_m = re.search(r"(\d+)\s+packets transmitted", text)
    recv_m = re.search(r"(\d+)\s+(?:packets received|received)", text)
    sent = int(sent_m.group(1)) if sent_m else cfg.PING_COUNT
    recv = int(recv_m.group(1)) if recv_m else len(times)

    summary = SUMMARY_RE.search(text)
    if summary:
        rmin, ravg, rmax, jitter = (float(summary.group(i)) for i in range(1, 5))
    elif times:
        rmin, ravg, rmax = min(times), statistics.mean(times), max(times)
        jitter = statistics.pstdev(times) if len(times) > 1 else 0.0
    else:
        return {
            "rtt_min": "",
            "rtt_avg": "",
            "rtt_max": "",
            "jitter": "",
            "loss_pct": loss,
            "n_sent": sent,
            "n_recv": recv,
            "method": "icmp",
        }

    return {
        "rtt_min": round(rmin, 3),
        "rtt_avg": round(ravg, 3),
        "rtt_max": round(rmax, 3),
        "jitter": round(jitter, 3),
        "loss_pct": loss,
        "n_sent": sent,
        "n_recv": recv,
        "method": "icmp",
    }


def run_ping(host: str) -> dict | None:
    try:
        proc = subprocess.run(
            ping_cmd(host),
            capture_output=True,
            text=True,
            timeout=cfg.PING_COUNT * 3 + 5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ping failed for {host}: {e}")
        return None
    # ping returns 1 or 2 on loss depending on OS; still parse stdout
    parsed = parse_ping(proc.stdout + "\n" + proc.stderr)
    return parsed


def tcp_connect_once(host: str, port: int, timeout: float = 3.0) -> float | None:
    sock = None
    try:
        ip = socket.gethostbyname(host)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        sock.connect((ip, port))
        return (time.perf_counter() - t0) * 1000.0
    except OSError:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def run_tcp(host: str) -> dict:
    port = cfg.TCP_PORT.get(host, cfg.DEFAULT_TCP_PORT)
    rtts = []
    for _ in range(cfg.PING_COUNT):
        r = tcp_connect_once(host, port)
        if r is not None:
            rtts.append(r)
    n_recv = len(rtts)
    loss = 100.0 * (cfg.PING_COUNT - n_recv) / cfg.PING_COUNT
    if not rtts:
        return {
            "rtt_min": "",
            "rtt_avg": "",
            "rtt_max": "",
            "jitter": "",
            "loss_pct": loss,
            "n_sent": cfg.PING_COUNT,
            "n_recv": 0,
            "method": f"tcp:{port}",
        }
    return {
        "rtt_min": round(min(rtts), 3),
        "rtt_avg": round(statistics.mean(rtts), 3),
        "rtt_max": round(max(rtts), 3),
        "jitter": round(statistics.pstdev(rtts) if len(rtts) > 1 else 0.0, 3),
        "loss_pct": loss,
        "n_sent": cfg.PING_COUNT,
        "n_recv": n_recv,
        "method": f"tcp:{port}",
    }


def probe_host(host: str) -> dict:
    result = run_ping(host)
    if result is None or result["n_recv"] == 0:
        tcp = run_tcp(host)
        if result is not None and result["n_recv"] == 0:
            tcp["method"] = tcp["method"] + "+icmp_loss"
        return tcp
    return result


def measure_throughput(url: str = cfg.THROUGHPUT_URL, timeout: int = 40) -> float | None:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  throughput sample failed: {e}")
        return None
    dt = time.perf_counter() - t0
    if dt <= 0 or not blob:
        return None
    mbps = (len(blob) * 8) / dt / 1e6
    return round(mbps, 3)


def ensure_csv(path: str):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_rows(path: str, rows: list[dict]):
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for row in rows:
            w.writerow(row)


def empty_metric():
    return {k: "" for k in CSV_FIELDS}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--auto", action="store_true", help="detect wifi/hotspot + vpn + time-of-day each round")
    p.add_argument("--connection", choices=cfg.CONNECTION_CHOICES)
    p.add_argument("--vpn", choices=cfg.VPN_CHOICES)
    p.add_argument("--tod", choices=cfg.TOD_CHOICES)
    p.add_argument("--minutes", type=float, default=25, help="how long to run (default 25; ignored with --watch)")
    p.add_argument("--watch", action="store_true", help="keep going until ctrl-c (good with --auto)")
    p.add_argument("--interval", type=float, default=12, help="seconds between rounds (default 12)")
    p.add_argument("--csv", default=cfg.CSV_FILE)
    p.add_argument("--note", default="", help="free-text, e.g. 'other people on netflix'")
    p.add_argument("--once", action="store_true", help="one round then exit (sanity check)")
    p.add_argument("--no-throughput", action="store_true")
    args = p.parse_args()
    if not args.auto:
        missing = [k for k in ("connection", "vpn", "tod") if getattr(args, k) is None]
        if missing:
            p.error("need --connection/--vpn/--tod, or pass --auto")
    return args


def resolve_condition(args):
    """Manual labels, or sniff the machine."""
    if not args.auto:
        label = f"{args.connection}_{args.vpn}_{args.tod}"
        return {
            "connection": args.connection,
            "vpn": args.vpn,
            "tod": args.tod,
            "label": label,
            "note": args.note,
            "meta": None,
        }
    from network_status import snapshot

    snap = snapshot()
    note = args.note
    bits = []
    if snap.get("ssid"):
        bits.append(f"ssid={snap['ssid']}")
    if snap.get("public_ip"):
        bits.append(f"ip={snap['public_ip']}")
    if snap.get("vpn_reason"):
        bits.append(snap["vpn_reason"])
    auto_note = "; ".join(bits)
    if note:
        note = f"{note} | {auto_note}"
    else:
        note = auto_note
    return {
        "connection": snap["connection"],
        "vpn": snap["vpn"],
        "tod": snap["tod"],
        "label": snap["label"],
        "note": note,
        "meta": snap,
    }


def main():
    args = parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    ensure_csv(args.csv)

    duration = 0 if (args.once or args.watch) else args.minutes * 60
    t_start = time.time()
    round_idx = 0
    last_label = None

    print(f"run_id={run_id}")
    print(f"csv={args.csv}")
    print(f"targets: {', '.join(h for h, _ in all_targets())}")
    if args.auto:
        print("mode=auto (re-detect connection/vpn/tod each round)")
    if args.once:
        print("single round")
    elif args.watch:
        print(f"watching forever, interval ~{args.interval}s (ctrl-c to stop)")
    else:
        print(f"running for {args.minutes} min, interval ~{args.interval}s")
    print()

    try:
        while True:
            cond = resolve_condition(args)
            if cond["label"] != last_label:
                print(f"label={cond['label']}")
                if cond.get("meta"):
                    m = cond["meta"]
                    print(
                        f"  detected: {m.get('connection_reason')} | vpn={m.get('vpn')} "
                        f"({m.get('vpn_reason')}) | ip={m.get('public_ip')}"
                    )
                last_label = cond["label"]

            round_idx += 1
            elapsed = time.time() - t_start
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rows = []

            for host, role in all_targets():
                m = probe_host(host)
                row = empty_metric()
                row.update(
                    {
                        "ts": ts,
                        "run_id": run_id,
                        "round_idx": round_idx,
                        "elapsed_s": round(elapsed, 1),
                        "label": cond["label"],
                        "connection": cond["connection"],
                        "vpn": cond["vpn"],
                        "tod": cond["tod"],
                        "note": cond["note"],
                        "target": host,
                        "role": role,
                        "throughput_mbps": "",
                    }
                )
                row.update(m)
                rows.append(row)
                avg = m.get("rtt_avg", "")
                print(f"  [{round_idx}] {host:22}  rtt={avg!s:>8}  loss={m['loss_pct']}%  {m['method']}")

            do_tp = (not args.no_throughput) and (round_idx % cfg.THROUGHPUT_EVERY == 1)
            if do_tp:
                mbps = measure_throughput()
                row = empty_metric()
                row.update(
                    {
                        "ts": ts,
                        "run_id": run_id,
                        "round_idx": round_idx,
                        "elapsed_s": round(elapsed, 1),
                        "label": cond["label"],
                        "connection": cond["connection"],
                        "vpn": cond["vpn"],
                        "tod": cond["tod"],
                        "note": cond["note"],
                        "target": "cloudflare_1mb",
                        "role": "throughput",
                        "method": "http_get",
                        "throughput_mbps": mbps if mbps is not None else "",
                        "n_sent": 1,
                        "n_recv": 1 if mbps is not None else 0,
                        "loss_pct": 0 if mbps is not None else 100,
                    }
                )
                rows.append(row)
                print(f"  [{round_idx}] throughput              {mbps} Mbps")

            append_rows(args.csv, rows)

            if args.once:
                break
            if not args.watch and duration and time.time() - t_start >= duration:
                break

            spent = time.time() - t_start - elapsed
            sleep_for = args.interval - spent
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nstopped.")

    print(f"done. {round_idx} rounds written to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
