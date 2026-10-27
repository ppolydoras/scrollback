#!/usr/bin/env bash
# Opens Scrollback in its own window if Chrome or Chromium is installed,
# otherwise in the default browser. Starts the server first if it is not running.
set -euo pipefail

PORT="${SCROLLBACK_PORT:-8765}"
URL="http://localhost:${PORT}/"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

is_up() { python3 - "$URL" <<'EOF'
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1] + "api/meta", timeout=1)
except Exception:
    sys.exit(1)
EOF
}

if ! is_up; then
  if systemctl --user is-enabled scrollback.service >/dev/null 2>&1; then
    systemctl --user start scrollback.service
  else
    nohup python3 "$APP_DIR/scrollback.py" --port "$PORT" >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 30); do is_up && break; sleep 0.2; done
fi

for browser in google-chrome chromium chromium-browser brave-browser microsoft-edge; do
  if command -v "$browser" >/dev/null 2>&1; then
    exec "$browser" --app="$URL" --class=Scrollback
  fi
done
exec xdg-open "$URL"
