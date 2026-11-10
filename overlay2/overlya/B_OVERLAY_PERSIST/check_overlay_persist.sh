#!/bin/bash
# check_overlay_persist.sh (FILE BARU) -- ringkasan status; aman dijalankan kapan saja (hanya membaca)
# Pakai:  sudo ./check_overlay_persist.sh
BROKER="/home/alice/broker"; LOWER="/media/root-ro"
ok()   { printf "  [ OK ]  %s\n" "$1"; }
warn() { printf "  [WARN]  %s\n" "$1"; }
cmpf() { # cmpf <label> <live> <lower>
  if [ ! -e "$3" ]; then warn "$1: tidak ada di SD asli"; elif cmp -s "$2" "$3"; then ok "$1: sama dengan SD asli"; else warn "$1: BEDA dengan SD asli (belum tersimpan permanen)"; fi; }
echo "== Status overlay =="
FS="$(findmnt -no FSTYPE / 2>/dev/null)"; echo "  root filesystem: $FS"
if [ "$FS" = "overlay" ]; then
  mountpoint -q "$LOWER" && ok "SD asli ter-mount di $LOWER ($(findmnt -no OPTIONS $LOWER | cut -d, -f1))" || warn "$LOWER TIDAK ADA - paket tidak bisa bekerja"
else echo "  (overlay mati - perbandingan dengan SD asli dilewati)"; fi
echo "== Pemasangan paket =="
[ -x /usr/local/sbin/broker-persist ] && ok "helper broker-persist terpasang" || warn "helper broker-persist belum terpasang"
[ -f /etc/sudoers.d/broker-persist ]  && ok "aturan sudo terpasang"           || warn "aturan sudo belum ada"
for S in send ipservice; do
  P="$(systemctl show $S -p Environment 2>/dev/null)"
  case "$P" in *overlay_persist*) ok "$S: PYTHONPATH overlay_persist aktif";; *) warn "$S: PYTHONPATH overlay_persist TIDAK aktif";; esac
done
systemctl cat ipservice 2>/dev/null | grep -q "broker-persist net" && ok "ipservice: jeda + simpan jaringan aktif" || warn "ipservice: jeda/simpan jaringan belum aktif"
if [ "$FS" = "overlay" ] && mountpoint -q "$LOWER"; then
  echo "== Perbandingan dengan SD asli =="
  cmpf "config.json" "$BROKER/config.json" "$LOWER$BROKER/config.json"
  cmpf "bridge.conf" "$BROKER/conf.d/bridge.conf" "$LOWER$BROKER/conf.d/bridge.conf"
  cmpf "/etc/hostname" /etc/hostname "$LOWER/etc/hostname"
  if diff -rq /etc/netplan "$LOWER/etc/netplan" >/dev/null 2>&1; then ok "/etc/netplan: sama dengan SD asli"; else warn "/etc/netplan: BEDA dengan SD asli"; fi
fi
echo "== Info jaringan =="
echo "  hostname : $(hostname)"; echo "  IP eth0  : $(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}')"
grep -h "addresses" /etc/netplan/*.yaml 2>/dev/null | sed 's/^/  netplan  :/'
echo "== Log helper terakhir =="; journalctl -t broker-persist -n 5 --no-pager 2>/dev/null | sed 's/^/  /'
