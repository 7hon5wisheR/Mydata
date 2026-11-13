#!/bin/bash
# ================================================================
#  setup_overlay_persist.sh  (NEW FILE)
#  Installs "overlay + persist config.json, bridge.conf, hostname and network profile to the real SD card"
#  WITHOUT changing any Python code. Run it WHILE THE OVERLAY IS STILL OFF.
#  Usage:  sudo ./setup_overlay_persist.sh     (then follow guide B1)
#  This script does NOT enable the overlay.
# ================================================================
set -e
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
if [ "$(findmnt -no FSTYPE /)" = "overlay" ]; then
  echo "Overlay is active. Disable it first: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1
fi
[ -f "$BROKER_DIR/overlay_persist/sitecustomize.py" ] || { echo "Folder $BROKER_DIR/overlay_persist is missing (copy it first)"; exit 1; }
[ -f "$BROKER_DIR/overlay_persist/broker-persist" ]   || { echo "broker-persist file is missing"; exit 1; }

echo "[1] Install root helper: /usr/local/sbin/broker-persist"
install -o root -g root -m 755 "$BROKER_DIR/overlay_persist/broker-persist" /usr/local/sbin/broker-persist
sed -i "s#^BROKER=.*#BROKER=\"\${BROKER:-$BROKER_DIR}\"#" /usr/local/sbin/broker-persist

echo "[2] Restricted sudo rule (only 2 arguments allowed)"
SUDOERS=/etc/sudoers.d/broker-persist
cat > "$SUDOERS.tmp" <<EOT
$HOME_BASE ALL=(root) NOPASSWD: /usr/local/sbin/broker-persist config.json, /usr/local/sbin/broker-persist conf.d/bridge.conf
EOT
chmod 440 "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp" >/dev/null || { rm -f "$SUDOERS.tmp"; echo "Invalid sudoers file, aborted"; exit 1; }
mv "$SUDOERS.tmp" "$SUDOERS"

echo "[3] systemd drop-ins: PYTHONPATH (send, ipservice) + start delay and network persist (ipservice)"
IPSERVICE_DELAY="${IPSERVICE_DELAY:-30}"
if [ -f /etc/systemd/system/send.service ]; then
  mkdir -p /etc/systemd/system/send.service.d
  cat > /etc/systemd/system/send.service.d/10-hardening.conf <<EOT
[Service]
Environment=PYTHONPATH=$BROKER_DIR/overlay_persist
ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'
EOT
else
  echo "    (warning: send.service not found, skipped)"
fi
if [ -f /etc/systemd/system/ipservice.service ]; then
  mkdir -p /etc/systemd/system/ipservice.service.d
  # After=dip.service + delay: gives dip.py time to change the IP before ipservice reads it.
  # ExecStartPre "persist net": stores the IP profile (netplan) and hostname on the real SD card if they changed.
  cat > /etc/systemd/system/ipservice.service.d/10-hardening.conf <<EOT
[Unit]
After=dip.service
[Service]
Environment=PYTHONPATH=$BROKER_DIR/overlay_persist
ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'
ExecStartPre=/bin/sleep $IPSERVICE_DELAY
ExecStartPre=-/usr/local/sbin/broker-persist net
EOT
else
  echo "    (warning: ipservice.service not found, skipped)"
fi
systemctl daemon-reload

echo
echo "Done. The overlay is NOT enabled yet. Next steps (guide B1):"
echo "  sudo systemctl restart send ipservice"
echo "  sudo raspi-config nonint do_overlayfs 0 && sudo reboot"
echo "After boot check:  findmnt -no FSTYPE /   (overlay)   and   findmnt /media/root-ro"
