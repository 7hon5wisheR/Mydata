#!/bin/bash
__version__="1.0.0.(16) - overlay"
# =====================================================================================================================================
#      OVERLAY
#      Name                       : OVERLAY
#      Version                    : 1.0.0.(16) 
#      Date Created               : 11-10-2026
#      Updated                    : 11-10-2026
#      Author                     : Saifuddin
#      Notes                      : remove_overlay_persist.sh - removes the helper, sudo rule, drop-ins and the IP profile saver. Does not touch the overlay.
#                                   Disable the overlay first:  sudo raspi-config nonint do_overlayfs 1 && sudo reboot
# ======================================================================================================================================
set -e
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ "$(findmnt -no FSTYPE /)" = "overlay" ] && { echo "Overlay is active; disable it first."; exit 1; }
systemctl disable --now broker-persist-net.path broker-persist-net.timer 2>/dev/null || true
rm -f /etc/systemd/system/broker-persist-net.service /etc/systemd/system/broker-persist-net.path /etc/systemd/system/broker-persist-net.timer
rm -f /usr/local/sbin/broker-persist /etc/sudoers.d/broker-persist
rm -f /etc/systemd/system/send.service.d/10-hardening.conf /etc/systemd/system/ipservice.service.d/10-hardening.conf
systemctl daemon-reload
systemctl restart send ipservice || true
echo "Removed. The overlay_persist/ folder can be deleted manually."
