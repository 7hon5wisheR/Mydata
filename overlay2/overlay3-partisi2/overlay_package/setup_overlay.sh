#!/bin/bash
# ================================================================
#  setup_overlay.sh  -- khusus OVERLAY (tidak menyentuh setup_raspberry.sh)
#  Jalankan SEKALI di Raspberry yang sudah ter-setup:
#
#      sudo bash /home/alice/broker/setup_overlay.sh
#
#  Otomatis, tidak perlu mengetik apa pun lagi:
#    tahap 1 : izin sudo apps (NOPASSWD) -> chmod -> rsync
#              -> setup_overlay_persist.sh -> reboot
#    tahap 2 : (otomatis setelah boot) raspi-config nonint do_overlayfs 0
#              -> reboot  -> overlay aktif
#
#  Prasyarat: folder overlay_persist/ (sitecustomize.py, broker-persist) dan
#  setup_overlay_persist.sh + check_overlay_persist.sh sudah ada di broker/.
#  Opsi:  --status   tampilkan status overlay saja
# ================================================================
set -e

HOME_BASE="${HOME_BASE:-alice}"
BROKER_DIR="${BROKER_DIR:-/home/${HOME_BASE}/broker}"
SETUP_PERSIST="${BROKER_DIR}/setup_overlay_persist.sh"
CHECK_PERSIST="${BROKER_DIR}/check_overlay_persist.sh"
HELPER_SRC="${BROKER_DIR}/overlay_persist/broker-persist"
SITECUSTOM_SRC="${BROKER_DIR}/overlay_persist/sitecustomize.py"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/apps}"
STATE_DIR="${STATE_DIR:-/var/lib/setup_overlay}"
STAGE_FILE="${STATE_DIR}/stage"                      # persist_installed | overlay_enabled
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/overlay-stage2.service}"
AUTO_REBOOT="${AUTO_REBOOT:-yes}"                    # "no" = hanya tampilkan perintah reboot
REBOOT_DELAY="${REBOOT_DELAY:-10}"                   # detik, Ctrl+C untuk batal
SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}")"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  [OK] $1${NC}"; }
info() { echo -e "${CYAN}  [..] $1${NC}"; }
err()  { echo -e "${RED}  [!!] $1${NC}"; }
step() { echo -e "${BLUE}[$1] $2${NC}"; }
header() { echo -e "\n${BLUE}================================================================${NC}\n  $1\n${BLUE}================================================================${NC}"; }

if [ "$EUID" -ne 0 ] && [ "${SETUP_OVERLAY_TEST:-0}" != "1" ]; then
  err "Jalankan dengan sudo!  Contoh: sudo bash ${SELF_PATH}"; exit 1
fi

is_overlay_active() { [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ]; }
stage_now() { cat "${STAGE_FILE}" 2>/dev/null || true; }

do_reboot() {
  if [ "${AUTO_REBOOT}" = "yes" ]; then
    info "Reboot dalam ${REBOOT_DELAY} detik (Ctrl+C untuk batal)..."
    sleep "${REBOOT_DELAY}"
    systemctl reboot
    exit 0
  fi
  info "Reboot manual sekarang: sudo reboot"
}

remove_unit() {
  systemctl disable overlay-stage2.service >/dev/null 2>&1 || true
  rm -f "${UNIT_FILE}"
  systemctl daemon-reload >/dev/null 2>&1 || true
}

show_status() {
  header "STATUS OVERLAY"
  echo "  root filesystem : $(findmnt -no FSTYPE / 2>/dev/null)   (overlay = AKTIF, ext4 = mati)"
  echo "  setting boot    : $(raspi-config nonint get_overlayfs 2>/dev/null | sed 's/^0$/aktif/;s/^1$/mati/')"
  echo "  tahap setup     : $(stage_now)"
  [ -f "${SUDOERS_FILE}" ] && echo "  sudoers apps    : ada (${SUDOERS_FILE})" || echo "  sudoers apps    : TIDAK ADA"
}

