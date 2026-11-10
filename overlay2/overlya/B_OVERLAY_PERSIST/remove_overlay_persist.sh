#!/bin/bash
# remove_overlay_persist.sh (FILE BARU) -- melepas helper, sudo, dan drop-in. Tidak menyentuh overlay.
# Matikan overlay dulu:  sudo raspi-config nonint do_overlayfs 1 && sudo reboot
set -e
[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
[ "$(findmnt -no FSTYPE /)" = "overlay" ] && { echo "Overlay aktif; matikan dulu."; exit 1; }
rm -f /usr/local/sbin/broker-persist /etc/sudoers.d/broker-persist
rm -f /etc/systemd/system/send.service.d/10-hardening.conf /etc/systemd/system/ipservice.service.d/10-hardening.conf
systemctl daemon-reload
systemctl restart send ipservice || true
echo "Dilepas. Folder overlay_persist/ boleh dihapus manual."
