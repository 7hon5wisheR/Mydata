#!/usr/bin/env python3
"""
Read temperature & humidity from an SHT20 sensor (I2C) on Raspberry Pi.
Uses NO-HOLD (non-blocking) mode + polling, since HOLD mode often causes
'Remote I/O error' on Raspberry Pi's I2C driver due to poor support for
clock-stretching.
"""

import time
from smbus2 import SMBus, i2c_msg

I2C_BUS = 1
SHT20_ADDR = 0x40

# Commands (NO HOLD MASTER MODE - non-blocking, requires polling)
TRIGGER_TEMP_NOHOLD = 0xF3
TRIGGER_HUMI_NOHOLD = 0xF5

SOFT_RESET = 0xFE

MAX_RETRIES = 20
POLL_DELAY = 0.02  # 20 ms between polling attempts


def crc8_check(data: bytes) -> bool:
    """Verify SHT20's CRC-8 (polynomial x^8+x^5+x^4+1, 0x31)."""
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


def _read_raw(bus: SMBus, trigger_cmd: int) -> int:
    """Send the trigger command, then poll-read 3 bytes (2 data + 1 CRC)."""
    bus.write_byte(SHT20_ADDR, trigger_cmd)

    last_err = None
    for _ in range(MAX_RETRIES):
        time.sleep(POLL_DELAY)
        try:
            read = i2c_msg.read(SHT20_ADDR, 3)
            bus.i2c_rdwr(read)
            data = list(read)
        except OSError as e:
            # Sensor not ready yet (still converting) -> device NACK, retry
            last_err = e
            continue

        if not crc8_check(bytes(data)):
            raise IOError("CRC check failed, SHT20 data is corrupted")

        raw = (data[0] << 8) | data[1]
        raw &= 0xFFFC
        return raw

    raise TimeoutError(f"SHT20 sensor did not respond after {MAX_RETRIES} attempts") from last_err


def read_temperature(bus: SMBus) -> float:
    raw = _read_raw(bus, TRIGGER_TEMP_NOHOLD)
    return -46.85 + (175.72 * raw / 65536.0)


def read_humidity(bus: SMBus) -> float:
    raw = _read_raw(bus, TRIGGER_HUMI_NOHOLD)
    return -6 + (125.0 * raw / 65536.0)


def main():
    with SMBus(I2C_BUS) as bus:
        try:
            bus.write_byte(SHT20_ADDR, SOFT_RESET)
            time.sleep(0.05)
        except OSError:
            pass  # soft reset failure is not fatal

        while True:
            try:
                temp = read_temperature(bus)
                hum = read_humidity(bus)
                print(f"Temperature: {temp:.2f} \u00b0C")
                print(f"Humidity   : {hum:.2f} %RH")
                print("-" * 30)
            except (OSError, IOError, TimeoutError) as e:
                print(f"Failed to read sensor: {e}")

            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
