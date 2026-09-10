# sht20.py
# -*- coding: utf-8 -*-
__version__ = "1.1.0(1) - SHT20 I2C shared driver (temperature + humidity)"
# =====================================================================================================================================
#      SHT20
#      Name                       : SHT20 I2C DRIVER (shared)
#      Version                    : 1.1.0(1)
#      Date Created               : 27-07-2026
#      Updated                    : 06-09-2026
#      Author                     : Saifuddin
#      Notes                      : Replaces the simulation in temperature.py / humidity.py.
#                                   One shared I2C driver + one background thread that
#                                   reads the SHT20 sensor periodically and caches the
#                                   values, so temperature.py and humidity.py both read
#                                   from this cache instead of fighting over the I2C bus.
#                                 : v1.1.0(1) - fixed double-trigger bug (command byte
#                                   was being sent twice per read), added CRC-8 check,
#                                   cache now starts as None (not 0.0) so consumers can
#                                   tell "not read yet" apart from a real 0.0 reading,
#                                   broadened exception handling so the background
#                                   thread cannot die silently, reduced log spam by
#                                   only logging on status transitions.
# ======================================================================================================================================
import threading
import time
from smbus2 import SMBus, i2c_msg

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
def _crc8_ok(data):
    """Verify SHT20's CRC-8 (polynomial x^8+x^5+x^4+1, 0x31) on the 2 data bytes."""
    crc = 0
    for byte in data[:2]:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x131
            else:
                crc <<= 1
        crc &= 0xFF
    return crc == data[2]


def _read_raw(bus, trigger_cmd):
    """
    Single I2C transaction: send the trigger command, then read 3 bytes
    (2 data + 1 CRC) in the SAME transaction. This is the correct way to
    use SHT20's HOLD MASTER mode - the sensor clock-stretches the bus
    while it converts, so no separate sleep/poll is needed, and the
    command byte is only sent ONCE (the old code sent it twice, which
    caused re-triggering and intermittent I/O errors).
    """
    write = i2c_msg.write(SHT20_ADDR, [trigger_cmd])
    read  = i2c_msg.read(SHT20_ADDR, 3)
    bus.i2c_rdwr(write, read)
    data = list(read)

    if not _crc8_ok(data):
        raise IOError("CRC check failed, SHT20 data is corrupted")

    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC
    return raw


def _read_temperature(bus):
    raw = _read_raw(bus, TRIGGER_TEMP_HOLD)
    return -46.85 + (175.72 * raw / 65536.0)


def _read_humidity(bus):
    raw = _read_raw(bus, TRIGGER_HUMI_HOLD)
    return -6 + (125.0 * raw / 65536.0)

# =====================================================
# CACHE STATE
# =====================================================
_cached_temperature = None   # None = not read successfully yet
_cached_humidity     = None  # None = not read successfully yet
_cache_lock          = threading.Lock()
_last_error          = None
_was_ok              = None  # tracks previous read outcome, for transition-only logging


def _update_loop():
    """
    Background thread: open the I2C bus, read temperature + humidity in
    one pass, store them in the cache. If the sensor read fails (cable
    unplugged / bus busy / sensor not present), the previous cached
    value is kept as-is (not reset to None/0) so downstream consumers
    (status.py, etc.) never get a bogus reading - they just keep the
    last known good value until reading succeeds again.
    """
    global _cached_temperature, _cached_humidity, _last_error, _was_ok

    while True:
        try:
            with SMBus(I2C_BUS) as bus:
                t = round(_read_temperature(bus), 2)
                h = round(_read_humidity(bus), 2)

            with _cache_lock:
                _cached_temperature = t
                _cached_humidity    = h
                _last_error         = None

            if _was_ok is False:
                print("[SHT20] Sensor recovered, readings resumed")
            _was_ok = True

        except Exception as e:
            # Catch ANY exception here (not just OSError) so the daemon
            # thread can never die silently and stop updating the cache.
            with _cache_lock:
                _last_error = str(e)

            if _was_ok is not False:
                print("[SHT20] Read failed: %s (further identical failures are suppressed)" % e)
            _was_ok = False

        time.sleep(READ_INTERVAL)


_update_thread = threading.Thread(target=_update_loop, daemon=True)
_update_thread.start()

# =====================================================
# PUBLIC API (used by temperature.py / humidity.py)
# =====================================================
def get_temperature():
    """
    Returns:
        float: last known temperature in Celsius, or
        None: if the sensor has never been read successfully.
    """
    with _cache_lock:
        return _cached_temperature


def get_humidity():
    """
    Returns:
        float: last known humidity in %RH, or
        None: if the sensor has never been read successfully.
    """
    with _cache_lock:
        return _cached_humidity


def get_last_error():
    """Last I2C error message (None if the last read succeeded)."""
    with _cache_lock:
        return _last_error


def is_available():
    """True if at least one successful reading has been cached."""
    with _cache_lock:
        return _cached_temperature is not None and _cached_humidity is not None
