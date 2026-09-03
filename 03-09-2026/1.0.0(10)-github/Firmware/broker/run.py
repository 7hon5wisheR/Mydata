#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - migrated to sublight, light-on-startup delegated via MQTT"

import os
import sys
import time
import json
import threading
import RPi.GPIO as GPIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR  = os.path.join(BASE_DIR, "library")
sys.path.insert(0, LIB_DIR)



# =====================================================================================================================================
#      RUN
#      Name                       : RUN - fix double publish on worker
#      Version                    : 1.0.0(9)
#      Date Created               : 05-05-2026
#      Updated                    : 28-08-2026
#      Changes                    : light.py replaced with sublight.py.
#                                   IMPORTANT: run.py does NOT import sublight's
#                                   GPIO-controlling functions (sub_on/sub_off) -
#                                   lightLockControl.py is the sole owner of the
#                                   light's GPIO/PWM pins (16/18). If run.py also
#                                   called sublight.sub_on() directly, it would
#                                   create a SECOND GPIO.PWM object on the same
#                                   physical pins from a separate process,
#                                   fighting lightLockControl.py for control.
#                                   Instead:
#                                     - light_status_worker() now reads status via
#                                       sublight.get_light_status(), which only
#                                       reads status.json (a plain file read) -
#                                       safe, no GPIO involved.
#                                     - The "light must turn on at startup"
#                                       requirement is now done by PUBLISHING an
#                                       MQTT command to topic "in" (the same way
#                                       any external client would ask for the
#                                       light to turn on), so lightLockControl.py
#                                       - the actual GPIO owner - executes it
#                                       through its normal command path. This
#                                       forces the light ON even if status.json
#                                       currently says "Off".
#      Author                     : Saifuddin
# ======================================================================================================================================

from logger import get_logger
log = get_logger("run")

import reader
import utilitys
import lock1
import sublight
import door
import startstops
import mqtts
import status

log.info("[INIT] MODE: SERVICE (REAL GPIO)")

STATUS_FILE = os.path.join(BASE_DIR, "status.json")

# ==================================================
# SUPPRESS FLAG
# Prevents worker from publishing while an MQTT command
# is being processed by on_message (lightLockControl, etc.)
# ==================================================
_suppress_worker_publish = threading.Event()


def suppress_worker(duration=1.0):
    """
    Set suppress flag for `duration` seconds.
    Called from mqtts.on_message when a command is received.
    Prevents lock/light workers from publishing status simultaneously.
    """
    _suppress_worker_publish.set()
    log.debug("[RUN] Worker publish suppressed for %.1fs", duration)

    def _clear():
        time.sleep(duration)
        _suppress_worker_publish.clear()
        log.debug("[RUN] Worker publish suppress cleared")

    threading.Thread(target=_clear, daemon=True).start()


# ==================================================
# HELPERS
# ==================================================
def get_session_id():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f).get("sessionId", "")
    except Exception as e:
        log.warning("[INIT] Failed read sessionId: %s", e)
        return ""


# ==================================================
# WORKERS
# ==================================================
def lock_status_worker():
    last     = None
    last_log = 0
    while True:
        try:
            now = time.time()
            cur = lock1.get_lock1_status()
            if now - last_log > 10:
                log.debug("[LOCK] %s STATUS=%s", time.strftime("%F %T"), cur)
                last_log = now
            if cur != last:
                log.info("[LOCK] STATUS CHANGED -> %s", cur)
                if not _suppress_worker_publish.is_set():
                    status.publish_last_status(0, 0)
                else:
                    log.debug("[LOCK] Publish suppressed (MQTT command active)")
                last = cur
        except Exception as e:
            log.error("[LOCK] Worker error: %s", e)
        time.sleep(0.2)


def light_status_worker():
    """
    Watches the light's state and re-publishes status.json whenever it
    changes. lightLockControl.py (a separate service/process) is the
    one that actually flips the light and writes "Light"/"LightPWM"
    into status.json - this worker just detects that and broadcasts
    it over MQTT "out", since the MQTT publisher is only wired up in
    THIS process (run.py). sublight.get_light_status() only reads
    status.json (a file), so this is safe to call from run.py's
    process - it does not touch GPIO and does not create a second
    PWM owner.
    """
    last     = None
    last_log = 0
    while True:
        try:
            now = time.time()
            cur = sublight.get_light_status()
            if now - last_log > 10:
                log.debug("[LIGHT] %s STATUS=%s", time.strftime("%F %T"), cur)
                last_log = now
            if cur != last:
                log.info("[LIGHT] STATUS CHANGED -> %s", cur)
                if not _suppress_worker_publish.is_set():
                    status.publish_last_status(0, 0)
                else:
                    log.debug("[LIGHT] Publish suppressed (MQTT command active)")
                last = cur
        except Exception as e:
            log.error("[LIGHT] Worker error: %s", e)
        time.sleep(0.2)


