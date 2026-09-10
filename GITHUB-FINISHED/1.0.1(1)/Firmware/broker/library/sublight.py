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
#   GPIO.PWM on pins 16/18. It is imported by BOTH lightLockControl.py
#   (the real owner) AND run.py (which only needs get_light_status()).
#   To keep run.py from accidentally becoming a second PWM owner just
#   by importing this file, GPIO/PWM setup is now LAZY: it does NOT
#   run at import time. It only runs the first time a GPIO-touching
#   function (set_pwm/sub_on/sub_off/start_dim_watcher) is actually
#   called - which in practice only happens inside lightLockControl.py.
#   get_light_status()/_update_status_light() never touch GPIO, so
#   run.py stays a pure file-reader and never claims the pins.

__version__ = "1.0.0(10) - fixed CONFIG_PATH/STATUS_PATH to point at the real broker/ dir, not library/"

# =====================================================================================================================================
#      SUBLIGHT
#      Name                       : SUBLIGHT
#      Version                    : 1.0.0(10)
#      Date Created               : 08-05-2026
#      Updated                    : 01-09-2026
#      Changes                    : FIX - this file lives at
#                                   broker/library/sublight.py, but
#                                   BASE_DIR was computed as
#                                   os.path.dirname(os.path.abspath(__file__)),
#                                   i.e. broker/library/ - the folder
#                                   sublight.py itself sits in. That made
#                                   CONFIG_PATH resolve to
#                                   broker/library/config.json and
#                                   STATUS_PATH to broker/library/status.json,
#                                   NOT the real broker/config.json /
#                                   broker/status.json that apps.py,
#                                   status.py, and everything else actually
#                                   read/write. Result: changing "dim" via
#                                   apps.py had ZERO effect on the light,
#                                   no matter what (start_dim_watcher()
#                                   running or not - it was polling the
#                                   wrong file the whole time). Fixed by
#                                   walking one directory up from
#                                   library/ to the actual broker/ root.
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

# sublight.py lives in broker/library/ - the real config.json and
# status.json live one level up, in broker/ itself (same directory as
# apps.py, lightLockControl.py, run.py, etc). Walk up one directory
# from this file's own folder to reach it - do NOT use this file's own
# directory directly (that was the bug: it silently pointed at
# broker/library/config.json, a different, unmanaged file).
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATUS_PATH = os.path.join(BASE_DIR, "status.json")   # EXISTING file, reused here

DIM_MIN = 0
DIM_MAX = 100
DIM_POLL_SEC = 0.5   # how often the in-process watcher checks config.json

# Mutex named "light" - same as the old light.py, still keeps GPIO
# access mutually exclusive across threads within this process.
_mutex = FileMutex("light")

# Tracks whether the light is currently supposed to be ON, so the
# dim watcher below only re-applies brightness while On (never turns
# the light on/off by itself - that stays under sub_on()/sub_off()).
_is_on = False

# =====================================================
# LAZY GPIO INIT (see "SINGLE GPIO OWNER" note above)
# =====================================================
_pwm = None
_gpio_ready = False
_gpio_init_lock = threading.Lock()


def _ensure_gpio():
    """
    Set up GPIO + create the GPIO.PWM object, but only the FIRST time
    this is actually called - not automatically on import. This is
    what makes single-GPIO-ownership hold across processes: a process
    that only imports sublight.py to call get_light_status() (e.g.
    run.py) never triggers this, so it never claims pins 16/18.
    Only lightLockControl.py, which calls sub_on()/sub_off()/
    start_dim_watcher(), becomes the owner.
    """
    global _pwm, _gpio_ready
    if _gpio_ready:
        return
    with _gpio_init_lock:
        if _gpio_ready:
            return
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(ENABLE_GPIO, GPIO.OUT)
        GPIO.setup(PWM_GPIO, GPIO.OUT)

        _pwm = GPIO.PWM(PWM_GPIO, PWM_FREQ_HZ)
        _pwm.start(0)
        GPIO.output(ENABLE_GPIO, GPIO.LOW)

        _gpio_ready = True
        log.info("[SUBLIGHT] GPIO initialized lazily - this process is now the PWM owner")


def set_pwm(value):
    """Set the raw PWM duty cycle (0-100) and toggle the enable pin accordingly."""
    _ensure_gpio()
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
# No GPIO involved - safe to call from ANY process (e.g. run.py).
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
    Called by status.py / run.py as a replacement for
    light.get_light_status(). Reads the "Light" field directly from
    status.json (the same file already sent to the web) - not a
    separate file, and no GPIO access, so this is safe to call from
    a process that is NOT the GPIO owner.
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
    _ensure_gpio()
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
    _ensure_gpio()
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
        _ensure_gpio()
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
        _ensure_gpio()
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
# IN-PROCESS DIM WATCHER
# Replaces light_pwm.py's old no-arg watch mode. Runs as a thread
# INSIDE this same process (started by lightLockControl.py's main()),
# so there is still only ONE GPIO.PWM owner for pins 16/18 - no
# second process, no flicker.
# =====================================================
def start_dim_watcher():
    """
    Start a background thread that polls config.json's "dim" field
    and, ONLY WHILE THE LIGHT IS CURRENTLY ON (_is_on == True),
    re-applies the new brightness live. Safe to call once from
    lightLockControl.py's main() - it is the call that makes THAT
    process the GPIO owner (via _ensure_gpio()).
    """
    _ensure_gpio()

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
    log.info("[SUBLIGHT] In-process dim watcher started (single GPIO owner preserved), watching %s", CONFIG_PATH)
