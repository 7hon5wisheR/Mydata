#!/bin/bash
__version__="1.0.0.(18) - overlay2"
# =====================================================================================================================================
#      OVERLAY2
#      Name                       : OVERLAY2
#      Version                    : 1.0.0.(18) 
#      Date Created               : 11-13-2026
#      Updated                    : 11-13-2026
#      Author                     : Saifuddin
#      Notes                      : setup_overlay2.sh - builds Overlay2 from scratch, fully automatic, no prompts (after the first command):
#                                     stage 1 (overlay OFF): sudoers (no password for the :8082 overlay toggle), packages, persistent USB
#                                       (optional format), fstab mount, master defaults, overlay2-prepare service, atomic-write drop-ins,
#                                       mosquitto.conf -> include_dir /mnt/persist/broker/conf.d, mosquitto test  -> reboot
#                                     stage 2 (automatic after boot): raspi-config nonint do_overlayfs 0 -> reboot -> overlay ON
#                                   Persistent store (default = firmware, as recommended by the JSON-persistence guide for a quick setup):
#                                     firmware : /boot/firmware/broker  (FAT boot partition of the SD card, must be mounted rw; no extra hardware)
#                                     usb      : /mnt/persist/broker    (ext4 USB drive labelled PERSIST; stronger against power loss)
#                                   Usage:  sudo ./setup_overlay2.sh                    # firmware store (default)
#                                           sudo ./setup_overlay2.sh --usb              # USB labelled PERSIST must already exist
#                                           sudo ./setup_overlay2.sh --format /dev/sda  # format the USB first (ERASES it, asks YES)
#                                           sudo ./setup_overlay2.sh --format /dev/sda --yes   # no question
#                                           sudo ./setup_overlay2.sh --status
#                                   Run from the broker folder (the folder containing apps.py is the parent of overlay2/).
# ======================================================================================================================================
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$HERE/$(basename "${BASH_SOURCE[0]}")"
FILES="$HERE/files"
HOME_BASE="${HOME_BASE:-alice}"
BROKER_DIR="${BROKER_DIR:-/home/${HOME_BASE}/broker}"
STORE_MODE="firmware"
MNT="${MNT:-$( ( . /etc/overlay2.conf 2>/dev/null; echo "${PERSIST_DIR:-/boot/firmware}" ) )}"
STORE="$MNT/broker"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/apps}"
STATE_DIR="${STATE_DIR:-/var/lib/overlay2}"
STAGE_FILE="$STATE_DIR/stage"
UNIT2="/etc/systemd/system/overlay2-stage2.service"
MOSQ="${MOSQ:-/etc/mosquitto/mosquitto.conf}"
AUTO_REBOOT="${AUTO_REBOOT:-yes}"
REBOOT_DELAY="${REBOOT_DELAY:-10}"
FORMAT_DEV=""; ASSUME_YES=0

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  [OK] $1${NC}"; }
info() { echo -e "${CYAN}  [..] $1${NC}"; }
err()  { echo -e "${RED}  [!!] $1${NC}"; }
step() { echo -e "${BLUE}[$1] $2${NC}"; }
header() { echo -e "\n${BLUE}================================================================${NC}\n  $1\n${BLUE}================================================================${NC}"; }

is_overlay_active() { [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ]; }
stage_now() { cat "$STAGE_FILE" 2>/dev/null || true; }
do_reboot() {
  if [ "$AUTO_REBOOT" = "yes" ]; then info "Rebooting in ${REBOOT_DELAY} seconds (Ctrl+C to cancel)..."; sleep "$REBOOT_DELAY"; systemctl reboot; exit 0; fi
  info "Reboot manually now: sudo reboot"
}
remove_unit2() { systemctl disable overlay2-stage2.service >/dev/null 2>&1 || true; rm -f "$UNIT2"; systemctl daemon-reload >/dev/null 2>&1 || true; }

