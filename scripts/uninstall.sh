#!/usr/bin/env bash
# Removes the service and the launcher. Your reading history is kept in
# ~/.local/share/scrollback unless you pass --purge.
set -euo pipefail

UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/scrollback.service"
if [[ -f "$UNIT" ]]; then
  systemctl --user disable --now scrollback.service || true
  rm -f "$UNIT"
  systemctl --user daemon-reload
fi
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/scrollback.desktop"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/scrollback.svg"

if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/scrollback" "${XDG_CONFIG_HOME:-$HOME/.config}/scrollback"
  rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/annals" "${XDG_CONFIG_HOME:-$HOME/.config}/annals"
  echo "Scrollback and all its data are removed."
else
  echo "Scrollback is uninstalled. Your data is still in ${XDG_DATA_HOME:-$HOME/.local/share}/scrollback"
fi
