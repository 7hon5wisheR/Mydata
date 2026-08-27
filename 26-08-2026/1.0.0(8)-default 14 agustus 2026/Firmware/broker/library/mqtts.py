#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__version__ = "0.0.2.2 - normalize cmd title-case"

import os
import time
import json
import threading
import hashlib
import paho.mqtt.client as mqtts
import RPi.GPIO as GPIO
from reader import SHUTDOWN_GPIO, GPIO
from mutex import FileMutex
from network_utils import get_ip, get_local_ip, get_mac_address, get_hostname
import startstops
import lock1
import light
import reader
import status

# =====================================================================================================================================
#      6. MQTT
#      Name                       : MQTT - SESSION GUARD + suppress worker publish
#      Version                    : 0.0.2.1
#      Date Created               : 05-05-2026
#      Updated                    : 08-05-2026
#      Changes                    : Added set_suppress_callback() and invoke suppress
#                                   at the start of on_message so lock/light workers in run.py
#                                   do not publish duplicate status (double/triple out)
# ======================================================================================================================================


# =====================================================
#  LOGGER
# =====================================================
from logger import get_logger
log = get_logger("mqtts")

SCAN_MUTEX = FileMutex("scan")

# =====================================================
#  STATIC INFO
# =====================================================
BROKER      = get_local_ip()
LOCAL_IP    = get_local_ip()
HOSTNAME    = get_hostname()
MAC         = get_mac_address("eth0")
MAC_ADDRESS = get_hostname()

log.info("[MQTT] LOCAL_IP   : %s", LOCAL_IP)
log.info("[MQTT] MACADDRESS : %s", MAC)
log.info("[MQTT] CABINET    : %s", MAC_ADDRESS)
log.info("[MQTT] BrokerAddr : %s", BROKER)
log.info("[MQTT] HOSTNAME   : %s", HOSTNAME)

# =====================================================
#  MQTT TOPICS
# =====================================================
CLIENT_ID       = f"mqttreader_{MAC_ADDRESS.replace(':', '')}"
TOPIC_COUNT     = f"{MAC_ADDRESS}/inRfidnumber"
TOPIC_JSON      = "out"
TOPIC_CTRL_SCAN = "in"
TOPIC_CTRL_STOP = "in"
TOPIC_LOCK1     = "out"
TOPIC_UNLOCK1   = "out"
TOPIC_LIGHTON   = "out"
TOPIC_LIGHTOFF  = "out"
TOPIC_DOOR      = "out"
TOPIC_STATUS    = "out"
TOPIC_LIGHTALL  = "in"
TOPIC_LOCKALL   = "in"
TOPIC_SESSION   = "in"

LAST_STATUS_FILE = "status.json"

# =====================================================
#  GLOBALS
# =====================================================
mqttc           = None
scan_session_id = ""
scan_requested  = threading.Event()
stop_requested  = threading.Event()
is_scanning     = threading.Lock()

# Callback to run.py to suppress worker publish
# while on_message is handling a command
_suppress_callback = None


def set_suppress_callback(fn):
    """
    Register the suppress function from run.py.
    Called in run.main() after mqtt_setup_safe().
    """
    global _suppress_callback
    _suppress_callback = fn
    log.info("[MQTT] suppress_callback registered")


# =====================================================
#  IP FILTER HELPER
# =====================================================
def is_ip_match(ip_target: str) -> bool:
    if not ip_target:
        return True
    return (
        ip_target == "all" or
        ip_target == LOCAL_IP
    )


# =====================================================
#  MAC FILTER HELPER
# =====================================================
def is_mac_match(mac_target: str) -> bool:
    if not mac_target:
        return True
    return (
        mac_target == "all" or
        mac_target == MAC.lower() or
        mac_target == MAC_ADDRESS.lower()
    )


