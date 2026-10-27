#!/usr/bin/env bash
# Sets up text to speech for Scrollback:
#   1. a private Python environment with Piper in your Scrollback data folder
#   2. one voice (default en_GB-cori-medium, about 60 MB)
# Run it again at any time. Pass a voice name to get a different one:
#   ./scripts/setup-tts.sh en_GB-alan-medium
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Ask Scrollback where its data folder is (this also moves over an old Annals folder).
DATA="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from scrollback_core import config; print(config.data_dir())' "$APP_DIR")"
VENV="$DATA/tts-venv"
VOICE="${1:-en_GB-cori-medium}"

mkdir -p "$DATA"

if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
  echo "Python cannot create virtual environments on this system yet."
  echo "Install the venv package, then run this script again:"
  echo "  sudo apt install python3-venv"
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating a Python environment for Piper in $VENV"
  python3 -m venv "$VENV"
fi

echo "Installing Piper (this downloads about 40 MB the first time)"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade piper-tts

if [[ -f "$DATA/voices/$VOICE.onnx" && -f "$DATA/voices/$VOICE.onnx.json" ]]; then
  echo "Voice $VOICE is already installed"
else
  python3 "$APP_DIR/scrollback.py" --download-voice "$VOICE"
fi

echo "Testing"
python3 "$APP_DIR/scrollback.py" --tts-test

if systemctl --user is-active scrollback.service >/dev/null 2>&1; then
  systemctl --user restart scrollback.service
  echo "Restarted the Scrollback service"
fi
echo "Done. Press Listen in Scrollback or P on the keyboard."