show_status() {
  header "OVERLAY2 STATUS"
  echo "  root filesystem : $(findmnt -no FSTYPE / 2>/dev/null)   (overlay = ACTIVE, ext4 = off)"
  echo "  setup stage     : $(stage_now)"
  echo "  persistent store: $(mountpoint -q "$MNT" && findmnt -no SOURCE,FSTYPE,OPTIONS "$MNT" || echo 'not mounted')"
  echo "  prepare state   : $(cat /run/overlay2.state 2>/dev/null || echo none)"
  [ -f "$SUDOERS_FILE" ] && echo "  sudoers         : present ($SUDOERS_FILE)" || echo "  sudoers         : MISSING"
  grep -m1 '^include_dir' "$MOSQ" 2>/dev/null | sed 's/^/  mosquitto       : /'
}

# ---------------------------------------------------------------- stage 2
stage2() {
  header "OVERLAY2 stage 2 of 2 - enabling the overlay"
  if is_overlay_active; then ok "Overlay is already ACTIVE."; remove_unit2; exit 0; fi
  step "S2.1" "raspi-config nonint do_overlayfs 0 ..."
  if raspi-config nonint do_overlayfs 0; then
    echo "overlay_enabled" > "$STAGE_FILE"; remove_unit2
    ok "Overlay set to ACTIVE - takes effect after the reboot"
    info "After boot, check:  sudo $HERE/check_overlay2.sh   (all [ OK ])"
    do_reboot
  else
    err "raspi-config do_overlayfs 0 FAILED. The overlay was NOT enabled."; exit 1
  fi
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --status) [ "$EUID" -eq 0 ] || true; show_status; exit 0 ;;
    --stage2) [ "$(stage_now)" = "persist_installed" ] && ! is_overlay_active && stage2; exit 0 ;;
    --format) FORMAT_DEV="${2:-}"; STORE_MODE="usb"; shift ;;
    --usb) STORE_MODE="usb" ;;
    --firmware) STORE_MODE="firmware" ;;
    --yes) ASSUME_YES=1 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac; shift
done

[ "$EUID" -eq 0 ] || { err "Run with sudo!  Example: sudo $SELF"; exit 1; }
if [ "$STORE_MODE" = "usb" ]; then MNT="/mnt/persist"; else MNT="/boot/firmware"; fi
STORE="$MNT/broker"
if is_overlay_active; then
  header "Overlay is already ACTIVE"
  info "Nothing to do. To turn it off use the disable button on :8082 or: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"
  exit 0
fi
[ "$(stage_now)" = "persist_installed" ] && stage2

# ---------------------------------------------------------------- stage 1
header "OVERLAY2 stage 1 of 2 - sudo, persistent store ($STORE_MODE), mosquitto"

step "1" "Checking package files ..."
MISSING=0
for f in "$BROKER_DIR/apps.py" "$FILES/overlay2-prepare" "$FILES/overlay2-prepare.service" "$FILES/sitecustomize.py" "$FILES/mosquitto.conf.template"; do
  [ -f "$f" ] || { err "not found: $f"; MISSING=1; }
done
[ "$MISSING" = "0" ] || { err "Incomplete package. Cancelled."; exit 1; }
[ -s "$BROKER_DIR/config.json" ] && [ -s "$BROKER_DIR/conf.d/bridge.conf" ] || [ -d "$BROKER_DIR/.defaults" ] || { err "No config.json / conf.d/bridge.conf in $BROKER_DIR. Cancelled."; exit 1; }
ok "package files complete"

step "2" "Passwordless sudo for $HOME_BASE (needed by the :8082 overlay enable/disable button): $SUDOERS_FILE"
TMP="$(mktemp)"; echo "$HOME_BASE ALL=(ALL) NOPASSWD: ALL" > "$TMP"
if visudo -c -f "$TMP" >/dev/null 2>&1; then
  install -m 440 -o root -g root "$TMP" "$SUDOERS_FILE"; ok "$HOME_BASE ALL=(ALL) NOPASSWD: ALL (mode 440)"
