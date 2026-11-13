#!/bin/bash
__version__="1.0.0.(18) - overlay2"
# =====================================================================================================================================
#      OVERLAY2
#      Name                       : OVERLAY2
#      Version                    : 1.0.0.(18) 
#      Date Created               : 11-13-2026
#      Updated                    : 11-13-2026
#      Author                     : Saifuddin
#      Notes                      : check_overlay2.sh - verify Overlay2 (read-only, safe). Usage: sudo ./check_overlay2.sh
# ======================================================================================================================================
[ -f /etc/overlay2.conf ] && . /etc/overlay2.conf
BROKER="${BROKER_DIR:-/home/alice/broker}"; MNT="${PERSIST_DIR:-/boot/firmware}"; STORE=$MNT/broker; F=0
ok(){ echo "[ OK ] $*"; }; bad(){ echo "[FAIL] $*"; F=$((F+1)); }
findmnt -no FSTYPE / | grep -q overlay && ok "root is overlay (volatile SD)" || bad "overlay is NOT active (root is $(findmnt -no FSTYPE /))"
mountpoint -q $MNT && ok "$MNT mounted ($(findmnt -no SOURCE,FSTYPE $MNT))" || bad "$MNT NOT mounted"
findmnt -no OPTIONS $MNT | tr "," "\n" | grep -qx rw && ok "$MNT is rw" || bad "$MNT not rw (boot read-only?)"
sudo -u alice test -w "$STORE" && ok "alice can write to $STORE" || bad "alice cannot write to $STORE"
for p in "$BROKER/config.json" "$BROKER/conf.d/bridge.conf"; do
  [ -L "$p" ] && ok "$p -> $(readlink $p)" || bad "$p is not a symlink"
  [ -s "$p" ] && ok "$p readable, non-empty" || bad "$p missing/empty"
done
python3 -c 'import json;json.load(open("'$STORE'/config.json"))' 2>/dev/null && ok "config.json is valid JSON" || bad "config.json invalid"
sudo -u mosquitto test -r "$STORE/conf.d/bridge.conf" && ok "mosquitto can read bridge.conf" || bad "mosquitto cannot read bridge.conf"
grep -qx "include_dir $STORE/conf.d" /etc/mosquitto/mosquitto.conf && ok "mosquitto include_dir = $STORE/conf.d" || bad "mosquitto include_dir is wrong"
systemctl is-active --quiet mosquitto && ok "mosquitto active" || bad "mosquitto not active"
systemctl is-enabled overlay2-prepare >/dev/null 2>&1 && ok "overlay2-prepare enabled" || bad "overlay2-prepare not enabled"
grep -q 'mode=NORMAL' /run/overlay2.state 2>/dev/null && ok "mode NORMAL ($(cat /run/overlay2.state))" || bad "mode is not NORMAL: $(cat /run/overlay2.state 2>/dev/null) (DEGRADED = persistent store missing)"
for s in send ipservice; do systemctl show $s -p Environment 2>/dev/null | grep -q persist_store && ok "$s uses atomic writes" || bad "$s has no PYTHONPATH persist_store"; done
sudo -n true 2>/dev/null && ok "sudo needs no password" || bad "sudo asks for a password (the :8082 overlay button will fail)"
[ -f /etc/sudoers.d/apps ] && ok "sudoers /etc/sudoers.d/apps present" || bad "sudoers file missing"
echo "hostname: $(hostname) | eth0: $(ip -4 -o addr show eth0 | awk '{print $4}')"
echo "bridge name: $(grep -m1 -E '^[[:space:]]*connection' $STORE/conf.d/bridge.conf)"
echo; [ $F -eq 0 ] && echo "RESULT: ALL OK" || echo "RESULT: $F FAIL"; exit $F
