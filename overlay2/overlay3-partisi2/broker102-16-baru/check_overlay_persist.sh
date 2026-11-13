#!/bin/bash
# check_overlay_persist.sh (NEW FILE) -- status summary; safe to run at any time (read-only)
# Usage:  sudo ./check_overlay_persist.sh
BROKER="/home/alice/broker"; LOWER="/media/root-ro"
ok()   { printf "  [ OK ]  %s\n" "$1"; }
warn() { printf "  [WARN]  %s\n" "$1"; }
cmpf() { # cmpf <label> <live> <lower>
  if [ ! -e "$3" ]; then warn "$1: not present on the SD card (lower layer)"; elif cmp -s "$2" "$3"; then ok "$1: identical to the SD card copy"; else warn "$1: DIFFERS from the SD card copy (not persisted yet)"; fi; }
echo "== Overlay status =="
FS="$(findmnt -no FSTYPE / 2>/dev/null)"; echo "  root filesystem: $FS"
if [ "$FS" = "overlay" ]; then
  mountpoint -q "$LOWER" && ok "SD card (lower layer) mounted at $LOWER ($(findmnt -no OPTIONS $LOWER | cut -d, -f1))" || warn "$LOWER is NOT mounted - the package cannot work"
else echo "  (overlay is off - comparison with the SD card skipped)"; fi
echo "== Package installation =="
[ -x /usr/local/sbin/broker-persist ] && ok "broker-persist helper installed" || warn "broker-persist helper not installed"
[ -f /etc/sudoers.d/broker-persist ]  && ok "sudo rule installed"            || warn "sudo rule missing"
for S in send ipservice; do
  P="$(systemctl show $S -p Environment 2>/dev/null)"
  case "$P" in *overlay_persist*) ok "$S: PYTHONPATH overlay_persist active";; *) warn "$S: PYTHONPATH overlay_persist NOT active";; esac
done
systemctl cat ipservice 2>/dev/null | grep -q "broker-persist net" && ok "ipservice: start delay + network persist active" || warn "ipservice: start delay / network persist not active"
if [ "$FS" = "overlay" ] && mountpoint -q "$LOWER"; then
  echo "== Comparison with the SD card =="
  cmpf "config.json" "$BROKER/config.json" "$LOWER$BROKER/config.json"
  cmpf "bridge.conf" "$BROKER/conf.d/bridge.conf" "$LOWER$BROKER/conf.d/bridge.conf"
  cmpf "/etc/hostname" /etc/hostname "$LOWER/etc/hostname"
  if diff -rq /etc/netplan "$LOWER/etc/netplan" >/dev/null 2>&1; then ok "/etc/netplan: identical to the SD card copy"; else warn "/etc/netplan: DIFFERS from the SD card copy"; fi
fi
echo "== Network info =="
echo "  hostname : $(hostname)"; echo "  eth0 IP  : $(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}')"
grep -h "addresses" /etc/netplan/*.yaml 2>/dev/null | sed 's/^/  netplan  :/'
echo "== Last helper log entries =="; journalctl -t broker-persist -n 5 --no-pager 2>/dev/null | sed 's/^/  /'