else rm -f "$TMP"; err "Invalid sudoers syntax. Cancelled."; exit 1; fi
rm -f "$TMP"
chmod +x "$HERE"/*.sh "$FILES/overlay2-prepare"

step "3" "Packages (python3-yaml for the IP restore$([ "$STORE_MODE" = usb ] && echo ', parted/e2fsprogs for formatting')) ..."
python3 -c 'import yaml' 2>/dev/null || apt-get install -y python3-yaml
if [ -n "$FORMAT_DEV" ]; then command -v parted >/dev/null || apt-get install -y parted; command -v mkfs.ext4 >/dev/null || apt-get install -y e2fsprogs; fi
ok "packages ready"

if [ "$STORE_MODE" = "usb" ]; then
step "4" "Persistent USB drive (ext4, label PERSIST)"
if [ -n "$FORMAT_DEV" ]; then
  [ -b "$FORMAT_DEV" ] || { err "$FORMAT_DEV is not a block device"; lsblk -o NAME,SIZE,TRAN,LABEL,MOUNTPOINT; exit 1; }
  ROOTDEV="$(lsblk -no PKNAME "$(findmnt -no SOURCE / 2>/dev/null)" 2>/dev/null || true)"
  case "$FORMAT_DEV" in *mmcblk*) err "Refusing to format the SD card"; exit 1 ;; esac
  [ -n "$ROOTDEV" ] && [ "$FORMAT_DEV" = "/dev/$ROOTDEV" ] && { err "Refusing to format the system disk"; exit 1; }
  lsblk -o NAME,SIZE,TRAN,MODEL,LABEL "$FORMAT_DEV"
  if [ "$ASSUME_YES" != "1" ]; then read -r -p "ALL DATA on $FORMAT_DEV will be ERASED. Type YES: " ans; [ "$ans" = "YES" ] || { err "Cancelled"; exit 1; }; fi
  umount "${FORMAT_DEV}"* 2>/dev/null || true
  parted -s "$FORMAT_DEV" mklabel msdos mkpart primary ext4 1MiB 100%
  sleep 2; PART="${FORMAT_DEV}1"; [ -b "$PART" ] || PART="${FORMAT_DEV}p1"
  mkfs.ext4 -F -L PERSIST -m 0 "$PART"
  ok "formatted $PART (label PERSIST)"
fi
blkid -L PERSIST >/dev/null 2>&1 || { err "No drive labelled PERSIST. Plug it in, or use: sudo $SELF --format /dev/sdX"; exit 1; }
ok "found drive: $(blkid -L PERSIST)"

step "5" "Mounting by label at $MNT (nofail) via fstab"
mkdir -p "$MNT"; cp -n /etc/fstab /etc/fstab.bak.overlay2
grep -q "LABEL=PERSIST" /etc/fstab || echo "LABEL=PERSIST $MNT ext4 defaults,noatime,nofail,x-systemd.device-timeout=20 0 2" >> /etc/fstab
systemctl daemon-reload
mountpoint -q "$MNT" || mount "$MNT" || { err "mount failed"; exit 1; }
touch "$MNT/.w.$$" && rm -f "$MNT/.w.$$" || { err "$MNT is not writable"; exit 1; }
ok "$MNT mounted and writable"
else
step "4" "Persistent store on the FAT boot partition: $MNT"
mountpoint -q "$MNT" || { err "$MNT is not a mount point (this Pi may use /boot). Use --usb instead."; exit 1; }
findmnt -no OPTIONS "$MNT" | tr ',' '\n' | grep -qx rw || { err "$MNT is mounted READ-ONLY. Check raspi-config boot read-only setting; use --usb instead."; exit 1; }
grep -E "^[^#][^[:space:]]*[[:space:]]+$MNT[[:space:]]" /etc/fstab >/dev/null || { err "No fstab line for $MNT. Use --usb instead."; exit 1; }
if grep -E "^[^#][^[:space:]]*[[:space:]]+$MNT[[:space:]]" /etc/fstab | awk '{print $4}' | tr ',' '\n' | grep -qx ro; then
  err "fstab mounts $MNT read-only (boot read-only is on). Turn it off in raspi-config or use --usb."; exit 1; fi
ok "$MNT is mounted read-write"

step "5" "FAT has no Unix owners: mount $MNT as $HOME_BASE (uid/gid/umask in fstab) so the apps can write"
cp -n /etc/fstab /etc/fstab.bak.overlay2
if ! grep -E "^[^#][^[:space:]]*[[:space:]]+$MNT[[:space:]]" /etc/fstab | grep -q 'uid='; then
  UID_="$(id -u "$HOME_BASE")"; GID_="$(id -g "$HOME_BASE")"
  sed -i -E "\|^[^#][^[:space:]]*[[:space:]]+$MNT[[:space:]]| s/^([^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+)([^[:space:]]+)/\1\2,uid=$UID_,gid=$GID_,umask=0022/" /etc/fstab
  systemctl daemon-reload
  if ! mount -o remount "$MNT" 2>/dev/null; then err "remount of $MNT failed; restoring fstab"; cp -f /etc/fstab.bak.overlay2 /etc/fstab; systemctl daemon-reload; exit 1; fi
fi
sudo -u "$HOME_BASE" touch "$MNT/.wa.$$" 2>/dev/null && rm -f "$MNT/.wa.$$" \
  || { err "$HOME_BASE cannot write to $MNT; restoring fstab"; cp -f /etc/fstab.bak.overlay2 /etc/fstab; systemctl daemon-reload; mount -o remount "$MNT" || true; exit 1; }
ok "$HOME_BASE can write to $MNT"
fi

step "6" "Master defaults on the SD (read-only copies used for seeding and degraded mode)"
mkdir -p "$BROKER_DIR/.defaults"
[ -f "$BROKER_DIR/config.json" ] && [ ! -L "$BROKER_DIR/config.json" ] && cp -n "$BROKER_DIR/config.json" "$BROKER_DIR/.defaults/config.json" || true
[ -f "$BROKER_DIR/conf.d/bridge.conf" ] && [ ! -L "$BROKER_DIR/conf.d/bridge.conf" ] && cp -n "$BROKER_DIR/conf.d/bridge.conf" "$BROKER_DIR/.defaults/bridge.conf" || true
[ -s "$BROKER_DIR/.defaults/config.json" ] && [ -s "$BROKER_DIR/.defaults/bridge.conf" ] || { err "Master defaults incomplete in $BROKER_DIR/.defaults"; exit 1; }
chown -R "$HOME_BASE:$HOME_BASE" "$BROKER_DIR/.defaults"
ok "defaults saved in $BROKER_DIR/.defaults"

step "7" "Installing overlay2-prepare, its service and the atomic-write module"
install -m 755 "$FILES/overlay2-prepare" /usr/local/sbin/overlay2-prepare
install -m 644 "$FILES/overlay2-prepare.service" /etc/systemd/system/overlay2-prepare.service
mkdir -p "$BROKER_DIR/persist_store"; install -m 644 "$FILES/sitecustomize.py" "$BROKER_DIR/persist_store/sitecustomize.py"; chown -R "$HOME_BASE:$HOME_BASE" "$BROKER_DIR/persist_store"
BN="$(grep -m1 -E '^BASE_NAME *=' "$BROKER_DIR/ipservice.py" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
IO="$(grep -m1 -E '^IP_BASE_OCTET *=' "$BROKER_DIR/ipservice.py" 2>/dev/null | sed -E 's/^[^=]*= *([0-9]+).*/\1/')"
cat > /etc/overlay2.conf <<EOT
BROKER_DIR=$BROKER_DIR
PERSIST_DIR=$MNT
BASE_NAME=${BN:-MASCAB01}
IP_BASE_OCTET=${IO:-100}
IDENTITY=1
WAIT=30
OWNER=$HOME_BASE
EOT
for SVC in send ipservice; do
  if [ -f "/etc/systemd/system/$SVC.service" ]; then
    mkdir -p "/etc/systemd/system/$SVC.service.d"
    cat > "/etc/systemd/system/$SVC.service.d/10-overlay2.conf" <<EOT
