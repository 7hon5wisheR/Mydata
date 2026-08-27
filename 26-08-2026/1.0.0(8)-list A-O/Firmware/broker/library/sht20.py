# sht20.py
# -*- coding: utf-8 -*-
__version__ = "0.0.0.1.2 - SHT20 I2C shared driver (temperature + humidity)"

# =====================================================================================================================================
#      SHT20
#      Name                       : SHT20 I2C DRIVER (shared)
#      Version                    : 0.0.0.1.2
#      Date Created               : 27-07-2026
#      Author                     : Saifuddin
#      Notes                      : Replaces the simulation in temperature.py / humidity.py.
#                                   One shared I2C driver + one background thread that
#                                   reads the SHT20 sensor periodically and caches the
#                                   values, so temperature.py and humidity.py both read
#                                   from this cache instead of fighting over the I2C bus.
# ======================================================================================================================================

import threading
import time
from smbus2 import SMBus

# =====================================================
# CONFIG
# =====================================================
I2C_BUS    = 1
SHT20_ADDR = 0x40

TRIGGER_TEMP_HOLD = 0xE3
TRIGGER_HUMI_HOLD = 0xE5

READ_INTERVAL = 5  # seconds between background readings (same cadence as the old simulation)


# =====================================================
# LOW LEVEL READ
# =====================================================
def _read_temperature(bus: SMBus) -> float:
    bus.write_byte(SHT20_ADDR, TRIGGER_TEMP_HOLD)
    time.sleep(0.1)
    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_TEMP_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC
    return -46.85 + (175.72 * raw / 65536.0)


def _read_humidity(bus: SMBus) -> float:
    bus.write_byte(SHT20_ADDR, TRIGGER_HUMI_HOLD)
    time.sleep(0.1)
    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_HUMI_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC
    return -6 + (125.0 * raw / 65536.0)


# =====================================================
# CACHE STATE
# =====================================================
_cached_temperature = 0.0
_cached_humidity     = 0.0
_cache_lock          = threading.Lock()
_last_error          = None


def _update_loop():
    """
    Background thread: open the I2C bus, read temperature + humidity in
    one pass, store them in the cache. If the sensor read fails (cable
    unplugged / bus busy), the previous value is kept (not reset to 0)
    so downstream consumers (status.py, etc.) never get a bogus reading.
    """
    global _cached_temperature, _cached_humidity, _last_error

    while True:
        try:
            with SMBus(I2C_BUS) as bus:
                t = round(_read_temperature(bus), 2)
                h = round(_read_humidity(bus), 2)

            with _cache_lock:
                _cached_temperature = t
                _cached_humidity    = h
                _last_error         = None

        except OSError as e:
            with _cache_lock:
                _last_error = str(e)
            print(f"[SHT20] Read failed: {e}")

        time.sleep(READ_INTERVAL)


_update_thread = threading.Thread(target=_update_loop, daemon=True)
_update_thread.start()


# =====================================================
# PUBLIC API (used by temperature.py / humidity.py)
# =====================================================
def get_temperature() -> float:
    with _cache_lock:
        return _cached_temperature


def get_humidity() -> float:
    with _cache_lock:
        return _cached_humidity


def get_last_error():
    """Last I2C error message (None if the last read succeeded)."""
    with _cache_lock:
        return _last_error
