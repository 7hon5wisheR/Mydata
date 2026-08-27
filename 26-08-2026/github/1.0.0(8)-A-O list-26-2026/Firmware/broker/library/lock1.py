# lock1.py
# -*- coding: utf-8 -*-
__version__ = "0.0.0.1 - normalize status title-case"

# =====================================================================================================================================
#      LIGHT
#      Name                       : LIGHT 
#      Version                    : 0.0.0.1 - normalize status title-case
#      Date Created               : 08-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================



import time
import threading
import RPi.GPIO as GPIO
from mutex import FileMutex

# =====================================================
# GPIO
# =====================================================
LOCK1_GPIO = 24

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(LOCK1_GPIO, GPIO.OUT)  # no initial - do not reset pin on import  

# =====================================================
# MUTEX 
# =====================================================
_lock_mutex = FileMutex("lock1")


def _lock_hw():
    GPIO.output(LOCK1_GPIO, GPIO.LOW)   
    print("[LOCK1] HW LOCKED")


def _unlock_hw():
    GPIO.output(LOCK1_GPIO, GPIO.HIGH) 
    print("[LOCK1] HW UNLOCKED")


# =====================================================
# MUTEX
# =====================================================
def _acquire_mutex(action="LOCK", wait=True):
    print(f"[LOCK1] Trying to acquire mutex for {action}...")
    acquired = _lock_mutex.acquire(wait=False, owner="LOCK1")
    if not acquired:
        print(f"[LOCK1] Mutex busy, waiting to acquire {action}...")
        acquired = _lock_mutex.acquire(wait=True, owner="LOCK1")
    print(f"[LOCK1] Mutex acquired for {action}")
    return True



def lock(wait=True):
    _acquire_mutex("LOCK", wait)
    try:
        print("[LOCK1] Locking door")
        _lock_hw()
    finally:
        _lock_mutex.release(owner="LOCK1")
        print("[LOCK1] Mutex released after LOCK")


def unlock(wait=True):
    _acquire_mutex("UNLOCK", wait)
    try:
        print("[LOCK1] Unlocking door")
        _unlock_hw()
    finally:
        _lock_mutex.release(owner="LOCK1")
        print("[LOCK1] Mutex released after UNLOCK")


def lock_for(seconds, wait=True):
 

    def _worker():
        _acquire_mutex("LOCK", wait=True)
        try:
            print(f"[LOCK1] Lock for {seconds}s")
            _lock_hw()
            
            time.sleep(seconds)
        finally:
            _lock_mutex.release(owner="LOCK1")
            print("[LOCK1] Mutex released (lock_for)")

    threading.Thread(target=_worker, daemon=True).start()


def unlock_for(seconds, wait=True):
   

    def _worker():
        _acquire_mutex("UNLOCK", wait=True)
        try:
            print(f"[LOCK1] Unlock for {seconds}s")
            _unlock_hw()
           
            time.sleep(seconds)
        finally:
            _lock_mutex.release(owner="LOCK1")
            print("[LOCK1] Mutex released (unlock_for)")

    threading.Thread(target=_worker, daemon=True).start()


def get_lock1_status():
    return "Lock" if GPIO.input(LOCK1_GPIO) == GPIO.LOW else "Unlock"


def lock1_set(state, seconds=None):

    if seconds:
        if state:
            lock_for(seconds)
        else:
            unlock_for(seconds)
    else:
        if state:
            lock()
        else:
            unlock()
    return True