def door_status_worker():
    last1 = last2 = None
    while True:
        try:
            d1 = door.get_door1_status()
            d2 = door.get_door2_status()
            if d1 != last1 or d2 != last2:
                log.info("[DOOR] DOOR1=%s | DOOR2=%s", d1, d2)
                last1, last2 = d1, d2
        except Exception as e:
            log.error("[DOOR] Worker error: %s", e)
        time.sleep(0.2)


def log_door_status():
    while True:
        try:
            log.debug("[GPIO TEST] RAW=%s | API=%s",
                      GPIO.input(door.DOOR1_GPIO),
                      door.get_door1_status())
        except Exception as e:
            log.error("[DOOR LOG] Error: %s", e)
        time.sleep(5)


# ==================================================
# STARTUP
# ==================================================
def startup_init():
    """
    Set GPIO defaults on startup.

    NOTE ON LIGHT: unlike lock1/reader below (which run.py owns and
    drives directly), the light is NOT set here via direct GPIO calls
    anymore. It is turned on by publishing an MQTT "in" command (see
    force_light_on_at_startup()) so that lightLockControl.py - the
    sole owner of the light's GPIO/PWM pins - is the one that actually
    executes it, through the exact same code path as any other light
    command. This keeps GPIO/PWM ownership of the light pins in a
    single process.
    """
    log.info("[INIT] Startup init")
    GPIO.output(lock1.LOCK1_GPIO,       GPIO.LOW)
    GPIO.output(reader.SHUTDOWN_GPIO,   GPIO.HIGH)
    mqtts.stop_requested.set()
    mqtts.scan_requested.clear()
    log.info("[INIT] GPIO defaults set: LOCK=LOW, READER=HIGH (LIGHT handled via MQTT - see force_light_on_at_startup)")


def force_light_on_at_startup():
    """
    The device must always start with the light ON, regardless of
    whatever "Light" value is currently sitting in status.json (it
    may say "Off" from before a reboot/crash). Publish the same MQTT
    command a normal client would send, so lightLockControl.py turns
    the light on (using config.json's "dim" for brightness) through
    its normal, single-owner GPIO code path - run.py itself never
    touches the light's GPIO/PWM directly.

    Must be called AFTER mqtts.mqtt_setup_safe() so mqtts.mqttc is
    connected and ready to publish.
    """
    try:
        payload = json.dumps({"light": "On", "mac": "all"})
        mqtts.mqttc.publish("in", payload, qos=1, retain=False)
        log.info("[INIT] Published startup light-on command to topic 'in': %s", payload)
    except Exception as e:
        log.error("[INIT] Failed to publish startup light-on command: %s", e)


def periodic_scan():
    time.sleep(10)
    log.info("[SCAN] Startup scan triggered")
    threading.Thread(
        target=startstops.main,
        args=(
            mqtts.is_scanning,
            mqtts.stop_requested,
            mqtts.scan_session_id,
            mqtts.mqttc,
            mqtts.TOPIC_STATUS,
            mqtts.TOPIC_DOOR,
        ),
        daemon=True
    ).start()


# ==================================================
# MAIN
# ==================================================
def main():
    startup_init()

    threading.Thread(target=lock_status_worker,  daemon=True).start()
    threading.Thread(target=light_status_worker, daemon=True).start()
    threading.Thread(target=door_status_worker,  daemon=True).start()

    mqtts.mqtt_setup_safe("run")
    log.info("[INIT] MQTT started")

    # Register suppress_worker to mqtts so on_message
    # can suppress workers before publishing its own status
    mqtts.set_suppress_callback(suppress_worker)
    log.info("[INIT] Suppress callback registered to mqtts")

    # Force the light ON at startup (see docstring) - must happen
    # after mqtt_setup_safe() so mqtts.mqttc is connected.
    force_light_on_at_startup()

    threading.Thread(target=status.periodic_status_update, daemon=True).start()
    threading.Thread(target=periodic_scan,                 daemon=True).start()
    threading.Thread(target=log_door_status,               daemon=True).start()

    log.info("[INIT] All workers started, entering main loop")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
