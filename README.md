# Path Watch

Personal ISP path monitor for my home network in Addis Ababa.

It watches connections **this machine already makes** (browser, apps, downloads).
It does **not** probe YouTube / Facebook / Telegram on purpose. Metadata only —
who you connected to, live up/down speed, wifi vs hotspot, VPN on/off, time of day.
No payloads, no bypass. Timing gaps are not proof of ISP intent.

## Run

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
./run.sh
```

Open **http://localhost:5173**

Or separately:

```bash
.venv/bin/uvicorn server:app --port 8000
cd frontend && npm run dev
```

Leave it open and use the internet normally. Switch VPN / hotspot / time of day
so the model has contrast to learn from.

## What it does

| Piece | Role |
|-------|------|
| `server.py` | FastAPI + WebSocket live feed |
| `passive_monitor.py` | Speeds + sockets via psutil |
| `geo_cache.py` | IP → lat/lng (ip-api.com batch) |
| `throttle_engine.py` | Score + analysis from logs |
| `network_status.py` | wifi/hotspot, VPN, TOD, public IP |
| `frontend/` | React dashboard (monitor / map / analysis / apps) |

Logs (gitignored): `traffic_log.csv`, `speed_log.csv`

Map heat is painted over **Addis Ababa** (where you feel the path), using
intensity learned from **all** destinations this machine reaches — not one site.

## Config

Edit `config.py` for vantage city / lat / lon / timezone.
