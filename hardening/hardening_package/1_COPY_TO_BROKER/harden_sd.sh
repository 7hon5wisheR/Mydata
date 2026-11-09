#!/bin/bash
# ================================================================
#  harden_sd.sh  (FILE BARU)  -- lindungi SD card dari korup saat mati lampu
#  Tanpa overlay, tanpa partisi baru, TANPA mengubah kode Python yang ada.
#  Aman dijalankan berulang.   Pakai:  sudo ./harden_sd.sh   lalu: sudo reboot
# ================================================================
set -e
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
[ -f "$BROKER_DIR/hardening/sitecustomize.py" ] || { echo "Folder $BROKER_DIR/hardening belum ada"; exit 1; }

if [ "$(findmnt -no FSTYPE /)" = "overlay" ]; then
  echo "Overlay aktif. Matikan dulu: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"; exit 1
fi

CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
FSTAB=/etc/fstab
cp -n "$CMD" "$CMD.bak"; cp -n "$FSTAB" "$FSTAB.bak"

echo "[1] Penulisan atomik untuk config/status/rfid/bridge.conf (via PYTHONPATH, kode tidak diubah)"
for SVC in send ipservice; do
  if [ -f "/etc/systemd/system/$SVC.service" ]; then
    mkdir -p "/etc/systemd/system/$SVC.service.d"
    cat > "/etc/systemd/system/$SVC.service.d/10-hardening.conf" <<EOT
[Service]
Environment=PYTHONPATH=$BROKER_DIR/hardening
ExecStartPre=-/bin/sh -c 'rm -f $BROKER_DIR/.*.tmp.* $BROKER_DIR/conf.d/.*.tmp.*'
EOT
  else
    echo "    (peringatan: $SVC.service tidak ditemukan, dilewati)"
  fi
done
systemctl daemon-reload

echo "[2] cmdline.txt - opsional lanjutan (lihat dokumen 2), default DILEWATI"
if [ "${ENABLE_ROOTFLAGS:-0}" = "1" ]; then
  if ! grep -q "rootflags=" "$CMD"; then
    sed -i '1 s/$/ rootflags=data=journal,commit=1/' "$CMD"
  fi
else
  echo "    dilewati (memang tidak perlu untuk instalasi standar)"
fi

echo "[3] noatime di fstab"
sed -i -E '/^[^#].*[[:space:]](\/|\/boot\/firmware|\/boot)[[:space:]]+(ext4|vfat)/ { /noatime/! s/defaults/defaults,noatime/ }' "$FSTAB"

echo "[4] logs aplikasi ke RAM"
UID_=$(id -u $HOME_BASE); GID_=$(id -g $HOME_BASE)
mkdir -p "$BROKER_DIR/logs"
grep -q "$BROKER_DIR/logs" "$FSTAB" || \
  echo "tmpfs $BROKER_DIR/logs tmpfs defaults,nofail,noatime,size=100m,uid=$UID_,gid=$GID_,mode=0755 0 0" >> "$FSTAB"

echo "[5] journal sistem ke RAM"
mkdir -p /etc/systemd/journald.conf.d
printf "[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n" > /etc/systemd/journald.conf.d/volatile.conf

echo "[6] matikan swap"
dphys-swapfile swapoff 2>/dev/null || true
systemctl disable dphys-swapfile 2>/dev/null || true

echo "[7] mosquitto: tanpa persistence & log file"
M=/etc/mosquitto/mosquitto.conf
if [ -f "$M" ]; then
  cp -n "$M" "$M.bak"
  sed -i 's#^persistence true#persistence false#; s#^log_dest file .*#log_dest syslog#' "$M"
  systemctl restart mosquitto || true
fi

echo
echo "Selesai. Sekarang: sudo reboot"
echo "Cek setelah reboot:"
echo "  findmnt $BROKER_DIR/logs"
echo "  sudo systemctl show send -p Environment | grep PYTHONPATH"
