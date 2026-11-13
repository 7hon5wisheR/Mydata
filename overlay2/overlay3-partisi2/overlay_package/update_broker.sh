#!/bin/bash
# ================================================================
#  update_broker.sh  (NEW FILE) -- update the broker code while the OVERLAY IS ACTIVE
#  Usage:  sudo ./update_broker.sh /home/alice/new_broker [--yes] [--reboot]
#  /home/alice/new_broker = folder with the extracted new broker zip (contains apps.py)
#  NOT overwritten: config.json status.json rfid1.json rfid2.json conf.d/bridge.conf logs/ cloud/
#  Changes take effect after a reboot.
# ================================================================
set -e
HOME_BASE="alice"
BROKER="/home/${HOME_BASE}/broker"
LOWER="${LOWER:-/media/root-ro}"
NEW="${1:-}"; shift || true
YES=0; REBOOT=0
for a in "$@"; do [ "$a" = "--yes" ] && YES=1; [ "$a" = "--reboot" ] && REBOOT=1; done

[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ -n "$NEW" ] && [ -f "$NEW/apps.py" ] || { echo "Usage: sudo ./update_broker.sh <new_folder_containing_apps.py> [--yes] [--reboot]"; exit 1; }
if [ "${UPDATE_FORCE:-0}" != "1" ] && [ "$(findmnt -no FSTYPE /)" != "overlay" ]; then
  echo "Overlay is not active. Copy the code normally (this script is not needed)."; exit 1
fi
command -v rsync >/dev/null || { echo "rsync is not installed: sudo apt install rsync"; exit 1; }
[ -d "$LOWER$BROKER" ] || { echo "$LOWER$BROKER not found"; exit 1; }

EXC=(--exclude=/config.json --exclude=/status.json --exclude=/rfid1.json --exclude=/rfid2.json
     --exclude=/conf.d/bridge.conf --exclude=/logs/ --exclude=/cloud/ --exclude=__pycache__/ --exclude='*.pyc')

echo "Changes that will be applied (dry run, nothing written):"
rsync -rlptD --checksum --itemize-changes --dry-run "${EXC[@]}" "$NEW"/ "$LOWER$BROKER"/ | head -60
if [ "$YES" != "1" ]; then read -r -p "Continue? (type yes) " ans; [ "$ans" = "yes" ] || { echo "Cancelled"; exit 1; }; fi

[ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,rw "$LOWER"
trap '[ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,ro "$LOWER"' EXIT
rsync -rlptD --checksum --chown=${HOME_BASE}:${HOME_BASE} "${EXC[@]}" "$NEW"/ "$LOWER$BROKER"/
sync -f "$LOWER$BROKER"
echo "Done. The new code is active after a reboot."
[ "$REBOOT" = "1" ] && { trap - EXIT; [ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,ro "$LOWER"; reboot; }
