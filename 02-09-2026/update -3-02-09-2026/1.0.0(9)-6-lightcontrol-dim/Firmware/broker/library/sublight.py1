#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# sublight.py
# PWM light control module: sub_on() / sub_off().
#
# CROSS-PROCESS - REUSES THE EXISTING status.json:
#   lightLockControl.py runs as its own service, separate from
#   run.py/mqtts.py. To keep the light status readable by the other
#   process WITHOUT creating a new file (status.json is already sent
#   to the web dashboard - we don't want two status sources that need
#   to be manually kept in sync), this module reads/writes the
#   "Light" field directly in the EXISTING status.json - the same
#   field status.py has always used. Other fields in status.json
#   (Door1, Lock1, sessionId, etc.) are left untouched - this is a
#   read-modify-write, only the "Light" key is changed.
#
# CONFIG BRIGHTNESS KEY NOTE:
#   config.json uses the key "dim" (not "brightness"), on an INVERTED
#   scale:
#     dim = 0    -> brightest -> PWM duty = 100%
#     dim = 100  -> dimmest   -> PWM duty = 0%
#   sub_on() reads "dim" and inverts it into a PWM duty cycle.
#   sub_off() NEVER writes to config.json - the stored dim value stays
#   untouched.
#
# SINGLE GPIO OWNER (IMPORTANT):
#   This module is the ONLY thing in the whole system allowed to open
#   GPIO.PWM on pins 16/18. light_pwm.py's old "no-argument watch
#   config.json forever" mode used to duplicate this in a SEPARATE
#   process, which created a second competing PWM object on the same
#   physical pins -> the two signals fought each other -> flicker.
#   That mode has been removed from light_pwm.py. If you need live
#   "brightness changes while the light is already on" behaviour, use
#   start_dim_watcher() below (runs as a thread INSIDE this same
#   process/owner, started from lightLockControl.py).

__version__ = "1.0.0(10) - added in-process dim watcher, single GPIO owner"

# =====================================================================================================================================
#      SUBLIGHT
#      Name                       : SUBLIGHT
#      Version                    : 1.0.0(10)
#      Date Created               : 08-05-2026
#      Updated                    : 28-08-2026
#      Changes                    : Added start_dim_watcher() - an in-process
#                                   background thread that polls config.json's
#                                   "dim" field and live-applies brightness
#                                   changes WHILE the light is On, without
#                                   needing a second process/GPIO owner
#                                   (previously done insecurely by running
#                                   light_pwm.py with no arguments as a
#                                   separate service).
#      Author                     : Saifuddin
# ======================================================================================================================================


import json
import os
import threading
import time
import RPi.GPIO as GPIO
from mutex import FileMutex

from logger import get_logger
log = get_logger("sublight")

# =====================================================
# GPIO CONFIG
# =====================================================
ENABLE_GPIO = 16   # driver enable / power gate pin
PWM_GPIO    = 18   # PWM signal pin (brightness control)
PWM_FREQ_HZ = 1000

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATUS_PATH = os.path.join(BASE_DIR, "status.json")   # EXISTING file, reused here

DIM_MIN = 0
DIM_MAX = 100
DIM_POLL_SEC = 0.5   # how often the in-process watcher checks config.json

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(ENABLE_GPIO, GPIO.OUT)
GPIO.setup(PWM_GPIO, GPIO.OUT)

_pwm = GPIO.PWM(PWM_GPIO, PWM_FREQ_HZ)
_pwm.start(0)
GPIO.output(ENABLE_GPIO, GPIO.LOW)

# Mutex named "light" - same as the old light.py, still keeps GPIO
# access mutually exclusive across threads within this process.
_mutex = FileMutex("light")

# Tracks whether the light is currently supposed to be ON, so the
# dim watcher below only re-applies brightness while On (never turns
# the light on/off by itself - that stays under sub_on()/sub_off()).
_is_on = False


def set_pwm(value):
    """Set the raw PWM duty cycle (0-100) and toggle the enable pin accordingly."""
    value = max(0, min(100, int(value)))
    if value <= 0:
        _pwm.ChangeDutyCycle(0)
        GPIO.output(ENABLE_GPIO, GPIO.LOW)
    else:
        GPIO.output(ENABLE_GPIO, GPIO.HIGH)
        _pwm.ChangeDutyCycle(value)


def _read_configured_brightness() -> int:
    """
    Read "dim" from config.json (live, on every call) and invert it
    into a PWM duty cycle. Defaults to 100% (brightest) if
    config.json is missing, unreadable, or the field is empty.
    """
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        dim = int(cfg.get("dim", 0))
    except Exception as e:
        log.warning("[SUBLIGHT] Failed to read 'dim' from %s: %s. Defaulting dim=0 (brightest).",
                    CONFIG_PATH, e)
        dim = 0

    dim = max(DIM_MIN, min(DIM_MAX, dim))
    return DIM_MAX - dim   # dim=0 -> 100% ; dim=100 -> 0%


# =====================================================
# READ/WRITE THE "Light" FIELD IN status.json (not a new file)
# =====================================================
def _update_status_light(state: str):
    try:
        st = {}
        if os.path.exists(STATUS_PATH):
            with open(STATUS_PATH) as f:
                st = json.load(f)
        st["Light"] = state
        with open(STATUS_PATH, "w") as f:
            json.dump(st, f, indent=2)
    except Exception as e:
        log.error("[SUBLIGHT] Failed to update 'Light' in %s: %s", STATUS_PATH, e)