# ---------------------------------------------------------------- tahap 2
stage2() {
  header "OVERLAY tahap 2 dari 2 - mengaktifkan overlay"
  if is_overlay_active; then
    ok "Overlay sudah AKTIF."; remove_unit; exit 0
  fi
  step "5" "Menjalankan raspi-config nonint do_overlayfs 0 ..."
  if raspi-config nonint do_overlayfs 0; then
    echo "overlay_enabled" > "${STAGE_FILE}"
    remove_unit                       # SD card masih bisa ditulis (overlay aktif setelah reboot)
    ok "Overlay diset aktif - berlaku setelah reboot"
    info "Setelah boot cek:  cd ${BROKER_DIR} && sudo ./check_overlay_persist.sh  (semua [ OK ])"
    do_reboot
  else
    err "raspi-config do_overlayfs 0 GAGAL. Overlay TIDAK diaktifkan."
    exit 1
  fi
  exit 0
}

# ---------------------------------------------------------------- dispatcher
case "${1:-}" in
  --status) show_status; exit 0 ;;
  --stage2)                                   # dipanggil oleh overlay-stage2.service
    [ "$(stage_now)" = "persist_installed" ] && ! is_overlay_active && stage2
    exit 0 ;;
esac

if is_overlay_active; then
  header "Overlay sudah AKTIF"
  info "Tidak ada yang perlu dilakukan. Untuk mematikan: tombol disable di :8082"
  info "atau: sudo raspi-config nonint do_overlayfs 1 && sudo reboot"
  exit 0
fi
# dijalankan ulang setelah tahap 1 (cadangan manual kalau unit otomatis tidak jalan)
if [ "$(stage_now)" = "persist_installed" ]; then stage2; fi

# ---------------------------------------------------------------- tahap 1
header "OVERLAY tahap 1 dari 2 - izin sudo + paket persist"

step "1" "Memeriksa file paket di ${BROKER_DIR} ..."
MISSING=0
for f in "${SETUP_PERSIST}" "${HELPER_SRC}" "${SITECUSTOM_SRC}"; do
  [ -f "$f" ] || { err "tidak ditemukan: $f"; MISSING=1; }
done
[ "${MISSING}" = "0" ] || { err "Lengkapi dulu folder overlay_persist/ dan *.sh di ${BROKER_DIR}. Dibatalkan."; exit 1; }
ok "file paket lengkap"

step "2" "Menulis izin sudo tanpa password: ${SUDOERS_FILE}"
TMP="$(mktemp)"
echo "${HOME_BASE} ALL=(ALL) NOPASSWD: ALL" > "${TMP}"
if visudo -c -f "${TMP}" >/dev/null 2>&1; then
  install -m 440 -o root -g root "${TMP}" "${SUDOERS_FILE}"
  ok "${HOME_BASE} ALL=(ALL) NOPASSWD: ALL   (mode 440)"
else
  rm -f "${TMP}"; err "Sintaks sudoers tidak valid. Dibatalkan."; exit 1
fi
rm -f "${TMP}"

step "3" "chmod +x *.sh overlay_persist/broker-persist"
chmod +x "${BROKER_DIR}"/*.sh "${HELPER_SRC}"
ok "izin eksekusi diset"

step "4" "Memasang rsync (kalau belum ada) ..."
if command -v rsync >/dev/null 2>&1; then ok "rsync sudah terpasang"
else apt-get install -y rsync && ok "rsync terpasang"; fi

step "4b" "Menjalankan setup_overlay_persist.sh ..."
if ! (cd "${BROKER_DIR}" && ./setup_overlay_persist.sh); then
  err "setup_overlay_persist.sh GAGAL. Overlay tidak dilanjutkan, tidak ada reboot."; exit 1
fi
mkdir -p "${STATE_DIR}"
echo "persist_installed" > "${STAGE_FILE}"
ok "paket persist terpasang (overlay belum aktif)"

step "4c" "Membuat unit otomatis untuk tahap 2 ..."
cat > "${UNIT_FILE}" <<EOT
[Unit]
Description=Overlay tahap 2 - aktifkan overlay sekali, lalu reboot
After=multi-user.target
ConditionPathExists=${STAGE_FILE}

[Service]
Type=oneshot
ExecStart=/bin/bash ${SELF_PATH} --stage2
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
EOT
systemctl daemon-reload
systemctl enable overlay-stage2.service
ok "overlay-stage2.service aktif - jalan otomatis setelah reboot ini"

header "TAHAP 1 SELESAI"
info "Tidak perlu mengetik apa pun lagi:"
info "Pi reboot -> tahap 2 otomatis mengaktifkan overlay -> reboot sekali lagi."
do_reboot
