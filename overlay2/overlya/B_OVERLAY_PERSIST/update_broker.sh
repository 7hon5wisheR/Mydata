#!/bin/bash
# ================================================================
#  update_broker.sh  (FILE BARU) -- update kode broker saat OVERLAY AKTIF
#  Pakai:  sudo ./update_broker.sh /home/alice/new_broker [--yes] [--reboot]
#  /home/alice/new_broker = folder hasil ekstrak zip broker baru (berisi apps.py)
#  TIDAK menimpa: config.json status.json rfid1.json rfid2.json conf.d/bridge.conf logs/ cloud/
#  Perubahan baru terlihat setelah reboot.
# ================================================================
set -e
HOME_BASE="alice"
BROKER="/home/${HOME_BASE}/broker"
LOWER="${LOWER:-/media/root-ro}"
NEW="${1:-}"; shift || true
YES=0; REBOOT=0
for a in "$@"; do [ "$a" = "--yes" ] && YES=1; [ "$a" = "--reboot" ] && REBOOT=1; done

[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
[ -n "$NEW" ] && [ -f "$NEW/apps.py" ] || { echo "Pakai: sudo ./update_broker.sh <folder_baru_berisi_apps.py> [--yes] [--reboot]"; exit 1; }
if [ "${UPDATE_FORCE:-0}" != "1" ] && [ "$(findmnt -no FSTYPE /)" != "overlay" ]; then
  echo "Overlay tidak aktif. Salin kode seperti biasa (tidak perlu skrip ini)."; exit 1
fi
command -v rsync >/dev/null || { echo "rsync belum terpasang: sudo apt install rsync"; exit 1; }
[ -d "$LOWER$BROKER" ] || { echo "$LOWER$BROKER tidak ditemukan"; exit 1; }

EXC=(--exclude=/config.json --exclude=/status.json --exclude=/rfid1.json --exclude=/rfid2.json
     --exclude=/conf.d/bridge.conf --exclude=/logs/ --exclude=/cloud/ --exclude=__pycache__/ --exclude='*.pyc')

echo "Perubahan yang akan diterapkan (tanpa menulis):"
rsync -rlptD --checksum --itemize-changes --dry-run "${EXC[@]}" "$NEW"/ "$LOWER$BROKER"/ | head -60
if [ "$YES" != "1" ]; then read -r -p "Lanjutkan? (ketik ya) " ans; [ "$ans" = "ya" ] || { echo "Dibatalkan"; exit 1; }; fi

[ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,rw "$LOWER"
trap '[ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,ro "$LOWER"' EXIT
rsync -rlptD --checksum --chown=${HOME_BASE}:${HOME_BASE} "${EXC[@]}" "$NEW"/ "$LOWER$BROKER"/
sync -f "$LOWER$BROKER"
echo "Selesai. Kode baru aktif setelah reboot."
[ "$REBOOT" = "1" ] && { trap - EXIT; [ "${UPDATE_NOMOUNT:-0}" = "1" ] || mount -o remount,ro "$LOWER"; reboot; }