def get_light_status() -> str:
    """
    Called by status.py as a replacement for light.get_light_status().
    Reads the "Light" field directly from status.json (the same file
    already sent to the web) - not a separate file.
    """
    try:
        if os.path.exists(STATUS_PATH):
            with open(STATUS_PATH) as f:
                st = json.load(f)
            s = st.get("Light", "Off")
            if s in ("On", "Off"):
                return s
    except Exception as e:
        log.warning("[SUBLIGHT] Failed to read 'Light' from %s: %s", STATUS_PATH, e)
    return "Off"


# =====================================================
# PUBLIC API - permanent ON/OFF
# =====================================================
def sub_on():
    """
    Read the configured brightness (config.json['dim']) and set the
    PWM output accordingly. NEVER writes to config.json. Updates the
    "Light" field in the existing status.json so other processes know
    the current state.
    """
    global _is_on
    _mutex.acquire(wait=True, owner="LIGHT")
    try:
        brightness = _read_configured_brightness()
        set_pwm(brightness)
        _update_status_light("On")
        _is_on = True
        print(f"SUB ON -> brightness {brightness}%")
        log.info("[SUBLIGHT] ON -> brightness %s%%", brightness)
    finally:
        _mutex.release(owner="LIGHT")


def sub_off():
    """
    Force brightness to 0%. NEVER writes to config.json - the stored
    'dim' value stays intact for the next sub_on() call.
    """
    global _is_on
    _mutex.acquire(wait=True, owner="LIGHT")
    try:
        set_pwm(0)
        _update_status_light("Off")
        _is_on = False
        print("SUB OFF -> brightness 0%")
        log.info("[SUBLIGHT] OFF -> brightness 0%%")
    finally:
        _mutex.release(owner="LIGHT")


# =====================================================
# PUBLIC API - timed ON/OFF
# =====================================================
def sub_on_for(seconds):
    def _worker():
        global _is_on
        _mutex.acquire(wait=True, owner="LIGHT")
        try:
            brightness = _read_configured_brightness()
            set_pwm(brightness)
            _update_status_light("On")
            _is_on = True
            print(f"SUB ON for {seconds}s -> brightness {brightness}%")
            time.sleep(seconds)
        finally:
            set_pwm(0)
            _update_status_light("Off")
            _is_on = False
            _mutex.release(owner="LIGHT")
    threading.Thread(target=_worker, daemon=True).start()


def sub_off_for(seconds):
    def _worker():
        global _is_on
        _mutex.acquire(wait=True, owner="LIGHT")
        try:
            set_pwm(0)
            _update_status_light("Off")
            _is_on = False
            print(f"SUB OFF for {seconds}s -> brightness 0%")
            time.sleep(seconds)
        finally:
            _mutex.release(owner="LIGHT")
    threading.Thread(target=_worker, daemon=True).start()


def sub_set(state, seconds=None):
    """Convenience wrapper, drop-in replacement for light.light_set(state, seconds)."""
    if seconds:
        if state:
            sub_on_for(float(seconds))
        else:
            sub_off_for(float(seconds))
    else:
        if state:
            sub_on()
        else:
            sub_off()
    return True


# =====================================================
# NEW: IN-PROCESS DIM WATCHER
# Replaces light_pwm.py's old no-arg watch mode. Runs as a thread
# INSIDE this same process (started by lightLockControl.py's main()),
# so there is still only ONE GPIO.PWM owner for pins 16/18 - no
# second process, no flicker.
# =====================================================
def start_dim_watcher():
    """
    Start a background thread that polls config.json's "dim" field
    and, ONLY WHILE THE LIGHT IS CURRENTLY ON (_is_on == True),
    re-applies the new brightness live - e.g. if an admin changes
    dim in config.json while the light is already on, it updates
    immediately without needing a new MQTT "light: On" command.

    Does NOT turn the light on/off by itself - only adjusts
    brightness while it's already on. Safe to call once from
    lightLockControl.py's main().
    """
    def _watch():
        last_mtime = None
        last_dim = None
        while True:
            try:
                mtime = os.path.getmtime(CONFIG_PATH)
            except OSError:
                mtime = None

            if mtime != last_mtime or last_dim is None:
                try:
                    with open(CONFIG_PATH) as f:
                        cfg = json.load(f)
                    dim = int(cfg.get("dim", 0))
                    dim = max(DIM_MIN, min(DIM_MAX, dim))
                except Exception as e:
                    log.warning("[SUBLIGHT] dim watcher: could not read config.json: %s", e)
                    dim = last_dim

                if dim != last_dim:
                    if _is_on and dim is not None:
                        brightness = DIM_MAX - dim
                        _mutex.acquire(wait=True, owner="LIGHT")
                        try:
                            set_pwm(brightness)
                            log.info("[SUBLIGHT] Live dim change -> brightness %s%% (dim=%s)", brightness, dim)
                        finally:
                            _mutex.release(owner="LIGHT")
                    last_dim = dim
                last_mtime = mtime

            time.sleep(DIM_POLL_SEC)

    threading.Thread(target=_watch, daemon=True).start()
    log.info("[SUBLIGHT] In-process dim watcher started (single GPIO owner preserved)")
