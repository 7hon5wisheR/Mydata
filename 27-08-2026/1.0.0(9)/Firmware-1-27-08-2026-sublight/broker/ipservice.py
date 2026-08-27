#!/usr/bin/env python3
"""
ipservice.py
Monitors IP address changes, then automatically:
  1. Changes the system hostname based on the last IP octet -> letter mapping
  2. Updates the clientid & topic lines in bridge.conf
  3. Restarts the mosquitto (broker) service so the config gets reloaded

Last-octet -> letter mapping (BASE_OCTET=100):
  .101 -> A   .102 -> B   .103 -> C  ... .110 -> J  etc.

Example: BASE_NAME="MASCAB01" + IP .101 -> hostname "MASCAB01A"
"""

import subprocess
import time
import re
import logging
from pathlib import Path
from typing import Optional

# ==================== CONFIG (ADJUST AS NEEDED) ====================
INTERFACE = "eth0"                 # interface being monitored: eth0 / wlan0
BASE_NAME = "MASCAB01"             # hostname & clientid prefix
IP_BASE_OCTET = 100                # .101 -> A (101-100=1)
CHECK_INTERVAL = 5                 # seconds, delay between IP checks

# IMPORTANT: this service runs as root, so don't use "~".
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
    """Get the active IP address of the given interface."""
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", interface],
            capture_output=True, text=True, check=True,
        )
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
        if match:
            return match.group(1)
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to get IP from interface {interface}: {e}")
    return None


def octet_to_letter(last_octet: int) -> Optional[str]:
    """.101 -> A, .102 -> B, ... .126 -> Z"""
    idx = last_octet - IP_BASE_OCTET
    if idx < 1 or idx > 26:
        log.warning(f"Octet {last_octet} is out of the mapping range (101-126)")
        return None
    return chr(ord("A") + idx - 1)


def get_hostname_suffix(ip: str) -> Optional[str]:
    try:
        last_octet = int(ip.strip().split(".")[-1])
    except (ValueError, IndexError):
        log.error(f"Invalid IP: {ip}")
        return None
    return octet_to_letter(last_octet)


def set_hostname(new_hostname: str):
    try:
        subprocess.run(["hostnamectl", "set-hostname", new_hostname], check=True)
        log.info(f"Hostname changed -> {new_hostname}")
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to set hostname: {e}")


def update_bridge_conf(path: str, new_id: str) -> bool:
    """
    Update the 3 lines in bridge.conf that use the device name (all share
    the same value):
      connection MASCAB01J        -> connection <new_id>
      clientid MASCAB01J          -> clientid <new_id>
      topic MASCAB01J/# both 0    -> topic <new_id>/# both 0
    """
    p = Path(path)
    if not p.exists():
        log.error(f"bridge.conf not found: {path}")
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
            old_topic = parts[1]                      # e.g. MASCAB01J/#
            remainder = "/".join(old_topic.split("/")[1:])  # usually "#"
            parts[1] = f"{new_id}/{remainder}" if remainder else new_id
            new_line = " ".join(parts)
            changed |= (new_line != line)
            new_lines.append(new_line)

        else:
            new_lines.append(line)

    if changed:
        p.write_text("\n".join(new_lines) + "\n")
        log.info(f"bridge.conf updated: connection/clientid/topic -> {new_id}")
    else:
        log.info("bridge.conf already up to date, no changes")
    return changed


def reboot_pi(delay: int = 5):
    """Reboot the Raspberry Pi. Called after hostname & bridge.conf are
    updated, so every service (including apps.py, which reads the hostname
    once at startup) picks up the change. A short delay is given so logs
    and files have time to flush first."""
    log.info(f"Rebooting Raspberry Pi in {delay} seconds...")
    time.sleep(delay)
    try:
        subprocess.run(["reboot"], check=True)
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to reboot: {e}")


def load_last_ip() -> Optional[str]:
    p = Path(STATE_FILE)
    return p.read_text().strip() if p.exists() else None


def save_last_ip(ip: str):
    Path(STATE_FILE).write_text(ip)


def process_ip_change(ip: str):
    suffix = get_hostname_suffix(ip)
    if suffix is None:
        log.warning(f"IP {ip} could not be mapped to a letter, skipping")
        return

    new_id = f"{BASE_NAME}{suffix}"
    log.info(f"IP change detected: {ip} -> target: {new_id}")

    set_hostname(new_id)
    conf_changed = update_bridge_conf(BRIDGE_CONF_PATH, new_id)

    # Save the IP before rebooting, so that after it comes back up the
    # service doesn't think the IP "changed" again to the same value.
    save_last_ip(ip)

    if conf_changed:
        reboot_pi()


def main():
    log.info("ipservice.py started, monitoring IP changes...")
    last_ip = load_last_ip()

    while True:
        current_ip = get_current_ip(INTERFACE)
        if current_ip and current_ip != last_ip:
            log.info(f"IP changed: {last_ip} -> {current_ip}")
            process_ip_change(current_ip)
            last_ip = current_ip
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
