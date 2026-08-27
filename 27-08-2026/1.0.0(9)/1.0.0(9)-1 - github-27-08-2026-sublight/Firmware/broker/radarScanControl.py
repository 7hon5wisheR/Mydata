#!/usr/bin/env python3
__version__ = "1.0.0(9) - normalize cmd title-case"

import os
import sys
import time
import json
import threading
import hashlib
import RPi.GPIO as GPIO
import paho.mqtt.client as mqtt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR  = os.path.join(BASE_DIR, "library")
sys.path.insert(0, LIB_DIR)


# =====================================================================================================================================
#      RADAR CONTROL
#      Name                       : RADAR CONTROL - FIX-OPEN-ABORT
#      Version                    : 1.0.0(9)
#      Date Created               : 05-05-2026
#      Updated                    : 10-05-2026
#      Changes                    :
#        - handle_open() now also joins scan_thread with a short timeout
#          to prevent two handlers from running simultaneously during MQTT flood.
#        - handle_close() after time.sleep(0.5): if stop_evt is already set,
#          disable GPIO again (HIGH) before returning — previous version left
#          radar in LOW state (enabled) even though scan was aborted.
#        - Added _handler_lock for handle_open/handle_close serialization
#          so rapidly incoming MQTT messages do not overlap each other
#          (prevents race conditions between stop_evt.set() and stop_evt.clear()).
#      Author                     : Saifuddin
# ======================================================================================================================================

# === log ===
from logger import get_logger
log = get_logger("radar")
# ==================

import reader
import startstops
import mqtts

log.info("[INIT] RADAR SCAN CONTROL MODE")

# ==================================================
# GLOBAL
# ==================================================
scan_thread   = None
scan_lock     = threading.Lock()
stop_evt      = threading.Event()

# FIX: Lock for handle_open / handle_close serialization
# Prevents race conditions if MQTT sends OPENED & CLOSE almost simultaneously
_handler_lock = threading.Lock()

BROKER   = mqtts.BROKER
HOSTNAME = mqtts.HOSTNAME

_mqttc = mqtt.Client(
    client_id=f"{HOSTNAME}-radar",
    clean_session=True
)

# ==================================================
# HANDLERS
# ==================================================
def handle_open():

    # FIX: Serialize handler — non-blocking acquire,
    # skip if another handler is currently running
    if not _handler_lock.acquire(blocking=True, timeout=2):
        log.warning("[RADAR] handle_open: handler_lock timeout, skip")
        return

    try:
        log.info("[RADAR] DOOR OPEN -> STOP SCAN")

        try:
            with open("stop.flag", "w") as f:
                f.write("1")

        except Exception as e:
            log.error("[RADAR] stop.flag error: %s", e)

        # Set stop_evt BEFORE disabling GPIO
        stop_evt.set()

        # HIGH = OFF = radar disabled
        GPIO.output(reader.SHUTDOWN_GPIO, GPIO.HIGH)

        log.info("[RADAR] Radar hardware disabled (GPIO HIGH)")

        # FIX: Wait for scan_thread to finish with short timeout
        # Ensures startstops.main() actually detects stop_evt
        # before the next handler (handle_close) starts
        if scan_thread and scan_thread.is_alive():

            log.info("[RADAR] handle_open: waiting scan_thread to acknowledge stop...")

            scan_thread.join(timeout=2)

            if scan_thread.is_alive():
                log.warning("[RADAR] handle_open: scan_thread still alive after 2s (will be stopped by stop_evt)")
            else:
                log.info("[RADAR] handle_open: scan_thread stopped OK")

    finally:
        _handler_lock.release()