[Service]
Environment=PYTHONPATH=$BROKER_DIR/persist_store
Environment=BROKER_PERSIST_DIR=$MNT
ExecStartPre=-/bin/sh -c 'rm -f $STORE/.*.tmp.* $STORE/conf.d/.*.tmp.*'
EOT
  else info "(warning: $SVC.service not found, skipped)"; fi
done
systemctl daemon-reload; systemctl enable overlay2-prepare.service >/dev/null 2>&1
ok "installed (config: /etc/overlay2.conf)"

step "8" "Migrating config.json and bridge.conf to the persistent store (existing files there are kept) and creating links"
mkdir -p "$STORE/conf.d"
[ -s "$STORE/config.json" ] || cp -f "$BROKER_DIR/.defaults/config.json" "$STORE/config.json"
[ -s "$STORE/conf.d/bridge.conf" ] || cp -f "$BROKER_DIR/.defaults/bridge.conf" "$STORE/conf.d/bridge.conf"
IDENTITY=0 /usr/local/sbin/overlay2-prepare
ls -l "$BROKER_DIR/config.json" "$BROKER_DIR/conf.d/bridge.conf" | awk '{print "     "$9" "$10" "$11}'
ok "files live in the persistent store: $STORE"

step "9" "mosquitto.conf -> include_dir $STORE/conf.d"
if [ -f "$MOSQ" ]; then
  MBAK="$MOSQ.bak_overlay2_$(date +%Y%m%d_%H%M%S)"; cp "$MOSQ" "$MBAK"; info "backup: $MBAK"
  if grep -q '^include_dir' "$MOSQ"; then sed -i "s#^include_dir .*#include_dir $STORE/conf.d#" "$MOSQ"
  else echo "include_dir $STORE/conf.d" >> "$MOSQ"; fi
  sed -i 's#^log_dest file .*#log_dest syslog#' "$MOSQ"
