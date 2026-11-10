#!/bin/bash
__version__="1.0.0.(16) - overlay"
# =====================================================================================================================================
#      OVERLAY
#      Name                       : OVERLAY
#      Version                    : 1.0.0.(16) 
#      Date Created               : 11-10-2026
#      Updated                    : 11-10-2026
#      Author                     : Saifuddin
#      Notes                      : test_ip_persist.sh - REMOTE Test 5, without touching the DIP switch.
#                                   Mimics dip.py's commands (nmcli modify/down/up on netplan-eth0) with a test IP, then checks:
#                                     (a) NetworkManager writes the new IP to /etc/netplan/*.yaml
#                                     (b) the helper copies it to the real SD (/media/root-ro) when the overlay is active
#                                   Then restores the original IP automatically.
#                                   Usage:  sudo ./test_ip_persist.sh                  # test + restore the IP (safe)
#                                           sudo ./test_ip_persist.sh --reboot         # test, THEN reboot with the test IP (boot test)
#                                           sudo ./test_ip_persist.sh 192.168.137.198  # use another test IP
#                                   - Runs in the background; the AnyDesk/SSH session may drop for ~10-30 s. Result: /tmp/iptest.log
#                                   - Default test IP 192.168.137.199 (outside .101-.126, so ipservice does not change hostname/bridge.conf).
#                                   - ipservice is stopped during the test (so it does not reboot), then started again.
#                                   - Can run with the overlay OFF (only part a) or ACTIVE (a and b).
#                                   - --reboot mode: after boot, dip.py restores the switch IP. Proof the Pi booted with the test IP:
#                                       sudo journalctl -u dip.service -b | grep "Current IP"      -> must show the test IP
# ======================================================================================================================================
CONN="netplan-eth0"; IFACE="eth0"
LOWER="${LOWER:-/media/root-ro}"
LOG=/tmp/iptest.log
TEST_IP="192.168.137.199"; DO_REBOOT=0
for a in "$@"; do
  case "$a" in --reboot) DO_REBOOT=1 ;; --child) ;; [0-9]*.[0-9]*.[0-9]*.[0-9]*) TEST_IP="$a" ;; *) echo "Unknown argument: $a"; exit 1 ;; esac
done
[ "$EUID" -eq 0 ] || { echo "Run with sudo"; exit 1; }

# --- run in the background so it still finishes if the remote session drops ---
if [ "${IPTEST_BG:-0}" != "1" ]; then
  nmcli -g NAME connection show | grep -qx "$CONN" || { echo "Connection $CONN not found"; exit 1; }
  [ "$(nmcli -g ipv4.method connection show "$CONN")" = "manual" ] || { echo "ipv4.method is not manual; test cancelled"; exit 1; }
  echo "Test started in the background. The connection may drop for ~10-30 seconds."
  echo "Wait about 4 minutes, then read the result:  cat $LOG"
  IPTEST_BG=1 nohup "$0" "$@" --child >"$LOG" 2>&1 &
  exit 0
fi

FAILS=0
ok()  { echo "  OK     $*"; }
bad() { echo "  FAIL   $*"; FAILS=$((FAILS+1)); }
ts()  { date '+%H:%M:%S'; }
OVL=0; [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ] && OVL=1

# --- initial state (used to restore exactly) ---
ORIG_ADDR="$(nmcli -g ipv4.addresses connection show "$CONN" | head -n1)"
ORIG_GW="$(nmcli -g ipv4.gateway connection show "$CONN")"
ORIG_DNS="$(nmcli -g ipv4.dns connection show "$CONN")"
ORIG_IP="${ORIG_ADDR%/*}"; PREFIX="${ORIG_ADDR#*/}"
echo "[$(ts)] Overlay: $([ $OVL = 1 ] && echo ACTIVE || echo off) | original IP: $ORIG_ADDR gw=$ORIG_GW dns=$ORIG_DNS | test IP: $TEST_IP/$PREFIX"
[ -n "$ORIG_IP" ] && [ -n "$PREFIX" ] || { echo "Could not read the original IP; cancelled"; exit 1; }

apply_ip() {   # mimics dip.py
  nmcli connection modify "$CONN" ipv4.method manual ipv4.addresses "$1/$PREFIX" ipv4.gateway "$ORIG_GW" ipv4.dns "$ORIG_DNS" \
    && nmcli connection down "$CONN"; sleep 2; nmcli connection up "$CONN"
}
wait_for() {   # wait_for <seconds> <command...>
  local t="$1"; shift; local i=0
  while [ $i -lt "$t" ]; do "$@" && return 0; sleep 2; i=$((i+2)); done; return 1
}
upper_has() { grep -qs "$1" /etc/netplan/*.yaml; }
lower_has() { grep -qs "$1" "$LOWER"/etc/netplan/*.yaml; }

systemctl stop ipservice 2>/dev/null; echo "[$(ts)] ipservice stopped temporarily"

restore() {
  echo "[$(ts)] Restoring the original IP $ORIG_ADDR"
  apply_ip "$ORIG_IP"
  wait_for 30 upper_has "$ORIG_IP" && ok "/etc/netplan YAML contains $ORIG_IP again" || bad "/etc/netplan YAML does not contain $ORIG_IP after restoring"
  if [ $OVL = 1 ]; then
    wait_for 150 lower_has "$ORIG_IP" && ok "real SD contains $ORIG_IP again" || bad "real SD does not contain $ORIG_IP (wait 1-2 minutes, then: sudo ./verify_overlay_persist.sh)"
  fi
  systemctl start ipservice 2>/dev/null; echo "[$(ts)] ipservice started again"
}

echo "[$(ts)] Step 1: change the IP to $TEST_IP (like dip.py)"
apply_ip "$TEST_IP"
if wait_for 30 upper_has "$TEST_IP"; then
  ok "(a) NetworkManager wrote the test IP to /etc/netplan  <- core assumption PROVEN"
  grep -l "$TEST_IP" /etc/netplan/*.yaml | sed 's/^/         file: /'
else
  bad "(a) /etc/netplan does NOT contain the test IP. dip.py does not save the IP to netplan; design v2 cannot be used. Send: ls -l /etc/netplan ; nmcli -f NAME,FILENAME connection show"
  restore; echo; echo "RESULT: FAIL ($FAILS)"; exit 1
fi

if [ $OVL = 1 ]; then
  echo "[$(ts)] Step 2: waiting for the helper to copy to the real SD (max 150 seconds)"
  if wait_for 150 lower_has "$TEST_IP"; then
    ok "(b) real SD ($LOWER) contains the test IP  <- the IP will survive a reboot / power cut"
  else
    bad "(b) real SD does NOT contain the test IP. Check: systemctl status broker-persist-net.path broker-persist-net.timer ; journalctl -t broker-persist"
  fi
else
  echo "[$(ts)] Overlay off: part (b) skipped (data is already directly on the SD)"
fi

if [ "$DO_REBOOT" = "1" ] && [ $FAILS -eq 0 ]; then
  echo "[$(ts)] --reboot mode: rebooting with the test IP. After boot, dip.py restores the switch IP."
  echo "         Proof: sudo journalctl -u dip.service -b | grep 'Current IP'  (must show $TEST_IP)"
  echo; echo "RESULT BEFORE REBOOT: all OK"
  sync; sleep 3; reboot; exit 0
fi

restore
echo
[ $FAILS -eq 0 ] && echo "RESULT: PASS - the test IP was saved permanently and the original IP has been restored." || echo "RESULT: $FAILS FAIL - see the FAIL lines above."
exit $([ $FAILS -eq 0 ] && echo 0 || echo 1)
