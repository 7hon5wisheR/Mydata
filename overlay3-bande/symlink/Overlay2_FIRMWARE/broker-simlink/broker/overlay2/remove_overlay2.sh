#!/bin/bash
__version__="1.0.0.(18) - overlay2"
# =====================================================================================================================================
#      OVERLAY2
#      Name                       : OVERLAY2
#      Version                    : 1.0.0.(18) 
#      Date Created               : 11-13-2026
#      Updated                    : 11-13-2026
#      Author                     : Saifuddin
#      Notes                      : remove_overlay2.sh - uninstall Overlay2; config.json and bridge.conf are copied back onto the SD as normal files.
#                                   Overlay must be OFF.  Usage: sudo ./remove_overlay2.sh   (the data in the persistent store is left untouched)
# ======================================================================================================================================
[ -f /etc/overlay2.conf ] && . /etc/overlay2.conf
BROKER="${BROKER_DIR:-/home/alice/broker}"; HOME_BASE=alice; MNT="${PERSIST_DIR:-/boot/firmware}"; MOSQ=/etc/mosquitto/mosquitto.conf
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ "$(findmnt -no FSTYPE /)" = "overlay" ] && { echo "Overlay is ON. Disable it first: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1; }
systemctl disable --now overlay2-prepare.service overlay2-stage2.service 2>/dev/null
for pair in "config.json:$BROKER/config.json" "bridge.conf:$BROKER/conf.d/bridge.conf"; do
  n="${pair%%:*}"; l="${pair#*:}"
  if [ -L "$l" ]; then
    src="$(readlink -f "$l")"; rm -f "$l"
    if [ -s "$src" ]; then cp -f "$src" "$l"; else cp -f "$BROKER/.defaults/$n" "$l"; fi
    chown $HOME_BASE:$HOME_BASE "$l"
  fi
done
sed -i "s#^include_dir .*#include_dir $BROKER/conf.d#" "$MOSQ"
rm -f /etc/systemd/system/send.service.d/10-overlay2.conf /etc/systemd/system/ipservice.service.d/10-overlay2.conf \
      /etc/systemd/system/overlay2-prepare.service /etc/systemd/system/overlay2-stage2.service \
      /usr/local/sbin/overlay2-prepare /etc/overlay2.conf
sed -i '\#LABEL=PERSIST#d' /etc/fstab
sed -i -E "\\|[[:space:]]$MNT[[:space:]]| s/,uid=[0-9]+,gid=[0-9]+,umask=0022//" /etc/fstab
rm -rf /var/lib/overlay2 "$BROKER/persist_store"
systemctl daemon-reload; systemctl restart mosquitto
echo "Removed. config.json and bridge.conf are normal files on the SD again; mosquitto include_dir restored. Reboot recommended."
