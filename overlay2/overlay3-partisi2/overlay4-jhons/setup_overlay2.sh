#!/bin/bash
# ================================================================
#  setup_overlay2.sh  -- SATU FILE: dari NOL (overlay mati) sampai overlay AKTIF
#  OVERLAY 2: root + home VOLATILE (RAM), data penting di direktori PERSISTEN
#
#  Cukup satu perintah per Raspberry (overlay harus MATI):
#      sudo bash setup_overlay2.sh
#
#  Yang dikerjakan (otomatis, berurutan):
#    1. cek partisi data (default /boot/firmware, bisa USB) writable
#    2. pindahkan config.json + conf.d/bridge.conf ke <PERSIST_DIR>
#       (di folder broker diganti symlink -> kode broker TIDAK perlu diubah)
#    3. beri hak tulis 'alice' ke partisi FAT (fstab, berlaku setelah reboot)
#    4. ubah include_dir di mosquitto.conf ke <PERSIST_DIR>/conf.d, restart mosquitto
#    5. pasang sitecustomize v2 (ditanam di file ini) + drop-in systemd (send, ipservice)
#    6. izin sudo apps (NOPASSWD) -> enable/disable overlay di :8082 TANPA password
#    7. aktifkan overlay -> reboot sekali
#  Root SD card TIDAK pernah dibuka untuk ditulis (tanpa helper/remount).
#
#  Opsi:   --status   status + VERIFIKASI lokasi file + tes tulis (jalankan SETELAH overlay aktif)
#          --undo     kembalikan file ke folder broker (overlay harus mati)
#  Variabel: PERSIST_DIR=/mnt/usb/broker-data   (mis. USB ext4 lewat fstab LABEL + nofail)
#            HOME_BASE=alice   BROKER_DIR=/home/alice/broker
# ================================================================
set -e

HOME_BASE="${HOME_BASE:-alice}"
BROKER_DIR="${BROKER_DIR:-/home/${HOME_BASE}/broker}"
MOSQ_CONF="${MOSQ_CONF:-/etc/mosquitto/mosquitto.conf}"
FSTAB="${FSTAB:-/etc/fstab}"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/apps}"
SITE_DST="${BROKER_DIR}/overlay_persist/sitecustomize.py"
BACKUP_DIR="${BROKER_DIR}/.overlay2-backup"
DROPIN_DIR="${DROPIN_DIR:-/etc/systemd/system}"
IPSERVICE_DELAY="${IPSERVICE_DELAY:-30}"
AUTO_REBOOT="${AUTO_REBOOT:-yes}"
REBOOT_DELAY="${REBOOT_DELAY:-10}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YEL='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  [OK] $1${NC}"; }
info() { echo -e "${CYAN}  [..] $1${NC}"; }
warn() { echo -e "${YEL}  [!]  $1${NC}"; }
err()  { echo -e "${RED}  [!!] $1${NC}"; }
step() { echo -e "${BLUE}[$1] $2${NC}"; }
header() { echo -e "\n${BLUE}================================================================${NC}\n  $1\n${BLUE}================================================================${NC}"; }
die() { err "$1"; exit 1; }

[ "$EUID" -eq 0 ] || [ "${SETUP_OVERLAY_TEST:-0}" = "1" ] || die "Jalankan dengan sudo!  Contoh: sudo bash $0"

is_overlay_active() { [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ]; }

# partisi data: /boot/firmware (Bookworm) atau /boot
if [ -z "${BOOT_MNT:-}" ]; then
  if findmnt -rno TARGET /boot/firmware >/dev/null 2>&1; then BOOT_MNT=/boot/firmware; else BOOT_MNT=/boot; fi
fi
PERSIST_DIR="${PERSIST_DIR:-${BOOT_MNT}/broker-data}"
# mount point yang menampung PERSIST_DIR (untuk cek rw)
DATA_MNT="$(findmnt -no TARGET -T "$(dirname "${PERSIST_DIR}")" 2>/dev/null || echo "${BOOT_MNT}")"

