#!/bin/bash
__version__="1.0.0.(16) - overlay"
# =====================================================================================================================================
#      OVERLAY
#      Name                       : OVERLAY
#      Version                    : 1.0.0.(16) 
#      Date Created               : 11-10-2026
#      Updated                    : 11-10-2026
#      Author                     : Saifuddin
#      Notes                      : verify_overlay_persist.sh - checks that package B is installed correctly and whether the real SD matches the current state.
#                                   Usage:  sudo ./verify_overlay_persist.sh
#                                   Overlay OFF   : checks the installation (items that need the overlay are marked SKIP).
#                                   Overlay ACTIVE: also checks that the real SD == current state (config, bridge.conf, hostname, eth0 IP).
#                                   Run it on the master BEFORE the SD is cloned: there must be no FAIL. It only READS; it changes nothing.
# ======================================================================================================================================
HOME_BASE="alice"
BROKER="/home/${HOME_BASE}/broker"
LOWER="${LOWER:-/media/root-ro}"
NETPLAN_DIR="/etc/netplan"
FAIL=0; WARN=0
ok()   { echo "  OK     $*"; }
bad()  { echo "  FAIL   $*"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP   $*"; }
warn() { echo "  CHECK  $*"; WARN=$((WARN+1)); }

[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }
FS="$(findmnt -no FSTYPE / 2>/dev/null)"
OVL=0; [ "$FS" = "overlay" ] && OVL=1
echo "Root filesystem : $FS   (overlay = $([ $OVL = 1 ] && echo ACTIVE || echo off))"
echo "Hostname        : $(hostname)"
echo "IP eth0         : $(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | paste -sd' ' -)"
echo

echo "[1] Installation"
[ -x /usr/local/sbin/broker-persist ] && ok "helper /usr/local/sbin/broker-persist" || bad "helper not installed (run setup_overlay_persist.sh)"
grep -q 'network' /usr/local/sbin/broker-persist 2>/dev/null && ok "helper version 2 (supports the IP profile)" || bad "old helper version; re-run setup_overlay_persist.sh"
if [ -f /etc/sudoers.d/broker-persist ] && visudo -cf /etc/sudoers.d/broker-persist >/dev/null 2>&1; then ok "sudo rule valid"; else bad "sudo rule missing / invalid"; fi
for SVC in send ipservice; do
  D="/etc/systemd/system/$SVC.service.d/10-hardening.conf"
  if [ -f "$D" ] && grep -q "PYTHONPATH=$BROKER/overlay_persist" "$D"; then ok "drop-in $SVC (PYTHONPATH)"; else bad "drop-in $SVC missing"; fi
done
grep -q '^ExecStartPre=/bin/sleep' /etc/systemd/system/ipservice.service.d/10-hardening.conf 2>/dev/null \
  && ok "ipservice start delay: $(grep -o 'sleep [0-9]*' /etc/systemd/system/ipservice.service.d/10-hardening.conf) seconds" \
  || bad "ipservice start delay missing"
for U in broker-persist-net.path broker-persist-net.timer; do
  systemctl is-active --quiet "$U" && ok "$U active" || bad "$U not active"
done
echo

echo "[2] eth0 IP profile (netplan)"
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
  bad "no eth0 YAML in $NETPLAN_DIR (send the output of: ls -l /etc/netplan ; nmcli -f NAME,FILENAME connection show)"
else
  for f in "${ETHFILES[@]}"; do
    ok "YAML eth0: $f"
    CUR="$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | head -n1)"
    if [ -n "$CUR" ] && grep -q "${CUR%/*}" "$f"; then ok "active IP ${CUR%/*} is recorded in the YAML"; else warn "active IP ($CUR) not seen in the YAML; after the DIP switch changes, wait 1 minute and repeat"; fi
    if [ $OVL = 1 ]; then
      cmp -s "$f" "$LOWER$f" && ok "real SD == current view ($(basename "$f"))" || bad "real SD DIFFERS from the current view ($(basename "$f")); wait 1 minute and repeat"
    else
      skip "real SD comparison (overlay off)"
    fi
  done
fi
echo

echo "[3] Files that must be permanent"
if [ $OVL = 1 ]; then
  [ -d "$LOWER" ] && ok "$LOWER exists" || bad "$LOWER does not exist (the package cannot work)"
  for rel in config.json conf.d/bridge.conf; do
    cmp -s "$BROKER/$rel" "$LOWER$BROKER/$rel" && ok "$rel matches the real SD" || bad "$rel DIFFERS from the real SD"
  done
  cmp -s /etc/hostname "$LOWER/etc/hostname" && ok "/etc/hostname matches the real SD" || bad "/etc/hostname DIFFERS from the real SD"
  [ "$(hostname)" = "$(cat /etc/hostname 2>/dev/null)" ] && ok "running hostname == /etc/hostname" || warn "running hostname differs from /etc/hostname"
else
  skip "comparison with the real SD (overlay off)"
fi
echo

echo "[4] Broker services"
systemctl is-active --quiet send && ok "service send active" || warn "service send not active"
systemctl is-active --quiet ipservice && ok "service ipservice active" || warn "service ipservice not active (normal right after a reboot, wait for the start delay)"
echo
if [ $FAIL -eq 0 ]; then echo "RESULT: no FAIL ($WARN CHECK notes)."; else echo "RESULT: $FAIL FAIL, $WARN CHECK notes."; fi
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
