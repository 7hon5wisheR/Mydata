#!/bin/bash
# ================================================================
#  setup_overlay_persist.sh  (FILE BARU)
#  Memasang perlindungan "overlay + simpan config + bridge.conf + hostname ke SD asli"
#  TANPA mengubah kode Python. Jalankan SAAT OVERLAY MASIH MATI.
#  Pakai:  sudo ./setup_overlay_persist.sh     (lalu ikuti panduan B1)
#  Script ini TIDAK mengaktifkan overlay.
# ================================================================
set -e
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
if [ "$(findmnt -no FSTYPE /)" = "overlay" ]; then
  echo "Overlay sedang aktif. Matikan dulu: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1
fi
[ -f "$BROKER_DIR/overlay_persist/sitecustomize.py" ] || { echo "Folder $BROKER_DIR/overlay_persist belum ada (salin dulu)"; exit 1; }
[ -f "$BROKER_DIR/overlay_persist/broker-persist" ]   || { echo "File broker-persist belum ada"; exit 1; }

echo "[1] Pasang helper root: /usr/local/sbin/broker-persist"
install -o root -g root -m 755 "$BROKER_DIR/overlay_persist/broker-persist" /usr/local/sbin/broker-persist
sed -i "s#^BROKER=.*#BROKER=\"\${BROKER:-$BROKER_DIR}\"#" /usr/local/sbin/broker-persist

echo "[2] Izin sudo terbatas (hanya 2 argumen yang diizinkan)"
SUDOERS=/etc/sudoers.d/broker-persist
cat > "$SUDOERS.tmp" <<EOT
$HOME_BASE ALL=(root) NOPASSWD: /usr/local/sbin/broker-persist config.json, /usr/local/sbin/broker-persist conf.d/bridge.conf
EOT
chmod 440 "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp" >/dev/null || { rm -f "$SUDOERS.tmp"; echo "sudoers tidak valid, dibatalkan"; exit 1; }
mv "$SUDOERS.tmp" "$SUDOERS"

echo "[3] Drop-in systemd: PYTHONPATH (send, ipservice) + jeda dan penyimpanan jaringan (ipservice)"
IPSERVICE_DELAY="${IPSERVICE_DELAY:-30}"
if [ -f /etc/systemd/system/send.service ]; then
  mkdir -p /etc/systemd/system/send.service.d
  cat > /etc/systemd/system/send.service.d/10-hardening.conf <<EOT
[Service]
Environment=PYTHONPATH=$BROKER_DIR/overlay_persist
ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'
EOT
else
  echo "    (peringatan: send.service tidak ditemukan, dilewati)"
fi
if [ -f /etc/systemd/system/ipservice.service ]; then
  mkdir -p /etc/systemd/system/ipservice.service.d
  # After=dip.service + jeda: beri waktu dip.py mengubah IP sebelum ipservice membaca IP.
  # ExecStartPre persist net: simpan profil IP (netplan) dan hostname ke SD asli bila berubah.
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
  echo "    (peringatan: ipservice.service tidak ditemukan, dilewati)"
fi
systemctl daemon-reload

echo
echo "Selesai. BELUM ada overlay. Langkah berikut (panduan B1):"
echo "  sudo systemctl restart send ipservice"
echo "  sudo raspi-config nonint do_overlayfs 0 && sudo reboot"
echo "Setelah boot cek:  findmnt -no FSTYPE /   (overlay)   dan   findmnt /media/root-ro"
