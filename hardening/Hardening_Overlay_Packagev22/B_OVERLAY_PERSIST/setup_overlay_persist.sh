#!/bin/bash
__version__="1.0.0.(16) - overlay"
# =====================================================================================================================================
#      OVERLAY
#      Name                       : OVERLAY
#      Version                    : 1.0.0.(16) 
#      Date Created               : 11-10-2026
#      Updated                    : 11-10-2026
#      Author                     : Saifuddin
#      Notes                      : setup_overlay_persist.sh - installs "overlay + persist config + bridge.conf + hostname + eth0 IP to the real SD card"
#                                   WITHOUT changing any Python code. Run it WHILE THE OVERLAY IS STILL OFF.
#                                   Usage:  sudo ./setup_overlay_persist.sh     (then follow guide B1)
#                                   Safe to re-run (to upgrade from an older version). It does NOT enable the overlay.
#                                   ipservice start delay (seconds), adjustable:  sudo IPSERVICE_DELAY=45 ./setup_overlay_persist.sh
# ======================================================================================================================================
set -e
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
IPSERVICE_DELAY="${IPSERVICE_DELAY:-30}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
if [ "$(findmnt -no FSTYPE /)" = "overlay" ]; then
  echo "Overlay is active. Disable it first: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1
fi
[ -f "$BROKER_DIR/overlay_persist/sitecustomize.py" ] || { echo "Folder $BROKER_DIR/overlay_persist does not exist yet (copy it first)"; exit 1; }
[ -f "$BROKER_DIR/overlay_persist/broker-persist" ]   || { echo "File broker-persist does not exist yet"; exit 1; }
case "$IPSERVICE_DELAY" in ''|*[!0-9]*) echo "IPSERVICE_DELAY must be a number (seconds)"; exit 1 ;; esac
ls /etc/netplan/*.yaml >/dev/null 2>&1 || echo "(warning: no /etc/netplan/*.yaml - the IP profile cannot be made permanent; contact the package author)"

echo "[1] Install the root helper: /usr/local/sbin/broker-persist"
install -o root -g root -m 755 "$BROKER_DIR/overlay_persist/broker-persist" /usr/local/sbin/broker-persist
sed -i "s#^BROKER=.*#BROKER=\"\${BROKER:-$BROKER_DIR}\"#" /usr/local/sbin/broker-persist

echo "[2] Restricted sudo rule (only 2 arguments allowed)"
SUDOERS=/etc/sudoers.d/broker-persist
cat > "$SUDOERS.tmp" <<EOT
$HOME_BASE ALL=(root) NOPASSWD: /usr/local/sbin/broker-persist config.json, /usr/local/sbin/broker-persist conf.d/bridge.conf
EOT
chmod 440 "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp" >/dev/null || { rm -f "$SUDOERS.tmp"; echo "sudoers invalid, cancelled"; exit 1; }
mv "$SUDOERS.tmp" "$SUDOERS"

echo "[3] systemd drop-in (PYTHONPATH) for send and ipservice; ${IPSERVICE_DELAY} s start delay for ipservice"
for SVC in send ipservice; do
  if [ -f "/etc/systemd/system/$SVC.service" ]; then
    mkdir -p "/etc/systemd/system/$SVC.service.d"
    {
      echo "[Service]"
      echo "Environment=PYTHONPATH=$BROKER_DIR/overlay_persist"
      echo "ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'"
      if [ "$SVC" = "ipservice" ]; then
        # give dip.py time to finish changing the IP before ipservice reads it (backup safeguard)
        echo "ExecStartPre=/bin/sleep $IPSERVICE_DELAY"
        echo "TimeoutStartSec=$((IPSERVICE_DELAY + 120))"
      fi
    } > "/etc/systemd/system/$SVC.service.d/10-hardening.conf"
  else
    echo "    (warning: $SVC.service not found, skipped)"
  fi
done

echo "[4] eth0 IP profile saver (systemd path + timer) -> copies the eth0 /etc/netplan/*.yaml to the real SD"
cat > /etc/systemd/system/broker-persist-net.service <<'EOT'
[Unit]
Description=Save the eth0 IP profile (netplan) to the real SD card while the overlay is active
ConditionPathExists=/usr/local/sbin/broker-persist
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/broker-persist network
EOT
cat > /etc/systemd/system/broker-persist-net.path <<'EOT'
[Unit]
Description=Watch /etc/netplan for changes (IP from the DIP switch)

[Path]
PathChanged=/etc/netplan
Unit=broker-persist-net.service

[Install]
WantedBy=multi-user.target
EOT
cat > /etc/systemd/system/broker-persist-net.timer <<'EOT'
[Unit]
Description=Backup: check the eth0 IP profile every minute (does not write the SD when nothing changed)

[Timer]
OnBootSec=90
OnUnitActiveSec=60
Unit=broker-persist-net.service

[Install]
WantedBy=timers.target
EOT
systemctl daemon-reload
systemctl enable --now broker-persist-net.path broker-persist-net.timer

echo
echo "Done. The overlay is NOT enabled yet. Next steps (guide B1):"
echo "  sudo systemctl restart send ipservice"
echo "  sudo ./verify_overlay_persist.sh          (everything must be OK / SKIP)"
echo "  sudo raspi-config nonint do_overlayfs 0 && sudo reboot"
echo "After boot check:  findmnt -no FSTYPE /   (overlay)   and   findmnt /media/root-ro"
