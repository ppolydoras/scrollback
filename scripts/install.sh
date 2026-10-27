#!/usr/bin/env bash
# Installs Scrollback for the current user on Ubuntu:
#   - a systemd user service that starts Scrollback at login and restarts it on failure
#   - a "Scrollback" launcher in the app grid
# It also moves over an install made under the app's first name, Annals.
# Nothing is copied: the service runs the code from this folder.
# Use --no-service to install only the launcher (the server then starts on demand).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SCROLLBACK_PORT:-${ANNALS_PORT:-8765}}"
WITH_SERVICE=1
[[ "${1:-}" == "--no-service" ]] && WITH_SERVICE=0

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "python3 was not found. Install it with: sudo apt install python3" >&2
  exit 1
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "Scrollback needs Python 3.9 or newer." >&2; exit 1; }

CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/scrollback"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$CONF_DIR" "$APPS_DIR" "$ICON_DIR" "$UNIT_DIR"

# Move over an install made when the app was called Annals.
OLD_UNIT="$UNIT_DIR/annals.service"
OLD_CONF="${XDG_CONFIG_HOME:-$HOME/.config}/annals"
if [[ -f "$OLD_UNIT" ]]; then
  if [[ -z "${SCROLLBACK_PORT:-}${ANNALS_PORT:-}" ]]; then
    OLD_PORT="$(sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p' "$OLD_UNIT" | head -n 1)"
    [[ -n "$OLD_PORT" ]] && PORT="$OLD_PORT"
  fi
  systemctl --user disable --now annals.service >/dev/null 2>&1 || true
  rm -f "$OLD_UNIT"
  systemctl --user daemon-reload || true
  echo "Stopped and removed the old Annals service. Scrollback keeps port $PORT."
fi
rm -f "$APPS_DIR/annals.desktop" "$ICON_DIR/annals.svg"
if [[ -f "$OLD_CONF/env" && ! -f "$CONF_DIR/env" ]]; then
  sed 's/^\(#* *\)ANNALS_/\1SCROLLBACK_/' "$OLD_CONF/env" > "$CONF_DIR/env"
  chmod 600 "$CONF_DIR/env"
  rm -rf "$OLD_CONF"
  echo "Moved your service settings to $CONF_DIR/env."
fi

chmod +x "$APP_DIR/scrollback.py" "$APP_DIR/scripts/"*.sh

# Optional environment file for the storyteller keys, readable only by you.
if [[ ! -f "$CONF_DIR/env" ]]; then
  cat > "$CONF_DIR/env" <<'EOF'
# Environment for the Scrollback service. Lines are KEY=value, no quotes needed.
# Uncomment what you need, then run: systemctl --user restart scrollback
#
# SCROLLBACK_LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=
#
# SCROLLBACK_LLM_PROVIDER=openai
# SCROLLBACK_LLM_BASE_URL=http://localhost:11434/v1
# SCROLLBACK_LLM_MODEL=llama3.1:8b
#
# SCROLLBACK_CONTACT=you@example.com
EOF
  chmod 600 "$CONF_DIR/env"
fi

cp "$APP_DIR/static/icon.svg" "$ICON_DIR/scrollback.svg"

cat > "$APPS_DIR/scrollback.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Scrollback
Comment=A personal feed of historical events
Exec=env SCROLLBACK_PORT=$PORT "$APP_DIR/scripts/open-scrollback.sh"
Icon=scrollback
Terminal=false
Categories=Education;History;
StartupWMClass=Scrollback
EOF
update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

if [[ "$WITH_SERVICE" == 1 ]]; then
  cat > "$UNIT_DIR/scrollback.service" <<EOF
[Unit]
Description=Scrollback, a personal feed of historical events
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-$CONF_DIR/env
ExecStart="$PY" "$APP_DIR/scrollback.py" --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable scrollback.service
  systemctl --user restart scrollback.service
  echo "Scrollback is running at http://localhost:$PORT/ and will start at every login."
  echo "Logs: journalctl --user -u scrollback -f"
else
  echo "Launcher installed. Scrollback starts when you open it from the app grid."
fi
echo "Open it from the app grid (search for Scrollback) or visit http://localhost:$PORT/"
