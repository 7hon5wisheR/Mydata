# light.py
# -*- coding: utf-8 -*-
__version__ = "0.0.0.1 - normalize status title-case"

# =====================================================================================================================================
#      LIGHT
#      Name                       : LIGHT 
#      Version                    : 0.0.0.1 - normalize status title-case
#      Date Created               : 08-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================

import RPi.GPIO as GPIO
from mutex import FileMutex
import time
import threading 

# =====================================================
# LIGHT GPIO
# =====================================================
LIGHT_GPIO = 16
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(LIGHT_GPIO, GPIO.OUT)  # no initial - do not reset pin on import

# =====================================================
# MUTEX
# =====================================================
_light_mutex = FileMutex("light")


def _light_on():
    GPIO.output(LIGHT_GPIO, GPIO.HIGH)
    print("[LIGHT] HW ON")


def _light_off():
    GPIO.output(LIGHT_GPIO, GPIO.LOW)
    print("[LIGHT] HW OFF")


# =====================================================
# MUTEX HANDLER
# =====================================================
def _acquire_mutex(action="LIGHT", wait=True):
    print(f"[LIGHT] Trying to acquire mutex for {action}...")
    acquired = _light_mutex.acquire(wait=False, owner="LIGHT")
    if not acquired:
        print(f"[LIGHT] Mutex busy, waiting to acquire {action}...")
        acquired = _light_mutex.acquire(wait=True, owner="LIGHT")
    print(f"[LIGHT] Mutex acquired for {action}")
    return True


def light_on(wait=True):
    _acquire_mutex("ON", wait)
    try:
        print("[LIGHT] Turning ON")
        _light_on()
    finally:
        _light_mutex.release(owner="LIGHT")
        print("[LIGHT] Mutex released after ON")


def light_off(wait=True):
    _acquire_mutex("OFF", wait)
    try:
        print("[LIGHT] Turning OFF")
        _light_off()
    finally:
        _light_mutex.release(owner="LIGHT")
        print("[LIGHT] Mutex released after OFF")


def light_on_for(seconds, wait=True):
 
    def _worker():
        _acquire_mutex("ON", wait)
        try:
            print(f"[LIGHT] ON for {seconds}s")
            _light_on()
            time.sleep(seconds)
        finally:
            _light_mutex.release(owner="LIGHT")
            print("[LIGHT] Mutex released (light_on_for)")

    threading.Thread(target=_worker, daemon=True).start()


def light_off_for(seconds, wait=True):
   
    def _worker():
        _acquire_mutex("OFF", wait)
        try:
            print(f"[LIGHT] OFF for {seconds}s")
            _light_off()
            time.sleep(seconds)
        finally:
            _light_mutex.release(owner="LIGHT")
            print("[LIGHT] Mutex released (light_off_for)")

    threading.Thread(target=_worker, daemon=True).start()


def get_light_status():
    return "On" if GPIO.input(LIGHT_GPIO) else "Off"


def light_set(state, seconds=None):
   
    if seconds:
        if state:
            light_on_for(seconds)
        else:
            light_off_for(seconds)
    else:
        if state:
            light_on()
        else:
            light_off()
    return True
