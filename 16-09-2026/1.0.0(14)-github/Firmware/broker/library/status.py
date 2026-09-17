# library/status.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - switched to sublight for Light status"

import os
import json
import time

import lock1
import sublight
import door
import temperature
import humidity
from network_utils import get_ip, get_local_ip, get_mac_address, get_hostname


# =====================================================================================================================================
#      6. STATUS
#      Name                       : STATUS MQTT
#      Version                    : 1.0.0(9)
#      Date Created               : 23-03-2026
#      Updated                    : 27-08-2026
#      Change                     : publish_device_status & save_device_status no longer
#                                   force an empty sessionId - always retrieve it
#                                   from status.json
#                                 : Added TEMPERATURE and HUMIDITY fields to all
#                                   status payloads (_build_status, save_last_status,
#                                   save_device_status, publish payloads)
#                                 : light.py replaced with sublight.py - "Light" field
#                                   now sourced from sublight.get_light_status(), which
#                                   reads the SAME status.json's existing "Light" key
#                                   (kept up to date by lightLockControl.py, a separate
#                                   service, via PWM/dim control). No new file, no
#                                   change to the status.json schema sent to the web.
# ======================================================================================================================================


# =====================================================
#  LOGGER
# =====================================================
from logger import get_logger
log = get_logger("status")

# =====================================================
#  STATIC INFO
# =====================================================
BROKER      = get_local_ip()
HOSTNAME    = get_hostname()
MAC         = get_mac_address("eth0")
MAC_ADDRESS = get_hostname()

log.info("[STATUS] MACADDRESS : %s", MAC)
log.info("[STATUS] CABINET    : %s", MAC_ADDRESS)
log.info("[STATUS] BrokerAddr : %s", BROKER)
log.info("[STATUS] HOSTNAME   : %s", HOSTNAME)

# =====================================================
#  CONFIG
# =====================================================
CONFIG_FILE      = "config.json"
LAST_STATUS_FILE = "status.json"

# =====================================================
#  GLOBAL SESSION
# =====================================================
scan_session_id = None

_mqtt_publish_func  = None
_mqtt_topic_status  = None


def set_mqtt_publisher(func, topic_status):
    global _mqtt_publish_func, _mqtt_topic_status
    _mqtt_publish_func  = func
    _mqtt_topic_status  = topic_status


def set_session_id(sid):
    global scan_session_id
    scan_session_id = sid or ""

# =====================================================
#  HELPERS
# =====================================================

def load_admin_sessions():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                return cfg.get("admin_sessionID", [])
    except Exception as e:
        log.warning("[STATUS] Failed to get admin_sessionID: %s", e)
    return []


def load_hostname():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                hostname = cfg.get("hostname", "").strip()
                if hostname:
                    return hostname
    except Exception as e:
        log.warning("[STATUS] Failed to get hostname from config: %s", e)
    return "Unknown"

# =====================================================
#  BUILD STATUS
# =====================================================

def _build_status(count=0, duration=0):
    """
    Build status payload.

    sessionId priority:
      1. scan_session_id (set when scan is active via MQTT command)
      2. Fallback: read from status.json (last known session)
      3. Empty string if not available at all

    force_empty_session is no longer used - sessionId will ALWAYS
    be filled if available in status.json.

    "Light" priority:
      sublight.get_light_status() reads the "Light" key straight out
      of status.json itself (the same file this function writes to).
      lightLockControl.py - a separate service - updates that key
      directly whenever it turns the light on/off via PWM, so this
      always reflects the true last-known hardware state even though
      it runs in a different process.
    """
    global scan_session_id

    sid = scan_session_id or ""

    # Fallback: read from status.json if no active session exists
    if not sid:
        try:
            if os.path.exists(LAST_STATUS_FILE):
                with open(LAST_STATUS_FILE, "r") as f:
                    old = json.load(f)
                    sid = old.get("sessionId", "")
        except Exception as e:
            log.warning("[STATUS] Failed to read last sessionId: %s", e)

    admin_sessions = load_admin_sessions()
    is_admin       = sid in admin_sessions

    status = {
        "mac":         MAC_ADDRESS,
        "macAddress":  MAC,
        "hostname":    load_hostname(),
        "sessionId":   sid,
        "time":        time.strftime("%Y-%m-%d %H:%M:%S"),
        "Door1":       door.get_door1_status(),
        "Light":       sublight.get_light_status(),
        "Lock1":       lock1.get_lock1_status(),
        "Temperature": temperature.get_temperature_status(),
        "Humidity":    humidity.get_humidity_status(),
        "IP_ETH0":     get_ip("eth0"),
    }

    if is_admin:
        status["admin"] = True

    return status

