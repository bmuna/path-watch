#!/bin/bash
# Start both backend and frontend for development
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Setup first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ ! -d frontend/node_modules ]; then
  echo "Install frontend: cd frontend && npm install"
  exit 1
fi

echo "Starting backend on :8000 ..."
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 &
BACK_PID=$!

echo "Starting frontend on :5173 ..."
cd frontend && npm run dev &
FRONT_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""
echo "  Press Ctrl-C to stop both."
echo ""

trap "kill $BACK_PID $FRONT_PID 2>/dev/null; exit 0" INT TERM
wait
