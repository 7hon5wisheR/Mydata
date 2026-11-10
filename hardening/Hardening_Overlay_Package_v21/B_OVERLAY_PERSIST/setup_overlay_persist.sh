#!/bin/bash
# ================================================================
#  setup_overlay_persist.sh  (FILE BARU)
#  Memasang perlindungan "overlay + simpan config + bridge.conf + hostname + IP eth0 ke SD asli"
#  TANPA mengubah kode Python. Jalankan SAAT OVERLAY MASIH MATI.
#  Pakai:  sudo ./setup_overlay_persist.sh     (lalu ikuti panduan B1)
#  Aman dijalankan ulang (untuk memperbarui dari versi lama).
#  Script ini TIDAK mengaktifkan overlay.
#  Jeda mulai ipservice (detik), bisa diubah:  sudo IPSERVICE_DELAY=45 ./setup_overlay_persist.sh
# ================================================================
set -e
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
IPSERVICE_DELAY="${IPSERVICE_DELAY:-30}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
if [ "$(findmnt -no FSTYPE /)" = "overlay" ]; then
  echo "Overlay sedang aktif. Matikan dulu: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1
fi
[ -f "$BROKER_DIR/overlay_persist/sitecustomize.py" ] || { echo "Folder $BROKER_DIR/overlay_persist belum ada (salin dulu)"; exit 1; }
[ -f "$BROKER_DIR/overlay_persist/broker-persist" ]   || { echo "File broker-persist belum ada"; exit 1; }
case "$IPSERVICE_DELAY" in ''|*[!0-9]*) echo "IPSERVICE_DELAY harus angka (detik)"; exit 1 ;; esac
ls /etc/netplan/*.yaml >/dev/null 2>&1 || echo "(peringatan: tidak ada /etc/netplan/*.yaml - profil IP tidak bisa dibuat permanen; hubungi pembuat paket)"

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

echo "[3] Drop-in systemd (PYTHONPATH) untuk send dan ipservice; jeda mulai ${IPSERVICE_DELAY} dtk untuk ipservice"
for SVC in send ipservice; do
  if [ -f "/etc/systemd/system/$SVC.service" ]; then
    mkdir -p "/etc/systemd/system/$SVC.service.d"
    {
      echo "[Service]"
      echo "Environment=PYTHONPATH=$BROKER_DIR/overlay_persist"
      echo "ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'"
      if [ "$SVC" = "ipservice" ]; then
        # beri waktu dip.py menyelesaikan penggantian IP sebelum ipservice membaca IP (pengaman cadangan)
        echo "ExecStartPre=/bin/sleep $IPSERVICE_DELAY"
        echo "TimeoutStartSec=$((IPSERVICE_DELAY + 120))"
      fi
    } > "/etc/systemd/system/$SVC.service.d/10-hardening.conf"
  else
    echo "    (peringatan: $SVC.service tidak ditemukan, dilewati)"
  fi
done

echo "[4] Penyimpan profil IP eth0 (path + timer systemd) -> menyalin /etc/netplan/*.yaml eth0 ke SD asli"
cat > /etc/systemd/system/broker-persist-net.service <<'EOT'
[Unit]
Description=Simpan profil IP eth0 (netplan) ke SD asli saat overlay aktif
ConditionPathExists=/usr/local/sbin/broker-persist
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/broker-persist network
EOT
cat > /etc/systemd/system/broker-persist-net.path <<'EOT'
[Unit]
Description=Pantau perubahan /etc/netplan (IP dari saklar DIP)

[Path]
PathChanged=/etc/netplan
Unit=broker-persist-net.service

[Install]
WantedBy=multi-user.target
EOT
cat > /etc/systemd/system/broker-persist-net.timer <<'EOT'
[Unit]
Description=Cadangan: periksa profil IP eth0 tiap menit (tidak menulis SD bila tidak ada perubahan)

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
echo "Selesai. BELUM ada overlay. Langkah berikut (panduan B1):"
echo "  sudo systemctl restart send ipservice"
echo "  sudo ./verify_overlay_persist.sh          (semua harus OK / SKIP)"
echo "  sudo raspi-config nonint do_overlayfs 0 && sudo reboot"
echo "Setelah boot cek:  findmnt -no FSTYPE /   (overlay)   dan   findmnt /media/root-ro"