def handle_close():
    global scan_thread

    # FIX: Serialize handler
    if not _handler_lock.acquire(blocking=True, timeout=5):
        log.warning("[RADAR] handle_close: handler_lock timeout, skip")
        return

    try:
        log.info("[RADAR] DOOR CLOSED -> START SCAN")

        # Stop any previous scan first
        if scan_thread and scan_thread.is_alive():

            log.warning("[RADAR] Previous scan still alive, stopping first...")

            stop_evt.set()

            scan_thread.join(timeout=3)

            log.info("[RADAR] Previous scan stopped")

        # FIX: Double-check after join —
        # do not clear if another OPEN was already triggered
        # after join() (OPENED → CLOSED → OPENED rapidly)
        #
        # This is protected by _handler_lock:
        # handle_open cannot run simultaneously with handle_close,
        # therefore it is safe to clear here.

        # Clear BEFORE enabling GPIO
        # so scan thread never sees stale stop event
        stop_evt.clear()

        # Enable radar hardware (LOW = ON = reader enabled)
        GPIO.output(reader.SHUTDOWN_GPIO, GPIO.LOW)

        log.info("[RADAR] Radar hardware enabled (GPIO LOW)")

        time.sleep(0.5)   # allow radar to wake up

        # FIX:
        # If OPEN arrives during the 0.5s sleep,
        # stop_evt will be set by handle_open() —
        # but handle_open() CANNOT enter because
        # _handler_lock is still held by handle_close().
        #
        # Meaning:
        # OPEN arriving during the sleep will WAIT on
        # _handler_lock.acquire() until handle_close() finishes.
        #
        # After handle_close() releases lock →
        # handle_open() executes →
        # stop_evt.set() + GPIO HIGH.
        #
        # Therefore stop_evt.is_set() below should never become true
        # while _handler_lock is active.
        #
        # Still kept as a safety net for non-MQTT cases
        # (for example stop requests from elsewhere).
        if stop_evt.is_set():

            log.warning("[RADAR] stop_evt set after startup delay - aborting scan")

            # FIX:
            # Disable GPIO again,
            # do not leave radar in LOW state
            GPIO.output(reader.SHUTDOWN_GPIO, GPIO.HIGH)

            log.info("[RADAR] Radar hardware disabled (GPIO HIGH) - abort path")

            return

        # FIX: Guard against a concurrent scan started from elsewhere
        # (e.g. a manual {"rfid":"Startscan",...} command received by
        # mqtts.py, whether from this app's own UI, another department's
        # software, or any other MQTT client publishing to topic "in").
        #
        # mqtts.SCAN_MUTEX is a FileMutex backed by fcntl.flock on
        # /tmp/scan.lock - this works ACROSS PROCESSES (radarScanControl.py
        # and run.py/mqtts.py are separate OS processes), unlike scan_lock/
        # stop_evt above which are only local to this process. Acquiring it
        # here means the door-triggered scan and any manual MQTT-triggered
        # scan can never run at the same time - whichever gets here first
        # wins, the other is rejected.
        if not mqtts.SCAN_MUTEX.acquire(wait=False, owner="DOOR-SCAN"):
            log.warning(
                "[RADAR] Scan REJECTED - another scan is already in progress "
                "(mutex busy, likely a manual MQTT Startscan). "
                "Door-triggered scan skipped this cycle."
            )
            # We already enabled the radar hardware (GPIO LOW) above -
            # turn it back off since we're not actually going to scan.
            GPIO.output(reader.SHUTDOWN_GPIO, GPIO.HIGH)
            log.info("[RADAR] Radar hardware disabled (GPIO HIGH) - mutex reject path")
            return

        # Signature for the "Scanning" broadcast: this scan was triggered
        # by the door sensor (GPIO), not by any MQTT Startscan command -
        # source="GPIO" makes that traceable the same way as any other
        # source (md5("GPIO" + eth0 + time)).
        eth0_key     = mqtts.MAC.lower()
        trigger_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        trigger_sig  = hashlib.md5(f"GPIO{eth0_key}{trigger_time}".encode("utf-8")).hexdigest()

        def _door_scan_worker():
            try:
                startstops.main(
                    scan_lock,
                    stop_evt,
                    mqtts.scan_session_id,
                    _mqttc,
                    "out",
                    "out",
                    trigger_time=trigger_time,
                    trigger_sig=trigger_sig,
                )
            except Exception as e:
                log.error("[RADAR] Scan worker error: %s", e)
            finally:
                mqtts.SCAN_MUTEX.release(owner="DOOR-SCAN")
                log.info("[RADAR] Mutex released (door-triggered scan finished)")

        scan_thread = threading.Thread(
            target=_door_scan_worker,
            daemon=True
        )

        scan_thread.start()

        log.info("[RADAR] Scan thread started")

    finally:
        _handler_lock.release()


# ==================================================
# MQTT CALLBACKS
# ==================================================
def on_connect(client, userdata, flags, rc):
    log.info("[RADAR] MQTT Connected rc=%s", rc)

    client.subscribe("out", qos=1)

    log.info("[RADAR] Subscribed to topic: out")


def on_message(client, userdata, msg):

    try:
        payload = json.loads(msg.payload.decode())
        cmd     = payload.get("cmd", "").strip().title()

        log.info("[RADAR] Message cmd='%s': %s", cmd, payload)

        if not cmd:
            return

        if cmd == "Open":

            # Run in separate thread so MQTT loop is not blocked
            threading.Thread(target=handle_open, daemon=True).start()

        elif cmd == "Close":

            threading.Thread(target=handle_close, daemon=True).start()

        else:
            log.warning("[RADAR] Unknown cmd=%s, skip", cmd)

    except Exception as e:
        log.error("[RADAR] MQTT ERROR: %s", e)


# ==================================================
# MAIN
# ==================================================
def main():

    _mqttc.on_connect = on_connect
    _mqttc.on_message = on_message

    log.info("[RADAR] Connecting to MQTT broker %s:1883 ...", BROKER)

    _mqttc.connect(BROKER, 1883, 60)

    _mqttc.loop_start()

    mqtts.mqttc = _mqttc

    log.info("[RADAR] Waiting for door events...")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()