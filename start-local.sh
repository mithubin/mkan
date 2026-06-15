#!/bin/bash
# Local development mode — no Docker, no server needed.
# Requires Python 3.10+
set -e
cd "$(dirname "$0")/server"

if [ ! -d ".venv" ]; then
  echo "Setting up virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  echo "Done."
fi

echo "Starting mkan on http://localhost:8000 ..."
if command -v xdg-open &>/dev/null; then
  (sleep 1.5 && xdg-open http://localhost:8000) &
elif command -v open &>/dev/null; then
  (sleep 1.5 && open http://localhost:8000) &
fi

.venv/bin/uvicorn main:app --port 8000 --reload
