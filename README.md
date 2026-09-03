# Path Watch

Personal ISP path monitor for a home network in Addis Ababa.

Watches connections **this machine already makes** (browser, apps, downloads).
Does **not** probe specific sites. Metadata only: remote IPs, live up/down speed,
wifi vs hotspot, VPN on/off, time of day. No payloads, no bypass.
Timing gaps are **not** proof of ISP intent.

## Architecture (current)

Passive FastAPI backend + React frontend. No Streamlit, no active ping collector,
no trained GRU / classifier.

| Piece | Role |
|-------|------|
| `server.py` | FastAPI + WebSocket `/ws/live` + `/api/analysis` |
| `passive_monitor.py` | Speeds + sockets via psutil → CSV logs |
| `geo_cache.py` | IP → lat/lng (ip-api.com batch) |
| `throttle_engine.py` | Heuristic / statistical scorer + descriptive analysis |
| `network_status.py` | wifi/hotspot, VPN, TOD, public IP |
| `frontend/` | React UI (monitor / map / analysis / apps) |

`throttle_engine.py` compares live speed to same-condition baselines and
summarizes logs (TOD, VPN, connection). It is **not** a trained ML model.
Next step (not built): train a sequence model once enough labeled session
data exists.

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

## Findings so far (passive logs)

From `speed_log.csv` on the same day (wifi, no VPN):

- **Afternoon** (`wifi_novpn_afternoon`): median down ≈ **0.20 Mbps**
- **Evening** (`wifi_novpn_evening`): median down ≈ **0.08 Mbps**
- Link looks **bursty** (peak tens of Mbps vs low median)

**VPN and hotspot contrast still needed to distinguish throttling from
ordinary time-of-day congestion.** Until those labeled sessions exist, treat
the evening slowdown as a timing/congestion signal only — not differential
treatment evidence.

## Config

Edit `config.py` for vantage city / lat / lon / timezone.