do_reboot() {
  if [ "${AUTO_REBOOT}" = "yes" ]; then
    info "Reboot dalam ${REBOOT_DELAY} detik (Ctrl+C untuk batal)..."; sleep "${REBOOT_DELAY}"; systemctl reboot; exit 0
  fi
  info "Reboot manual sekarang: sudo reboot"
}

write_test() {   # tulis + sync + hapus di direktori persisten
  local d="$1"; mkdir -p "$d" 2>/dev/null || return 1
  echo ok > "$d/.write_test" 2>/dev/null && sync && rm -f "$d/.write_test"
}

set_include_dir() {  # $1 = path baru
  local new="$1"
  if grep -q '^[[:space:]]*include_dir' "${MOSQ_CONF}"; then
    sed -i "s#^[[:space:]]*include_dir.*#include_dir ${new}#" "${MOSQ_CONF}"
  else
    echo "include_dir ${new}" >> "${MOSQ_CONF}"
  fi
}

# ---------------------------------------------------------------- --status
if [ "${1:-}" = "--status" ]; then
  header "STATUS OVERLAY 2"
  echo "  root filesystem   : $(findmnt -no FSTYPE / 2>/dev/null)  (overlay = aktif, ext4 = mati)"
  echo "  partisi data      : ${DATA_MNT}  [$(findmnt -no FSTYPE "${DATA_MNT}" 2>/dev/null)]  opsi: $(findmnt -no OPTIONS "${DATA_MNT}" 2>/dev/null | cut -c1-70)"
  echo "  PERSIST_DIR       : ${PERSIST_DIR}"
  echo ""
  echo "  [VERIFIKASI] lokasi ASLI config.json dan bridge.conf:"
  BASE_REAL="$(readlink -f "${BROKER_DIR}")"; ALL_OUT=1
  for rel in config.json conf.d/bridge.conf; do
    real="$(readlink -f "${BROKER_DIR}/${rel}" 2>/dev/null)"
    mnt="$(findmnt -no TARGET -T "$(dirname "${real:-/}")" 2>/dev/null)"; fst="$(findmnt -no FSTYPE -T "$(dirname "${real:-/}")" 2>/dev/null)"
    case "${real}" in
      ""|"${BASE_REAL}"/*) ALL_OUT=0; err "${rel}: ${real:-tidak ada}  -> DI DALAM folder broker (hilang saat reboot!)";;
      *) ok "${rel}: ${real}  [partisi ${mnt}, ${fst}]";;
    esac
  done
  [ "${ALL_OUT}" = "1" ] && ok "KEDUA file berada DI LUAR folder broker" || err "Ada file yang MASIH di dalam folder broker - jalankan: sudo bash $0"
  echo ""
  echo "  mosquitto include : $(grep -h '^[[:space:]]*include_dir' "${MOSQ_CONF}" 2>/dev/null)"
  echo "  config.json valid : $(python3 -c "import json;json.load(open('${BROKER_DIR}/config.json'));print('ya')" 2>/dev/null || echo TIDAK)"
  echo "  bridge.conf       : $([ -s "${BROKER_DIR}/conf.d/bridge.conf" ] && echo ada || echo TIDAK ADA)"
  echo "  sudo tanpa pwd    : $([ -f "${SUDOERS_FILE}" ] && echo "ada (${SUDOERS_FILE})" || echo "TIDAK ADA")"
  if [ ! -d "${PERSIST_DIR}" ]; then warn "${PERSIST_DIR} belum ada (setup belum dijalankan)"
  elif write_test "${PERSIST_DIR}"; then ok "tes tulis ke ${PERSIST_DIR}: BERHASIL (persisten bisa ditulis)"
  else err "tes tulis ke ${PERSIST_DIR}: GAGAL - partisi read-only? (mis. boot dibuat read-only di raspi-config)"; fi
  echo "  RAM overlay       : $(df -h /media/root-rw 2>/dev/null | awk 'NR==2{print $3" dipakai dari "$2" ("$5")"}')"
  exit 0
fi

# ---------------------------------------------------------------- --undo
if [ "${1:-}" = "--undo" ]; then
  header "UNDO OVERLAY 2"
  is_overlay_active && die "Overlay masih aktif. Matikan dulu (tombol disable :8082 / raspi-config nonint do_overlayfs 1) lalu reboot."
  if [ -L "${BROKER_DIR}/config.json" ]; then
    cp --remove-destination "$(readlink -f "${BROKER_DIR}/config.json")" "${BROKER_DIR}/config.json"; ok "config.json dikembalikan ke folder broker"; fi
  if [ -L "${BROKER_DIR}/conf.d" ]; then
    SRC="$(readlink -f "${BROKER_DIR}/conf.d")"; rm -f "${BROKER_DIR}/conf.d"; mkdir -p "${BROKER_DIR}/conf.d"
    cp -r "${SRC}/." "${BROKER_DIR}/conf.d/"; chown -R "${HOME_BASE}:${HOME_BASE}" "${BROKER_DIR}/conf.d" 2>/dev/null || true
    ok "conf.d dikembalikan ke folder broker"; fi
  chown "${HOME_BASE}:${HOME_BASE}" "${BROKER_DIR}/config.json" 2>/dev/null || true
  set_include_dir "${BROKER_DIR}/conf.d"; systemctl restart mosquitto || true
  ok "mosquitto.conf: include_dir ${BROKER_DIR}/conf.d"
  info "Salinan di ${PERSIST_DIR} dibiarkan. Selesai."; exit 0
fi

# ================================================================ INSTALL
header "OVERLAY 2 - data persisten di ${PERSIST_DIR}"

step "1" "Memeriksa kondisi..."
if is_overlay_active; then
  warn "Overlay sudah AKTIF - tidak ada yang diubah. Cek: sudo bash $0 --status"
  warn "Untuk memasang ulang: matikan overlay dulu (tombol disable :8082), reboot, jalankan lagi."
  exit 0
fi
[ -f "${MOSQ_CONF}" ] || die "Tidak ditemukan: ${MOSQ_CONF}"
for f in "${BROKER_DIR}/config.json" "${BROKER_DIR}/conf.d"; do [ -e "$f" ] || die "Tidak ditemukan: $f"; done
ok "kondisi awal sesuai (overlay mati, folder broker ditemukan)"

step "2" "Memeriksa partisi data (${DATA_MNT})..."
OPTS="$(findmnt -no OPTIONS "${DATA_MNT}" 2>/dev/null || echo rw)"
case ",${OPTS}," in *,ro,*) die "${DATA_MNT} read-only. Jadikan read-write dulu (raspi-config > Overlay > boot partition: read-write)." ;; esac
write_test "${PERSIST_DIR}" || die "Tidak bisa menulis ke ${PERSIST_DIR}."
ok "${DATA_MNT} [$(findmnt -no FSTYPE "${DATA_MNT}" 2>/dev/null)] writable"
case "$(findmnt -no FSTYPE "${DATA_MNT}" 2>/dev/null)" in
  vfat) warn "Partisi FAT tanpa journal: mati listrik saat menulis bisa merusaknya. Untuk produksi lebih aman: USB ext4 (PERSIST_DIR=...)." ;;
esac

step "3" "Memindahkan config.json dan conf.d ke ${PERSIST_DIR} (diganti symlink)..."
mkdir -p "${PERSIST_DIR}/conf.d" "${BACKUP_DIR}"
# config.json
if [ -L "${BROKER_DIR}/config.json" ]; then ok "config.json sudah symlink"
else
  python3 -c "import json,sys;json.load(open(sys.argv[1]))" "${BROKER_DIR}/config.json" || die "config.json tidak valid JSON - dibatalkan."
  if [ -e "${PERSIST_DIR}/config.json" ]; then warn "config.json persisten sudah ada - DIPERTAHANKAN (salinan lokal -> backup)"
  else cp "${BROKER_DIR}/config.json" "${PERSIST_DIR}/config.json"; fi
  sync
  mv -f "${BROKER_DIR}/config.json" "${BACKUP_DIR}/config.json"
  ln -sfn "${PERSIST_DIR}/config.json" "${BROKER_DIR}/config.json"
  chown -h "${HOME_BASE}:${HOME_BASE}" "${BROKER_DIR}/config.json" 2>/dev/null || true
  ok "config.json -> ${PERSIST_DIR}/config.json"
fi
# conf.d
if [ -L "${BROKER_DIR}/conf.d" ]; then ok "conf.d sudah symlink"
else
  cp -rn "${BROKER_DIR}/conf.d/." "${PERSIST_DIR}/conf.d/" 2>/dev/null || true
  sync
  rm -rf "${BACKUP_DIR}/conf.d"; mv -f "${BROKER_DIR}/conf.d" "${BACKUP_DIR}/conf.d"
  ln -sfn "${PERSIST_DIR}/conf.d" "${BROKER_DIR}/conf.d"
  chown -h "${HOME_BASE}:${HOME_BASE}" "${BROKER_DIR}/conf.d" 2>/dev/null || true
  ok "conf.d -> ${PERSIST_DIR}/conf.d"
fi
[ -s "${PERSIST_DIR}/conf.d/bridge.conf" ] && ok "bridge.conf ada di ${PERSIST_DIR}/conf.d" || warn "bridge.conf belum ada di ${PERSIST_DIR}/conf.d (dibuat saat broker IP diatur)"
info "Backup file asli: ${BACKUP_DIR}"

step "4" "Hak tulis untuk '${HOME_BASE}' di partisi FAT (fstab, berlaku setelah reboot)..."
if [ "$(findmnt -no FSTYPE "${DATA_MNT}" 2>/dev/null)" = "vfat" ] && [ -f "${FSTAB}" ]; then
  U="$(id -u "${HOME_BASE}")"; G="$(id -g "${HOME_BASE}")"
  if awk -v m="${DATA_MNT}" '$2==m && $3=="vfat" && $4 ~ /uid=/ {f=1} END{exit !f}' "${FSTAB}"; then ok "fstab: opsi uid/gid sudah ada"
  else
    cp "${FSTAB}" "${FSTAB}.bak-overlay2"
    awk -v m="${DATA_MNT}" -v u="${U}" -v g="${G}" 'BEGIN{OFS="\t"} $0!~/^#/ && $2==m && $3=="vfat" {$4=$4",uid="u",gid="g",fmask=0133,dmask=0022"} {print}' "${FSTAB}.bak-overlay2" > "${FSTAB}"
    if ! awk -v m="${DATA_MNT}" '$2==m && $3=="vfat" && $4 ~ /uid=/ {f=1} END{exit !f}' "${FSTAB}"; then
      cp "${FSTAB}.bak-overlay2" "${FSTAB}"
      die "Baris fstab untuk ${DATA_MNT} (vfat) tidak ditemukan - tanpa uid/gid '${HOME_BASE}' tidak bisa menulis config.json. Tambahkan manual: uid=${U},gid=${G},fmask=0133,dmask=0022"
    elif findmnt --verify --fstab --tab-file "${FSTAB}" >/dev/null 2>&1 || [ "${SETUP_OVERLAY_TEST:-0}" = "1" ]; then
      ok "fstab diperbarui (uid=${U},gid=${G}); cadangan: ${FSTAB}.bak-overlay2"
    else
      cp "${FSTAB}.bak-overlay2" "${FSTAB}"; die "fstab hasil edit tidak valid - dikembalikan. Dibatalkan."
    fi
  fi
else
  info "bukan FAT / fstab tidak ada - dilewati (pastikan '${HOME_BASE}' bisa menulis ke ${PERSIST_DIR})"
fi

step "5" "Mengubah include_dir di ${MOSQ_CONF}..."
cp "${MOSQ_CONF}" "${MOSQ_CONF}.bak-overlay2"
set_include_dir "${PERSIST_DIR}/conf.d"
if systemctl restart mosquitto && systemctl is-active --quiet mosquitto; then
  ok "include_dir ${PERSIST_DIR}/conf.d  (mosquitto aktif)"
else
  cp "${MOSQ_CONF}.bak-overlay2" "${MOSQ_CONF}"; systemctl restart mosquitto || true
  die "mosquitto gagal start dengan konfigurasi baru - dikembalikan. Dibatalkan."
fi

step "6" "Memasang sitecustomize v2 (tertanam) + drop-in systemd..."
mkdir -p "${BROKER_DIR}/overlay_persist"
cat > "${SITE_DST}" <<'PYEOF'
# sitecustomize.py  -- OVERLAY 2 (volatile root, persistent data directory)
# -*- coding: utf-8 -*-
"""
Atomic writes (temp file + fsync + rename) for config.json, bridge.conf and the volatile
status files.

OVERLAY 2: config.json and conf.d/ inside the broker folder are SYMLINKS to a persistent data
directory (BROKER_PERSIST_DIR, default /boot/firmware/broker-data -- or a USB drive). open() is
patched, the symlink is resolved, and the temp file is created NEXT TO THE REAL FILE in that
persistent directory, so the rename is atomic on the same filesystem and the symlink is never
replaced. The root filesystem is never remounted and no helper / sudo is needed.
Files not in the persistent directory (status.json, rfid*.json, logs) stay in RAM (overlay).
"""
import builtins
import io
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_BROKER_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_PERSIST_DIR = os.path.realpath(os.environ.get("BROKER_PERSIST_DIR", "/boot/firmware/broker-data"))
_ALLOWED = (_BROKER_DIR, _PERSIST_DIR)
_real_open = io.open


def _is_target(file, mode):
    if mode not in ("w", "wt", "tw", "wb", "bw"):
        return None
    if not isinstance(file, (str, bytes, os.PathLike)):
        return None
    try:
        p = os.fsdecode(file)
        if os.path.basename(p) not in _PROTECTED:
            return None
        full = os.path.realpath(os.path.abspath(p))      # follows the symlink to the persistent dir
    except Exception:
        return None
    for base in _ALLOWED:
        if full.startswith(base + os.sep):
            return full
    return None


def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class _AtomicFile(object):
    def __init__(self, target, tmp, f):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_tmp", tmp)
        object.__setattr__(self, "_f", f)
        object.__setattr__(self, "_done", False)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_f"), name)
        if callable(attr):
            owner = self            # keep this wrapper alive while the method runs, so that
            def _method(*a, **k):   # open(p, "w").write(x) is written BEFORE the commit
                return attr(*a, **k)
            _method._owner = owner
            return _method
        return attr

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_f"), name, value)

    def __iter__(self):
        return iter(object.__getattribute__(self, "_f"))

    def _finish(self, commit):
        if object.__getattribute__(self, "_done"):
            return
        object.__setattr__(self, "_done", True)
        f = object.__getattribute__(self, "_f")
        tmp = object.__getattribute__(self, "_tmp")
        target = object.__getattribute__(self, "_target")
        try:
            if commit:
                f.flush()
                os.fsync(f.fileno())
            f.close()
            if commit:
                try:                                    # FAT has no unix permissions -> ignore errors
                    st = os.stat(target)
                    os.chmod(tmp, st.st_mode & 0o7777)
                    if os.geteuid() == 0:
                        os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
                os.replace(tmp, target)
                _fsync_dir(os.path.dirname(target))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def close(self):
        self._finish(True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish(exc_type is None)
        return False

    def __del__(self):
        try:
            self._finish(True)
        except Exception:
            pass


def _open(file, mode="r", *args, **kwargs):
    target = _is_target(file, mode)
    if target is not None:
        try:
            d = os.path.dirname(target)
            tmp = os.path.join(d, "." + os.path.basename(target) + ".tmp.%d" % os.getpid())
            f = _real_open(tmp, mode, *args, **kwargs)
            return _AtomicFile(target, tmp, f)
        except Exception:
            pass
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _open
io.open = _open
PYEOF
python3 -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "${SITE_DST}" || die "sitecustomize.py hasil tulis tidak valid."
rm -rf "${BROKER_DIR}/overlay_persist/__pycache__"
chown -R "${HOME_BASE}:${HOME_BASE}" "${BROKER_DIR}/overlay_persist" 2>/dev/null || true
ok "${SITE_DST} ditulis (versi overlay 2)"
rm -f /usr/local/sbin/broker-persist /etc/sudoers.d/broker-persist     # sisa overlay 1 (tidak dipakai lagi)
CLEAN="rm -f ${BROKER_DIR}/.*.tmp.* ${PERSIST_DIR}/.*.tmp.* ${PERSIST_DIR}/conf.d/.*.tmp.*"
if [ -f "${DROPIN_DIR}/send.service" ]; then
  mkdir -p "${DROPIN_DIR}/send.service.d"
  cat > "${DROPIN_DIR}/send.service.d/10-hardening.conf" <<EOT
[Service]
Environment=PYTHONPATH=${BROKER_DIR}/overlay_persist
Environment=BROKER_PERSIST_DIR=${PERSIST_DIR}
ExecStartPre=-/bin/sh -c '${CLEAN}'
EOT
  ok "send.service drop-in"
else warn "send.service tidak ditemukan - dilewati"; fi
if [ -f "${DROPIN_DIR}/ipservice.service" ]; then
  mkdir -p "${DROPIN_DIR}/ipservice.service.d"
  cat > "${DROPIN_DIR}/ipservice.service.d/10-hardening.conf" <<EOT
[Unit]
After=dip.service
[Service]
Environment=PYTHONPATH=${BROKER_DIR}/overlay_persist
Environment=BROKER_PERSIST_DIR=${PERSIST_DIR}
ExecStartPre=-/bin/sh -c '${CLEAN}'
ExecStartPre=/bin/sleep ${IPSERVICE_DELAY}
EOT
  ok "ipservice.service drop-in (tunggu dip.py ${IPSERVICE_DELAY} dtk sebelum membaca IP)"
else warn "ipservice.service tidak ditemukan - dilewati"; fi
systemctl daemon-reload

step "7" "Izin sudo tanpa password: ${SUDOERS_FILE}"
TMP="$(mktemp)"; echo "${HOME_BASE} ALL=(ALL) NOPASSWD: ALL" > "${TMP}"
if visudo -c -f "${TMP}" >/dev/null 2>&1; then
  install -m 440 -o root -g root "${TMP}" "${SUDOERS_FILE}"; ok "${HOME_BASE} ALL=(ALL) NOPASSWD: ALL (mode 440)"
else rm -f "${TMP}"; die "Sintaks sudoers tidak valid. Dibatalkan."; fi
rm -f "${TMP}"

step "8" "Mengaktifkan overlay (raspi-config nonint do_overlayfs 0)..."
raspi-config nonint do_overlayfs 0 || die "raspi-config do_overlayfs 0 GAGAL. Overlay TIDAK aktif."
ok "overlay diset aktif - berlaku setelah reboot"

header "SELESAI"
info "Setelah reboot cek:  sudo bash ${BROKER_DIR}/setup_overlay2.sh --status"
info "Pastikan baris 'tes tulis ... BERHASIL'. Kalau GAGAL, partisi data read-only."
do_reboot
