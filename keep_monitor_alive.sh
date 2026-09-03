#!/bin/bash
# Keep Path Watch backend alive. Restarts if :8000 is down OR speed_log stops growing.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
LOG="$ROOT/.monitor_watchdog.log"
PIDFILE="$ROOT/.monitor_watchdog.pid"
CSV="$ROOT/speed_log.csv"
STALE_SECS=45

echo $$ > "$PIDFILE"
echo "$(date -Iseconds) watchdog start pid=$$" >> "$LOG"

start_uvicorn() {
  nohup "$ROOT/.venv/bin/uvicorn" server:app --host 127.0.0.1 --port 8000 \
    >> "$ROOT/.monitor_uvicorn.log" 2>&1 &
  echo "$(date -Iseconds) started uvicorn pid=$!" >> "$LOG"
}

while true; do
  LISTEN_PID="$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  if [ -z "${LISTEN_PID}" ]; then
    echo "$(date -Iseconds) :8000 down — starting" >> "$LOG"
    start_uvicorn
    sleep 5
    continue
  fi

  # stuck listener: port up but CSV not advancing
  if [ -f "$CSV" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$CSV") ))
    if [ "$AGE" -gt "$STALE_SECS" ]; then
      echo "$(date -Iseconds) speed_log stale ${AGE}s — recycle pid=$LISTEN_PID" >> "$LOG"
      kill "$LISTEN_PID" 2>/dev/null || true
      sleep 2
      start_uvicorn
    fi
  fi
  sleep 20
done
