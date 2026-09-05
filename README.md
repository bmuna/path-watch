# Path Watch

https://github.com/bmuna/path-watch

Personal ISP path monitor for a home network in Addis Ababa.

Watches connections **this machine already makes** (browser, apps, downloads).
Does **not** probe specific sites. Metadata only: remote IPs, live up/down speed,
wifi vs hotspot, VPN on/off, time of day. No payloads, no bypass.
Timing gaps are **not** proof of ISP intent.

## Architecture (current)

Passive FastAPI backend + React frontend. A gradient-boosting model trains on
`speed_log.csv` (time, wifi/hotspot, VPN, destinations) and drives the live
score plus the map heat. No Streamlit, no site probing.

| Piece | Role |
|-------|------|
| `server.py` | FastAPI + WebSocket `/ws/live` + `/api/analysis` |
| `passive_monitor.py` | Speeds + sockets via psutil → CSV logs |
| `geo_cache.py` | IP → lat/lng (ip-api.com batch) |
| `model.py` / `train_model.py` | HistGradientBoosting on the logs → score + map heat |
| `throttle_engine.py` | Stats + wires the trained model into `/api/analysis` |
| `network_status.py` | wifi/hotspot, VPN, TOD, public IP |
| `frontend/` | React UI (monitor / map / analysis / apps) |

`model.py` trains two heads on the logs: expected Mbps, and P(throttled)
from VPN / time-of-day / link contrast labels. The map paints that
probability — Addis for the local path, world for destinations.

```
source .venv/bin/activate
python train_model.py
python -m unittest tests.test_model -v
```

If `python3` is the system 3.9, use `.venv/bin/python` instead.

Logs (gitignored): `speed_log.csv`, `traffic_log.csv`.  
Legacy `metrics_log.csv` may exist from an older ping pilot; it is not what
the live app analyzes.

## Run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
./run.sh
```

Open **http://localhost:5173**

Leave the monitor running. Use the internet normally. For contrast, switch
VPN on/off and wifi ↔ hotspot while it logs.

## Findings so far (passive `speed_log.csv`, one evening in Addis)

Model + stats on the same logs. Timing is not proof of ISP intent.

**VPN-on vs off (wifi, evening — same TOD bucket)**

| Condition | n | median ↓ Mbps | mean ↓ Mbps |
|-----------|---|---------------|-------------|
| `wifi_novpn_evening` | 3812 | 0.091 | 1.027 |
| `wifi_vpn_evening` | 1091 | 0.283 | 0.540 |

Median download is ~3.1× higher with VPN on. Means go the other way (no-VPN
mean inflated by rare bursts; peak ~41 Mbps). Mann-Whitney in
`analyze_all()`: p ≈ 0.

**Time-of-day (wifi, no VPN)**

| Condition | n | median ↓ Mbps |
|-----------|---|---------------|
| `wifi_novpn_afternoon` | 213 | 0.205 |
| `wifi_novpn_evening` | 3812 | 0.091 |

Evening/afternoon median ratio ≈ **0.44**. There is **no** `wifi_vpn_afternoon`
sample, so we cannot say whether that TOD gap shrinks under VPN. Proxy: at
evening, VPN-on median (0.283) is *above* afternoon no-VPN (0.205) — the
evening slump seen without VPN is not visible in the VPN-on evening session.

**Hotspot vs wifi (no VPN)**

| Condition | n | median ↓ Mbps | mean ↓ Mbps |
|-----------|---|---------------|-------------|
| `wifi_novpn_evening` | 3812 | 0.091 | 1.027 |
| `hotspot_novpn_evening` | 394 | 0.058 | 0.134 |
| `hotspot_novpn_night` | 265 | 0.188 | 0.517 |
| hotspot all (novpn) | 659 | 0.079 | 0.288 |
| wifi all (novpn) | 4025 | 0.096 | 0.993 |

Evening hotspot is slower than evening wifi on median. Night hotspot recovered
somewhat (different hour, still cellular).

**Still thin:** afternoon n=213; no VPN-on afternoon; one home, one evening.
Overnight samples would still help TOD. Retrain after long new sessions:
`python train_model.py`.

## Config

Edit `config.py` for vantage city / lat / lon / timezone.
