#!/bin/bash
# Install gateway-telegram as a system service (launchd / systemd).
# Usage: ./install-service.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TABULA_HOME="${TABULA_HOME:-$HOME/.tabula}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

LABEL="com.tabula.gateway-telegram"

mkdir -p "$TABULA_HOME/logs"

case "$(uname -s)" in
  Darwin)
    PLIST_SRC="$SCRIPT_DIR/$LABEL.plist"
    PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

    [ -f "$PLIST_SRC" ] || die "plist template not found: $PLIST_SRC"

    # Stop existing service if loaded
    if launchctl print "gui/$(id -u)/$LABEL" &>/dev/null; then
      info "Stopping existing service..."
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        launchctl print "gui/$(id -u)/$LABEL" &>/dev/null || break
        sleep 1
      done
    fi

    sed "s|__TABULA_HOME__|${TABULA_HOME}|g" "$PLIST_SRC" > "$PLIST_DEST"
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
    ok "gateway-telegram service installed (launchd)"
    ;;

  Linux)
    UNIT_SRC="$SCRIPT_DIR/gateway-telegram.service"
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT_DEST="$UNIT_DIR/gateway-telegram.service"

    [ -f "$UNIT_SRC" ] || die "systemd unit not found: $UNIT_SRC"

    mkdir -p "$UNIT_DIR"
    sed "s|__TABULA_HOME__|${TABULA_HOME}|g; s|__USER__|$(whoami)|g" "$UNIT_SRC" > "$UNIT_DEST"

    systemctl --user daemon-reload
    systemctl --user enable --now gateway-telegram.service
    ok "gateway-telegram service installed (systemd)"

    if command -v loginctl &>/dev/null; then
      loginctl enable-linger "$(whoami)" 2>/dev/null || true
    fi
    ;;

  *)
    die "Unsupported OS: $(uname -s). Use install-service.ps1 for Windows."
    ;;
esac

printf '\nCheck status:\n'
case "$(uname -s)" in
  Darwin) printf '  launchctl print gui/%s/%s\n' "$(id -u)" "$LABEL" ;;
  Linux)  printf '  systemctl --user status gateway-telegram\n' ;;
esac
printf '  tail -f %s/logs/gateway-telegram*.log\n' "$TABULA_HOME"
