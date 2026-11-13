#!/bin/bash
# =====================================================================================================================================
#  install_all.sh - one script for everything (Overlay2, no USB, persistent store on /boot/firmware)
#
#  Usage:   sudo bash ./install_all.sh
#
#  What it does automatically:
#    1. fix file permissions (chmod +x) and strip Windows line endings (CRLF)
#    2. disable "boot read-only" (raspi-config + /etc/fstab)
#    3. remount /boot/firmware as rw (no reboot needed)
#    4. run setup_overlay2.sh (stage 1) -> reboot -> stage 2 runs automatically -> reboot
#    5. after the last reboot: run this script once more -> it runs check_overlay2.sh automatically
#
#  In short: run this script, wait for the Pi to reboot twice, SSH back in, run this script once more.
# =====================================================================================================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOT="/boot/firmware"

G='\033[0;32m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[0;34m'; N='\033[0m'
ok()   { echo -e "${G}  [OK] $1${N}"; }
info() { echo -e "${C}  [..] $1${N}"; }
err()  { echo -e "${R}  [!!] $1${N}"; }
step() { echo -e "\n${B}[$1] $2${N}"; }
die()  { err "$1"; exit 1; }

[ "$EUID" -eq 0 ] || die "Run with sudo:  sudo bash $0"

# ------------------------------------------------------------ already finished? -> just verify
if [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ]; then
  echo -e "\n${B}Overlay is already ACTIVE - running the final check...${N}\n"
  bash "$HERE/check_overlay2.sh"
  exit $?
fi

# ------------------------------------------------------------ 1. izin & CRLF
step 1 "Fix file permissions and line endings (CRLF)"
cd "$HERE" || die "cannot enter $HERE"
for f in *.sh files/*; do
  [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
chmod +x ./*.sh files/overlay2-prepare 2>/dev/null
for f in setup_overlay2.sh check_overlay2.sh remove_overlay2.sh files/overlay2-prepare; do
  [ -x "$f" ] || die "$f could not be made executable"
done
ok "all scripts executable, LF line endings"

# ------------------------------------------------------------ 2. matikan boot read-only
step 2 "Disable boot read-only"
if command -v raspi-config >/dev/null 2>&1; then
  if [ "$(raspi-config nonint get_bootro_now 2>/dev/null)" = "0" ]; then
    raspi-config nonint disable_bootro && ok "raspi-config: boot read-only disabled" \
      || err "raspi-config disable_bootro failed (continuing with the fstab fix)"
  else
    ok "boot read-only is not enabled"
  fi
else
  info "raspi-config not found, only fixing fstab"
fi

# make sure fstab has no 'ro' option on /boot/firmware
if grep -E "^[^#].*[[:space:]]$BOOT[[:space:]]" /etc/fstab | awk '{print $4}' | tr ',' '\n' | grep -qx ro; then
  cp -f /etc/fstab /etc/fstab.bak.install_all
  awk -v b="$BOOT" '$0 !~ /^[[:space:]]*#/ && $2==b { n=split($4,a,","); o=""; for(i=1;i<=n;i++) if(a[i]!="ro") o=o (o==""?"":",") a[i]; if(o=="") o="defaults"; $4=o; print $1"  "$2"  "$3"  "$4"  "$5"  "$6; next } {print}' /etc/fstab.bak.install_all > /etc/fstab
  grep -E "^[^#].*[[:space:]]$BOOT[[:space:]]" /etc/fstab | awk '{print $4}' | tr ',' '\n' | grep -qx ro \
    && die "'ro' option is still in /etc/fstab, edit manually: sudo nano /etc/fstab (backup: /etc/fstab.bak.install_all)"
  ok "'ro' option removed from /etc/fstab (backup: /etc/fstab.bak.install_all)"
else
  ok "/etc/fstab is already correct"
fi
systemctl daemon-reload

# ------------------------------------------------------------ 3. remount rw
step 3 "Remount $BOOT as rw (no reboot)"
mountpoint -q "$BOOT" || die "$BOOT is not a mount point. This Pi may use /boot instead of /boot/firmware."
if ! findmnt -no OPTIONS "$BOOT" | tr ',' '\n' | grep -qx rw; then
  mount -o remount,rw "$BOOT" || die "remount rw failed. Try: sudo reboot, then run this script again."
fi
findmnt -no OPTIONS "$BOOT" | tr ',' '\n' | grep -qx rw || die "$BOOT is still read-only. Try: sudo reboot, then run this script again."
T="$BOOT/.write_test.$$"
touch "$T" 2>/dev/null && rm -f "$T" && ok "$BOOT is rw and writable" || die "$BOOT is not writable"

# ------------------------------------------------------------ 3b. FAT ownership (uid/gid)
step "3b" "Mount $BOOT owned by the broker user (FAT has no Unix owners)"
OWNER="${HOME_BASE:-alice}"
id "$OWNER" >/dev/null 2>&1 || die "user '$OWNER' does not exist"
UID_="$(id -u "$OWNER")"; GID_="$(id -g "$OWNER")"
if ! grep -E "^[^#].*[[:space:]]$BOOT[[:space:]]" /etc/fstab | grep -q 'uid='; then
  cp -n /etc/fstab /etc/fstab.bak.overlay2
  awk -v b="$BOOT" -v o=",uid=$UID_,gid=$GID_,umask=0022" '$0 !~ /^[[:space:]]*#/ && $2==b { print $1"  "$2"  "$3"  "$4 o"  "$5"  "$6; next } {print}' /etc/fstab.bak.overlay2 > /etc/fstab
  systemctl daemon-reload
  ok "fstab: added uid=$UID_,gid=$GID_,umask=0022 to $BOOT"
else
  ok "fstab already has uid/gid for $BOOT"
fi
if ! sudo -u "$OWNER" touch "$BOOT/.wa.$$" 2>/dev/null; then
  info "Remount does not change FAT owners, doing a full umount + mount ..."
  umount "$BOOT" || die "cannot unmount $BOOT (busy). Run: sudo reboot  -- then run this script again (fstab is already fixed)."
  mount "$BOOT"  || die "cannot mount $BOOT again! Run: sudo mount -a   or   sudo reboot"
  sudo -u "$OWNER" touch "$BOOT/.wa.$$" 2>/dev/null || die "$OWNER still cannot write to $BOOT. Check: mount | grep boot ; ls -ld $BOOT"
fi
rm -f "$BOOT/.wa.$$"
ok "$OWNER can write to $BOOT ($(findmnt -no OPTIONS "$BOOT" | cut -c1-70))"

# ------------------------------------------------------------ 4. setup stage 1
step 4 "Run setup_overlay2.sh (stage 1)"
info "The Pi will reboot by itself in 10 seconds. Stage 2 runs automatically, then it reboots once more."
info "Wait ~3 minutes, SSH back in, then run:  sudo bash $HERE/install_all.sh"
echo
exec bash "$HERE/setup_overlay2.sh"