# =====================================================
#  SAVE STATUS
# =====================================================

def save_last_status(count=0, duration=0):
    """Save status to status.json using sessionId from memory/file."""
    status = _build_status(count, duration)
    try:
        with open(LAST_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
        log.info("[STATUS] Saved status.json (sessionId=%s)", status.get("sessionId"))
    except Exception as e:
        log.error("[STATUS] Failed to save status.json: %s", e)
    return status


def save_device_status(count=0, duration=0):
    """
    Save status to status.json.

    sessionId is NOT cleared - it is still retrieved
    from status.json / memory.
    """
    status = _build_status(count, duration)
    try:
        with open(LAST_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
        log.info("[STATUS] Saved status.json (sessionId=%s)", status.get("sessionId"))
    except Exception as e:
        log.error("[STATUS] Failed to save status.json: %s", e)
    return status

# =====================================================
#  PUBLISH STATUS
# =====================================================

def publish_last_status(count=0, duration=0):
    """Publish status using sessionId from memory/file."""
    status = save_last_status(count, duration)

    if _mqtt_publish_func and _mqtt_topic_status:
        try:
            _mqtt_publish_func(
                _mqtt_topic_status,
                json.dumps(status, separators=(',', ':'))
            )
            log.info("[STATUS] Published (sessionId=%s)", status.get("sessionId"))
        except Exception as e:
            log.error("[STATUS] Failed to publish status: %s", e)

    return status


def publish_device_status(count=0, duration=0):
    """
    Publish periodic status from the RPi/system.

    sessionId is NOT cleared - it is still retrieved
    from status.json / memory.
    """
    status = _build_status(count, duration)

    if _mqtt_publish_func and _mqtt_topic_status:
        try:
            _mqtt_publish_func(
                _mqtt_topic_status,
                json.dumps(status, separators=(',', ':'))
            )
            log.info("[STATUS] Published DEVICE (sessionId=%s)", status.get("sessionId"))
        except Exception as e:
            log.error("[STATUS] Failed to publish device status: %s", e)

    return status

# =====================================================
#  SESSION HELPER
# =====================================================

def get_last_session_id():
    try:
        if os.path.exists(LAST_STATUS_FILE):
            with open(LAST_STATUS_FILE, "r") as f:
                data = json.load(f)
                sid  = data.get("sessionId")
                if sid and isinstance(sid, str):
                    return sid.strip()
    except Exception as e:
        log.warning("[STATUS] Failed to get sessionId: %s", e)
    return None

# =====================================================
#  PERIODIC STATUS
# =====================================================

def periodic_status_update():
    log.info("[STATUS] Periodic status worker started")
    while True:
        try:
            count    = 0
            duration = 0

            if os.path.exists("rfid2.json"):
                with open("rfid2.json", "r") as f:
                    data     = json.load(f)
                    count    = data.get("count", 0)
                    duration = data.get("duration", 0)

            # publish_device_status now also includes sessionId
            publish_device_status(count, duration)

        except Exception as e:
            log.error("[STATUS] Periodic update error: %s", e)

        time.sleep(60)
