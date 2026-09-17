#!/usr/bin/env bash
# Install BC-250 Recovery into ~/Reboot-to-UEFI and add it to Steam, with artwork.
# Run as your normal user (not root). Safe to re-run: it updates in place.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SRC")"
DEST="${1:-$HOME/Reboot-to-UEFI}"

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, not root." >&2
    exit 1
fi

python3 -c 'import PyQt6.QtWidgets, evdev' 2>/dev/null \
    || echo "warning: PyQt6 or python-evdev missing - the shortcut will fall back to the terminal menu" >&2

mkdir -p "$DEST/art"
install -m 755 "$SRC/bc250-recovery"       "$DEST/bc250-recovery"
install -m 644 "$SRC/bc250_recovery_ui.py" "$DEST/bc250_recovery_ui.py"
install -m 755 "$SRC/steam_cdp.py"         "$DEST/steam_cdp.py"
install -m 755 "$SRC/steam_shortcut.py"    "$DEST/steam_shortcut.py"
install -m 755 "$REPO/reboot-uefi.sh"      "$DEST/reboot-uefi.sh"
install -m 644 "$SRC"/art/*.png            "$DEST/art/"
echo "installed files to $DEST"

if python3 "$DEST/steam_cdp.py" ping >/dev/null 2>&1; then
    python3 "$DEST/steam_shortcut.py" install-live "$DEST"
    sleep 2
else
    if pgrep -x steam >/dev/null; then
        echo "Steam is running but its UI isn't answering; not touching shortcuts.vdf" >&2
        echo "(Steam would overwrite the change). Retry once Steam has finished starting." >&2
        exit 1
    fi
    python3 "$DEST/steam_shortcut.py" install "$DEST"
fi

python3 "$DEST/steam_shortcut.py" verify "$DEST"
