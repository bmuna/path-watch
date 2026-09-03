# hosts. dns boxes are "is the whole path just bad right now".
# youtube/facebook/telegram are the ones I actually care about.
# pinging the frontend is a noisy proxy (video goes to cdns) but it's a start.

CSV_FILE = "metrics_log.csv"

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
