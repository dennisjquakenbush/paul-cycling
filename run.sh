#!/bin/bash
# Run the full daily pipeline and (re)start the local dashboard server.
# Safe to run any time; launchd also runs `daily.py` every day at noon.
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3)"
echo "Using $PY"

# 1. refresh data + rebuild the dashboard
"$PY" daily.py

# 2. make sure the dashboard is being served at http://localhost:8777
if ! curl -s -o /dev/null http://localhost:8777/index.html 2>/dev/null; then
  echo "Starting dashboard server at http://localhost:8777 ..."
  (cd web && nohup "$PY" -m http.server 8777 >/tmp/paul_web.log 2>&1 &)
  sleep 1
fi
echo "Dashboard: http://localhost:8777"
