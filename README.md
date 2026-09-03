# home isp measurements

Wifi at home is usually fine. When I tether the laptop off my phone it gets
weird, especially at night, and I keep thinking it depends on what I'm using
(youtube vs just browsing). Maybe that's real, maybe I'm just annoyed. This
is me logging what my Mac actually does on the network so I can check instead
of guessing.

**Main thing:** a desktop app that watches connections *your apps already make*
(browser, Telegram, downloads…). It does **not** visit YouTube/Facebook itself.
It records who you connected to, live upload/download speed, wifi vs hotspot,
VPN on/off, and time of day. Metadata only — no payloads, no bypass.

## desktop app (start here)

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python desktop_app.py
```

Or double-click `Path Watch.command` after the venv exists.

Leave it open and use the internet normally. Turn VPN on/off, switch to
hotspot, download something — that contrast is what the hints learn from.

Logs land in `traffic_log.csv` (each new connection) and `speed_log.csv`
(upload/download every couple seconds).

## optional: active ping collector

The older `collect_metrics.py` still exists if you want deliberate pings to
DNS / sites. Prefer the desktop watcher for day-to-day data.

```
python collect_metrics.py --auto --watch
```

`network_status.py` detects SSID / VPN / clock / public IP
(`python network_status.py` to print what it thinks).

## analysis

```
python analyze_metrics.py
python analyze_metrics.py --save
```

Order I actually trust this:

1. Look at the medians. If there's no gap you can see, stop.
2. Mann-Whitney U, suspect rtt vs the dns baselines, within a label.
   Latency is skewed so no t-test. One-sided because the claim is "this is
   slower", not "this is different".
3. Same gap with vpn on. A gap that exists off-vpn and collapses on-vpn is
   the only thing that would make me take this seriously. A gap that stays
   is more like "youtube is farther away than 1.1.1.1".
4. Logreg / random forest predicting wifi vs hotspot (or vpn vs not) from
   the per-round timing. This is just "is there any signal at all". I print
   majority-class accuracy next to it so 70% on a 68/32 split doesn't look
   like a result.
5. GRU, maybe later, see below.

p-values: several hosts, several labels, I didn't correct them. Ranking,
not a claim about the isp.

## gru

I wrote `model_gru.py` because one of the stories in my head is "it was
fine for 30s and then it got shaped". A classifier on a single round can't
see that.

Only worth it once I have a few long sessions (I gated on 4 runs with
≥40 rounds). If the csv is basically independent snapshots, training a gru
is just extra parameters on the same median gap. I'm not doing that.

```
python model_gru.py          # bails if the csv is too short
python model_gru.py --force
```

torch is commented out in requirements on purpose.

## dashboard

```
streamlit run dashboard.py
```

Latest round, rtt over time, the mw / vpn tables, classifier weights if
anything actually trained.

## findings

Still need the 8 labeled sessions. Scripts work, evidence doesn't exist yet.
When I have the csv I'll put gap vs baseline (off-vpn vs on-vpn) for each
suspect host, whether mw agrees, whether the classifier beats majority
class, and whether anything interesting happens inside a run.

If the gaps are small or they don't shrink on vpn I'll say so. Would
probably just mean evening cell congestion.

## limitations

One apartment, my isp, my phone. A few 25-min captures is a pilot, not a
study. Pinging youtube.com is not watching youtube; the real bytes go to
googlevideo / cdns. The 1MB cloudflare fetch isn't a speedtest either. Vpn
changes path and dns, not just "hides me from the isp". Different timing
is not the same as them deciding to throttle telegram.

Other people on the wifi, lte being lte, the hotspot dropping to a worse
band, my vpn server being slow. `--note` exists because of that.

## if I keep going

Probe the actual cdns instead of the frontends. Longer download tests
(10–20s) if I want to talk about shaping. Same tests from somewhere else
so I can tell "this isp" from "this city at 8pm". Don't bother with the
gru until the within-run plots actually show an onset.

OONI has a real methodology for this (tls / download timing vs a baseline,
no payload inspection). I read their design doc and the kazakhstan / russia
/ türkiye reports before I started. This is a much smaller single-home
version with ping because I can leave it running.

- https://github.com/ooni/probe-cli/blob/master/docs/design/dd-007-throttling.md
- https://ooni.org/post/2023-throttling-kz-elections/
- https://ooni.org/post/2022-russia-blocks-amid-ru-ua-conflict/
- https://ooni.org/post/2023-turkey-throttling-blocking-twitter/

```
collect_metrics.py
analyze_metrics.py
model_gru.py      don't use this to decorate a weak csv
dashboard.py
config.py         hosts / ping count
metrics_log.csv   gitignored
```
