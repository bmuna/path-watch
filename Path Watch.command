#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r _
  exit 1
fi
exec .venv/bin/python desktop_app.py
