#!/bin/bash
# remove_overlay_persist.sh -- melepas helper, sudo, drop-in, dan penyimpan profil IP. Tidak menyentuh overlay.
# Matikan overlay dulu:  sudo raspi-config nonint do_overlayfs 1 && sudo reboot
set -e
[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
[ "$(findmnt -no FSTYPE /)" = "overlay" ] && { echo "Overlay aktif; matikan dulu."; exit 1; }
systemctl disable --now broker-persist-net.path broker-persist-net.timer 2>/dev/null || true
rm -f /etc/systemd/system/broker-persist-net.service /etc/systemd/system/broker-persist-net.path /etc/systemd/system/broker-persist-net.timer
rm -f /usr/local/sbin/broker-persist /etc/sudoers.d/broker-persist
rm -f /etc/systemd/system/send.service.d/10-hardening.conf /etc/systemd/system/ipservice.service.d/10-hardening.conf
systemctl daemon-reload
systemctl restart send ipservice || true
echo "Dilepas. Folder overlay_persist/ boleh dihapus manual."
