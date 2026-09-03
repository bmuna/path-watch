# hosts. dns boxes are "is the whole path just bad right now".
# youtube/facebook/telegram are the ones I actually care about.
# pinging the frontend is a noisy proxy (video goes to cdns) but it's a start.

CSV_FILE = "metrics_log.csv"
TRAFFIC_CSV = "traffic_log.csv"   # passive: connections your apps make
SPEED_CSV = "speed_log.csv"       # passive: live up/down Mbps


BASELINE_TARGETS = [
    "1.1.1.1",
    "8.8.8.8",
]

SUSPECT_TARGETS = [
    "www.youtube.com",
    "www.facebook.com",
    "telegram.org",
]

# some hotspots / guest nets eat icmp
TCP_PORT = {
    "1.1.1.1": 443,
    "8.8.8.8": 53,
}

DEFAULT_TCP_PORT = 443

# ~1MB. not a real speedtest, just so ping isn't the only signal
THROUGHPUT_URL = "https://speed.cloudflare.com/__down?bytes=1000000"
THROUGHPUT_EVERY = 8

PING_COUNT = 4
PING_TIMEOUT_MS = 2000

CONNECTION_CHOICES = ("wifi", "hotspot")
VPN_CHOICES = ("vpn", "novpn")
TOD_CHOICES = ("morning", "afternoon", "evening", "night")

# where these measurements were taken (edit for your setup)
VANTAGE_LABEL = "home network"
VANTAGE_CITY = "Addis Ababa"
VANTAGE_TZ = "Africa/Addis_Ababa"

# rough notes for the destinations page — not GPS, just what each probe is
TARGET_META = {
    "1.1.1.1": {"label": "Cloudflare DNS", "kind": "baseline", "note": "anycast DNS"},
    "8.8.8.8": {"label": "Google DNS", "kind": "baseline", "note": "anycast DNS"},
    "www.youtube.com": {"label": "YouTube", "kind": "suspect", "note": "frontend; video is on googlevideo"},
    "www.facebook.com": {"label": "Facebook", "kind": "suspect", "note": "frontend; media is on fbcdn"},
    "telegram.org": {"label": "Telegram", "kind": "suspect", "note": "site / DC edge"},
}
