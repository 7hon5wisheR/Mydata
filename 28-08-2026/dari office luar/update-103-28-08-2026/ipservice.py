#!/usr/bin/env python3
"""
ipservice.py
Memantau perubahan IP address, lalu otomatis:
  1. Ubah hostname sistem sesuai mapping octet terakhir IP -> huruf
  2. Update baris clientid & topic di bridge.conf
  3. Restart service mosquitto (broker) supaya config ke-reload

Mapping octet terakhir -> huruf (BASE_OCTET=100):
  .101 -> A   .102 -> B   .103 -> C  ... .110 -> J  dst

Contoh: BASE_NAME="MASCAB01" + IP .101 -> hostname "MASCAB01A"
"""

import subprocess
import time
import re
import logging
from pathlib import Path
from typing import Optional

# ==================== KONFIGURASI (SESUAIKAN) ====================
INTERFACE = "eth0"                 # interface yang dipantau: eth0 / wlan0
BASE_NAME = "MASCAB01"             # prefix hostname & clientid
IP_BASE_OCTET = 100                # .101 -> A (101-100=1)
CHECK_INTERVAL = 5                 # detik, jeda antar cek IP

# PENTING: service jalan sebagai root, jadi jangan pakai "~".
BRIDGE_CONF_PATH = "/home/alice/broker/conf.d/bridge.conf"

STATE_FILE = "/var/tmp/ipservice_last_ip.txt"
LOG_FILE = "/var/log/ipservice.log"
# ====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("ipservice")


def get_current_ip(interface: str) -> Optional[str]:
    """Ambil IP address aktif dari interface tertentu."""
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", interface],
            capture_output=True, text=True, check=True,
        )
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
        if match:
            return match.group(1)
    except subprocess.CalledProcessError as e:
        log.error(f"Gagal ambil IP dari interface {interface}: {e}")
    return None


def octet_to_letter(last_octet: int) -> Optional[str]:
    """.101 -> A, .102 -> B, ... .126 -> Z"""
    idx = last_octet - IP_BASE_OCTET
    if idx < 1 or idx > 26:
        log.warning(f"Octet {last_octet} di luar rentang mapping (101-126)")
        return None
    return chr(ord("A") + idx - 1)


def get_hostname_suffix(ip: str) -> Optional[str]:
    try:
        last_octet = int(ip.strip().split(".")[-1])
    except (ValueError, IndexError):
        log.error(f"IP tidak valid: {ip}")
        return None
    return octet_to_letter(last_octet)


def set_hostname(new_hostname: str):
    try:
        subprocess.run(["hostnamectl", "set-hostname", new_hostname], check=True)
        log.info(f"Hostname diubah -> {new_hostname}")
    except subprocess.CalledProcessError as e:
        log.error(f"Gagal set hostname: {e}")


def update_bridge_conf(path: str, new_id: str) -> bool:
    """
    Update 3 baris di bridge.conf yang memakai nama device (semua nilainya sama):
      connection MASCAB01J        -> connection <new_id>
      clientid MASCAB01J          -> clientid <new_id>
      topic MASCAB01J/# both 0    -> topic <new_id>/# both 0
    """
    p = Path(path)
    if not p.exists():
        log.error(f"bridge.conf tidak ditemukan: {path}")
        return False

    lines = p.read_text().splitlines()
    new_lines = []
    changed = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("connection "):
            new_line = f"connection {new_id}"
            changed |= (new_line != line)
            new_lines.append(new_line)

        elif stripped.startswith("clientid "):
            new_line = f"clientid {new_id}"
            changed |= (new_line != line)
            new_lines.append(new_line)

        elif stripped.startswith("topic ") and "/#" in stripped:
            parts = line.split()
            old_topic = parts[1]                      # contoh: MASCAB01J/#
            sisa = "/".join(old_topic.split("/")[1:])  # biasanya "#"
            parts[1] = f"{new_id}/{sisa}" if sisa else new_id
            new_line = " ".join(parts)
            changed |= (new_line != line)
            new_lines.append(new_line)

        else:
            new_lines.append(line)

    if changed:
        p.write_text("\n".join(new_lines) + "\n")
        log.info(f"bridge.conf diperbarui: connection/clientid/topic -> {new_id}")
    else:
        log.info("bridge.conf sudah sesuai, tidak ada perubahan")
    return changed


def reboot_pi(delay: int = 5):
    """Reboot Raspberry Pi. Dipanggil setelah hostname & bridge.conf diupdate,
    supaya semua service (termasuk apps.py yang baca hostname sekali saat startup)
    ikut segar. Diberi delay singkat supaya log & file sempat ke-flush dulu."""
    log.info(f"Reboot Raspberry Pi dalam {delay} detik...")
    time.sleep(delay)
    try:
        subprocess.run(["reboot"], check=True)
    except subprocess.CalledProcessError as e:
        log.error(f"Gagal reboot: {e}")


def load_last_ip() -> Optional[str]:
    p = Path(STATE_FILE)
    return p.read_text().strip() if p.exists() else None


def save_last_ip(ip: str):
    Path(STATE_FILE).write_text(ip)


def process_ip_change(ip: str):
    suffix = get_hostname_suffix(ip)
    if suffix is None:
        log.warning(f"IP {ip} tidak bisa dipetakan ke huruf, dilewati")
        return

    new_id = f"{BASE_NAME}{suffix}"
    log.info(f"Perubahan IP terdeteksi: {ip} -> target: {new_id}")

    set_hostname(new_id)
    conf_changed = update_bridge_conf(BRIDGE_CONF_PATH, new_id)

    # Simpan IP sebelum reboot, supaya setelah nyala lagi service tidak
    # menganggap IP "berubah" lagi ke nilai yang sama.
    save_last_ip(ip)

    if conf_changed:
        reboot_pi()


def main():
    log.info("ipservice.py dimulai, memantau perubahan IP...")
    last_ip = load_last_ip()

    while True:
        current_ip = get_current_ip(INTERFACE)
        if current_ip and current_ip != last_ip:
            log.info(f"IP berubah: {last_ip} -> {current_ip}")
            process_ip_change(current_ip)
            last_ip = current_ip
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
