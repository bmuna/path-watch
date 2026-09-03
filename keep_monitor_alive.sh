#!/bin/bash
# Keep Path Watch backend (passive_monitor via uvicorn) alive overnight.
# Does not kill a healthy listener — only restarts if port 8000 is free.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
LOG="$ROOT/.monitor_watchdog.log"
PIDFILE="$ROOT/.monitor_watchdog.pid"

echo $$ > "$PIDFILE"
echo "$(date -Iseconds) watchdog start pid=$$" >> "$LOG"

while true; do
  if ! lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$(date -Iseconds) :8000 down — starting uvicorn" >> "$LOG"
    nohup "$ROOT/.venv/bin/uvicorn" server:app --host 127.0.0.1 --port 8000 \
      >> "$ROOT/.monitor_uvicorn.log" 2>&1 &
    echo "$(date -Iseconds) uvicorn pid=$!" >> "$LOG"
  fi
  sleep 60
done