else
  sed "s#@CONF_DIR@#$STORE/conf.d#" "$FILES/mosquitto.conf.template" > "$MOSQ"; chmod 644 "$MOSQ"; MBAK=""
fi
sudo -u mosquitto test -r "$STORE/conf.d/bridge.conf" && ok "user mosquitto can read bridge.conf" \
  || { err "user mosquitto cannot read $STORE/conf.d/bridge.conf"; [ -n "$MBAK" ] && cp -f "$MBAK" "$MOSQ"; exit 1; }
systemctl restart mosquitto; sleep 2
if systemctl is-active --quiet mosquitto; then ok "mosquitto restarted and active"
else
  err "mosquitto failed to start; restoring the previous mosquitto.conf"
  [ -n "$MBAK" ] && cp -f "$MBAK" "$MOSQ"; systemctl restart mosquitto || true; exit 1
fi

step "10" "Creating the automatic stage-2 unit"
mkdir -p "$STATE_DIR"; echo "persist_installed" > "$STAGE_FILE"
cat > "$UNIT2" <<EOT
[Unit]
Description=Overlay2 stage 2 - enable the overlay once, then reboot
After=multi-user.target overlay2-prepare.service
ConditionPathExists=$STAGE_FILE

[Service]
Type=oneshot
ExecStart=/bin/bash $SELF --stage2
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
EOT
systemctl daemon-reload; systemctl enable overlay2-stage2.service >/dev/null 2>&1
ok "overlay2-stage2.service enabled"

header "STAGE 1 DONE"
info "Nothing else to type: reboot -> stage 2 enables the overlay -> reboot once more."
do_reboot
