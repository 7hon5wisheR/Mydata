#!/bin/bash
# remove_overlay_persist.sh (NEW FILE) -- removes the helper, sudo rule and systemd drop-ins. Does not touch the overlay itself.
# Disable the overlay first:  sudo raspi-config nonint do_overlayfs 1 && sudo reboot
set -e
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ "$(findmnt -no FSTYPE /)" = "overlay" ] && { echo "Overlay is active; disable it first."; exit 1; }
rm -f /usr/local/sbin/broker-persist /etc/sudoers.d/broker-persist
rm -f /etc/systemd/system/send.service.d/10-hardening.conf /etc/systemd/system/ipservice.service.d/10-hardening.conf
systemctl daemon-reload
systemctl restart send ipservice || true
echo "Removed. The overlay_persist/ folder can be deleted manually."
