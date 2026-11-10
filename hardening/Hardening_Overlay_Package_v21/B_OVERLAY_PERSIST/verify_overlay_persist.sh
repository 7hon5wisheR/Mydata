#!/bin/bash
# ================================================================
#  verify_overlay_persist.sh  (FILE BARU)
#  Memeriksa apakah paket B terpasang benar dan apakah SD asli sinkron dengan kondisi sekarang.
#  Pakai:  sudo ./verify_overlay_persist.sh
#  - Overlay MATI : memeriksa instalasi (item yang butuh overlay ditandai SKIP).
#  - Overlay AKTIF: memeriksa juga bahwa SD asli == kondisi sekarang (config, bridge.conf, hostname, IP eth0).
#  Jalankan ini di master SEBELUM SD di-clone: semua harus OK (tidak ada GAGAL).
#  Skrip ini hanya MEMBACA; tidak mengubah apa pun.
# ================================================================
HOME_BASE="alice"
BROKER="/home/${HOME_BASE}/broker"
LOWER="${LOWER:-/media/root-ro}"
NETPLAN_DIR="/etc/netplan"
FAIL=0; WARN=0
ok()   { echo "  OK     $*"; }
bad()  { echo "  GAGAL  $*"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP   $*"; }
warn() { echo "  PERIKSA $*"; WARN=$((WARN+1)); }

[ "$EUID" -eq 0 ] || { echo "Jalankan dengan sudo"; exit 1; }
FS="$(findmnt -no FSTYPE / 2>/dev/null)"
OVL=0; [ "$FS" = "overlay" ] && OVL=1
echo "Root filesystem : $FS   (overlay = $([ $OVL = 1 ] && echo AKTIF || echo mati))"
echo "Hostname        : $(hostname)"
echo "IP eth0         : $(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | paste -sd' ' -)"
echo

echo "[1] Instalasi"
[ -x /usr/local/sbin/broker-persist ] && ok "helper /usr/local/sbin/broker-persist" || bad "helper belum terpasang (jalankan setup_overlay_persist.sh)"
grep -q 'network' /usr/local/sbin/broker-persist 2>/dev/null && ok "helper versi 2 (mendukung profil IP)" || bad "helper versi lama; jalankan ulang setup_overlay_persist.sh"
if [ -f /etc/sudoers.d/broker-persist ] && visudo -cf /etc/sudoers.d/broker-persist >/dev/null 2>&1; then ok "aturan sudo valid"; else bad "aturan sudo tidak ada / tidak valid"; fi
for SVC in send ipservice; do
  D="/etc/systemd/system/$SVC.service.d/10-hardening.conf"
  if [ -f "$D" ] && grep -q "PYTHONPATH=$BROKER/overlay_persist" "$D"; then ok "drop-in $SVC (PYTHONPATH)"; else bad "drop-in $SVC tidak ada"; fi
done
grep -q '^ExecStartPre=/bin/sleep' /etc/systemd/system/ipservice.service.d/10-hardening.conf 2>/dev/null \
  && ok "jeda mulai ipservice: $(grep -o 'sleep [0-9]*' /etc/systemd/system/ipservice.service.d/10-hardening.conf) detik" \
  || bad "jeda mulai ipservice belum ada"
for U in broker-persist-net.path broker-persist-net.timer; do
  systemctl is-active --quiet "$U" && ok "$U aktif" || bad "$U tidak aktif"
done
echo

echo "[2] Profil IP eth0 (netplan)"
ETHFILES=()
for f in "$NETPLAN_DIR"/*.yaml; do
  [ -f "$f" ] || continue
  if python3 -I -c '
import sys,yaml
d=yaml.safe_load(open(sys.argv[1]))
e=((d.get("network") or {}).get("ethernets")) or {}
sys.exit(0 if any(k=="eth0" or ((v or {}).get("match") or {}).get("name")=="eth0" or ((v or {}).get("networkmanager") or {}).get("name")=="netplan-eth0" for k,v in e.items()) else 1)
' "$f" 2>/dev/null; then ETHFILES+=("$f"); fi
done
if [ ${#ETHFILES[@]} -eq 0 ]; then
  bad "tidak ada YAML eth0 di $NETPLAN_DIR (kirim hasil: ls -l /etc/netplan ; nmcli -f NAME,FILENAME connection show)"
else
  for f in "${ETHFILES[@]}"; do
    ok "YAML eth0: $f"
    CUR="$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | head -n1)"
    if [ -n "$CUR" ] && grep -q "${CUR%/*}" "$f"; then ok "IP aktif ${CUR%/*} tercatat di YAML"; else warn "IP aktif ($CUR) tidak terlihat di YAML; setelah saklar DIP diubah, tunggu 1 menit lalu ulangi"; fi
    if [ $OVL = 1 ]; then
      cmp -s "$f" "$LOWER$f" && ok "SD asli == tampilan sekarang ($(basename "$f"))" || bad "SD asli BERBEDA dari tampilan sekarang ($(basename "$f")); tunggu 1 menit lalu ulangi"
    else
      skip "perbandingan SD asli (overlay mati)"
    fi
  done
fi
echo

echo "[3] File yang harus permanen"
if [ $OVL = 1 ]; then
  [ -d "$LOWER" ] && ok "$LOWER ada" || bad "$LOWER tidak ada (paket tidak bisa bekerja)"
  for rel in config.json conf.d/bridge.conf; do
    cmp -s "$BROKER/$rel" "$LOWER$BROKER/$rel" && ok "$rel sama dengan SD asli" || bad "$rel BERBEDA dari SD asli"
  done
  cmp -s /etc/hostname "$LOWER/etc/hostname" && ok "/etc/hostname sama dengan SD asli" || bad "/etc/hostname BERBEDA dari SD asli"
  [ "$(hostname)" = "$(cat /etc/hostname 2>/dev/null)" ] && ok "hostname berjalan == /etc/hostname" || warn "hostname berjalan berbeda dari /etc/hostname"
else
  skip "perbandingan dengan SD asli (overlay mati)"
fi
echo

echo "[4] Layanan broker"
systemctl is-active --quiet send && ok "service send aktif" || warn "service send tidak aktif"
systemctl is-active --quiet ipservice && ok "service ipservice aktif" || warn "service ipservice tidak aktif (normal bila baru reboot, tunggu jeda mulai)"
echo
if [ $FAIL -eq 0 ]; then echo "HASIL: tidak ada GAGAL ($WARN catatan PERIKSA)."; else echo "HASIL: $FAIL GAGAL, $WARN catatan PERIKSA."; fi
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