# =====================================================
#  RFID JSON PUBLISH HELPER
# =====================================================
def publish_json_file(path="rfid2.json"):
    global mqttc

    if not mqttc:
        log.warning("[MQTT] mqtt client not ready")
        return

    if not os.path.exists(path):
        log.warning("[MQTT] JSON file not found: %s", path)
        return

    try:
        with open(path, "r") as f:
            content = f.read().strip()
            if not content:
                log.warning("[MQTT] JSON empty, skip publish")
                return

            data = json.loads(content)
            data["mac"]      = MAC_ADDRESS
            data["hostname"] = HOSTNAME
            payload = json.dumps(data, separators=(',', ':'))

        mqttc.publish("out", payload, qos=1, retain=False)
        log.info("[MQTT] JSON published to %s", TOPIC_JSON)

    except Exception as e:
        log.error("[MQTT] Failed publish JSON: %s", e)


# =====================================================
#  MQTT CALLBACKS
# =====================================================
def on_connect(client, userdata, flags, rc):
    log.info("[MQTT] Connected rc=%s", rc)
    client.subscribe("in", qos=1)


def on_message(client, userdata, msg):
    payload_raw = msg.payload.decode().strip()
    log.info("[MQTT] Received topic=%s payload=%s", msg.topic, payload_raw)

    global scan_session_id

    # =====================================================
    #  PARSE JSON
    # =====================================================
    try:
        data       = json.loads(payload_raw)
        cmd        = data.get("cmd",       "").strip()
        rfid_cmd   = data.get("rfid",      "").strip()
        light_cmd  = data.get("light",     "").strip()
        lock_cmd   = data.get("lock",      "").strip()
        session_id = data.get("sessionId", "").strip()
        mac_target = data.get("mac",       "").lower().strip()
        ip_target  = data.get("ip",        "").strip()
        sig_time   = str(data.get("time",  "")).strip()
        sig_value  = str(data.get("sig",   "")).strip()

        log.debug("[MQTT] Parsed cmd=%s rfid=%s light=%s lock=%s sessionId=%s mac=%s ip=%s",
                  cmd, rfid_cmd, light_cmd, lock_cmd, session_id, mac_target, ip_target)

    except Exception as e:
        log.warning("[MQTT] Invalid JSON payload: %s", e)
        return

    # =====================================================
    #  ONLY HANDLE TOPIC "in"
    # =====================================================
    if msg.topic != "in":
        log.debug("[MQTT] Ignored topic: %s", msg.topic)
        return

    # =====================================================
    #  SUPPRESS WORKER PUBLISH
    #  Activated at the start of on_message so lock_status_worker
    #  and light_status_worker in run.py do not publish
    #  status while this command is being processed
    # =====================================================
    if _suppress_callback:
        _suppress_callback(1.0)

    # Normalize incoming values to Title-case for consistent comparison
    cmd_upper   = cmd.strip().title()
    rfid_upper  = rfid_cmd.strip().title()
    light_upper = light_cmd.strip().title()
    lock_upper  = lock_cmd.strip().title()

    # =====================================================
    #  REBOOT  (filter MAC)
    # =====================================================
    if cmd_upper == "Reboot":
        if not is_mac_match(mac_target):
            log.debug("[MQTT] Reboot ignored - mac target '%s' doesn't match", mac_target)
            return

        log.info("[MQTT] Reboot command received - MAC matched!")
        log.info("[MQTT] Target: %s, My MAC: %s, My hostname: %s", mac_target, MAC, MAC_ADDRESS)

        try:
            mqttc.publish(
                "out",
                json.dumps({
                    "mac":      MAC_ADDRESS,
                    "hostname": HOSTNAME,
                    "event":    "Reboot",
                    "raw":      payload_raw
                }),
                qos=0,
                retain=False
            )
            log.info("[MQTT] Reboot echoed to 'out'")
            log.info("[REBOOT] Device rebooting in 2 seconds...")
            time.sleep(2)
            os.system("sudo reboot -f")

        except Exception as e:
            log.error("[MQTT] Failed during reboot preparation: %s", e)

        return

    # =====================================================
    #  SESSION START  (filter by IP)
    # =====================================================
    if cmd_upper == "Start" and session_id:
        if not is_ip_match(ip_target):
            log.debug("[SESSION] START ignored - ip target '%s' doesn't match LOCAL_IP '%s'",
                      ip_target, LOCAL_IP)
            return

        if scan_session_id:
            log.warning("[SESSION] START rejected - session '%s' still active, new '%s' denied",
                        scan_session_id, session_id)
            mqttc.publish(
                "out",
                json.dumps({
                    "mac":               MAC_ADDRESS,
                    "hostname":          HOSTNAME,
                    "info":              "Session already active, stop current session first",
                    "activeSessionId":   scan_session_id,
                    "rejectedSessionId": session_id,
                    "status":            "rejected"
                }),
                qos=1
            )
            return

        log.info("[SESSION] Start received, sessionId=%s", session_id)
        admin_sessions  = status.load_admin_sessions()
        is_admin        = session_id in admin_sessions
        scan_session_id = session_id
        status.set_session_id(session_id)

        count    = 0
        duration = 0
        if os.path.exists("rfid2.json"):
            try:
                with open("rfid2.json", "r") as f:
                    d        = json.load(f)
                    count    = d.get("count",    0)
                    duration = d.get("duration", 0)
            except Exception as e:
                log.warning("[SESSION] Failed read rfid2.json: %s", e)

        status.save_last_status(count=count, duration=duration)
        status.publish_last_status(count, duration)

        payload = {
            "mac":       MAC_ADDRESS,
            "hostname":  HOSTNAME,
            "info":      f"Session {session_id} started",
            "sessionId": session_id,
            "count":     count,
            "duration":  duration,
            "status":    "ok"
        }
        if is_admin:
            payload["admin"] = True

        mqttc.publish(TOPIC_STATUS, json.dumps(payload), qos=1)
        return

    # =====================================================
    #  SESSION STOP  (filter by IP)
    # =====================================================
    if cmd_upper == "Stopsession":
        if not is_ip_match(ip_target):
            log.debug("[SESSION] STOPSESSION ignored - ip target '%s' doesn't match LOCAL_IP '%s'",
                      ip_target, LOCAL_IP)
            return

        if session_id and scan_session_id and session_id != scan_session_id:
            log.warning("[SESSION] STOPSESSION rejected - sessionId '%s' doesn't match active '%s'",
                        session_id, scan_session_id)
            mqttc.publish(
                "out",
                json.dumps({
                    "mac":             MAC_ADDRESS,
                    "hostname":        HOSTNAME,
                    "info":            "Stop rejected, sessionId mismatch",
                    "activeSessionId": scan_session_id,
                    "sentSessionId":   session_id,
                    "status":          "rejected"
                }),
                qos=1
            )
            return

        log.info("[SESSION] Stop received, clearing sessionId='%s'", scan_session_id)
        prev_session_id = scan_session_id
        scan_session_id = ""
        status.set_session_id("")

        try:
            if os.path.exists(status.LAST_STATUS_FILE):
                with open(status.LAST_STATUS_FILE, "r") as f:
                    st = json.load(f)
                st["sessionId"] = ""
                with open(status.LAST_STATUS_FILE, "w") as f:
                    json.dump(st, f, indent=2)
                log.info("[SESSION] sessionId cleared in %s", status.LAST_STATUS_FILE)
        except Exception as e:
            log.error("[SESSION] Failed clear sessionId: %s", e)

        status.publish_last_status(0, 0)
        mqttc.publish(
            "out",
            json.dumps({
                "mac":            MAC_ADDRESS,
                "hostname":       HOSTNAME,
                "info":           "Session stopped",
                "stoppedSession": prev_session_id,
                "sessionId":      "",
                "status":         "ok"
            }),
            qos=1
        )
        return

    # =====================================================
    #  LIGHT / LOCK HARDWARE
    # =====================================================
    if light_upper in ("On", "Off") or lock_upper in ("Lock", "Unlock"):
        if not is_mac_match(mac_target):
            log.debug("[MQTT] light/lock ignored - mac target '%s' doesn't match", mac_target)
            return

        log.info("[MQTT] light='%s' lock='%s' hardware handled by lightLockControl.py",
                 light_cmd, lock_cmd)
        log.debug("[MQTT] Waiting 200ms for hardware to settle before status update...")
        time.sleep(0.2)

    # =====================================================
    #  STATUS UPDATE AFTER COMMAND
    # =====================================================
    if not is_mac_match(mac_target):
        log.debug("[MQTT] Status update skipped - mac target '%s' doesn't match", mac_target)
        return

    try:
        count    = 0
        duration = 0
        if os.path.exists("rfid2.json"):
            with open("rfid2.json", "r") as f:
                d        = json.load(f)
                count    = d.get("count",    0)
                duration = d.get("duration", 0)

        status.publish_last_status(count, duration)
        log.info("[STATUS] status.json updated & published to 'out'")
    except Exception as e:
        log.error("[STATUS] Status update error: %s", e)

    # =====================================================
    #  SCAN START
    # =====================================================
    if rfid_upper == "Startscan":
        if not is_mac_match(mac_target):
            log.debug("[SCAN] STARTSCAN ignored - mac target '%s' doesn't match", mac_target)
            return

        log.info("[SCAN] Startscan sessionid=%s, mac=%s time='%s' sig='%s'",
                  session_id, mac_target, sig_time, sig_value)

        if not SCAN_MUTEX.acquire(wait=False, owner="MQTT-SCAN"):
            log.warning(
                "[SCAN] Startscan REJECTED - a scan is already in progress "
                "(mutex busy, could be door-triggered or another MQTT client) "
                "session=%s mac=%s", scan_session_id, mac_target or "any"
            )
            # NOTE: rejection is intentionally LOCAL ONLY (log file on this
            # Raspberry Pi) - it is NOT published back to MQTT topic "out".
            return

        stop_requested.clear()
        scan_requested.set()
        GPIO.output(reader.SHUTDOWN_GPIO, GPIO.LOW)

        # Build the time/sig that will be attached to the "Scanning"
        # broadcast (published by startstops.main() once the door-wait
        # confirms and scanning actually begins):
        #   - Startscan arrived WITH a signature -> pass it through
        #     unchanged (same time + same sig), so the "Scanning"
        #     message traces back to the exact same source as the
        #     original Startscan command.
        #   - Startscan arrived WITHOUT a signature (old code / no
        #     source) -> compute a fresh one now: md5(eth0 + time),
        #     no source component at all.
        eth0_key = MAC.lower()
        if sig_time and sig_value:
            trigger_time = sig_time
            trigger_sig  = sig_value
        else:
            trigger_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            trigger_sig  = hashlib.md5(f"{eth0_key}{trigger_time}".encode("utf-8")).hexdigest()

        def _scan_worker():
            try:
                log.info("[SCAN] Worker started (session=%s)", scan_session_id)
                startstops.main(
                    scan_lock=is_scanning,
                    stop_evt=stop_requested,
                    scan_session_id=scan_session_id,
                    mqttc=mqttc,
                    topic_status=TOPIC_STATUS,
                    topic_door=TOPIC_DOOR,
                    trigger_time=trigger_time,
                    trigger_sig=trigger_sig,
                )
            except Exception as e:
                log.error("[SCAN] Worker error: %s", e)
            finally:
                SCAN_MUTEX.release(owner="MQTT-SCAN")
                log.info("[SCAN] Worker finished, mutex released")

        threading.Thread(target=_scan_worker, daemon=True).start()
        return

    # =====================================================
    #  SCAN STOP
    # =====================================================
    if rfid_upper == "Stopscan":
        if not is_mac_match(mac_target):
            log.debug("[SCAN] STOPSCAN ignored - mac target '%s' doesn't match", mac_target)
            return

        log.info("[SCAN] Stopscan sessionid=%s, mac=%s time='%s' sig='%s'",
                  session_id, mac_target, sig_time, sig_value)

        log.info("[SCAN] STOP requested (session=%s, mac=%s)", scan_session_id, MAC_ADDRESS)
        stop_requested.set()
        scan_requested.clear()
        startstops.stop_all(stop_requested)
        return


# =====================================================
#  MQTT SETUP
# =====================================================
def mqtt_setup_safe(client_suffix="main"):
    global mqttc
    client_id = f"{HOSTNAME}-{client_suffix}"
    mqttc = mqtts.Client(client_id=client_id, clean_session=True)

    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    mqttc.connect(BROKER, 1883, 60)
    mqttc.loop_start()

    log.info("[MQTT] Client started id=%s broker=%s", client_id, BROKER)
    status.set_mqtt_publisher(mqttc.publish, TOPIC_STATUS)
