#!/usr/bin/env python3
"""
sht20.py

Simple standalone reader for the SHT20 temperature/humidity sensor
over I2C. Prints temperature (°C) and humidity (%RH) every 2 seconds.
"""

import time
from smbus2 import SMBus

# =====================================================
# CONFIG
# =====================================================
I2C_BUS = 1
SHT20_ADDR = 0x40

# Commands
TRIGGER_TEMP_HOLD = 0xE3
TRIGGER_HUMI_HOLD = 0xE5

READ_INTERVAL = 2  # seconds between readings


# =====================================================
# LOW LEVEL READ
# =====================================================
def read_temperature(bus: SMBus) -> float:
    """Trigger a temperature measurement and return the value in Celsius."""
    bus.write_byte(SHT20_ADDR, TRIGGER_TEMP_HOLD)
    time.sleep(0.1)

    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_TEMP_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC

    return -46.85 + (175.72 * raw / 65536.0)


def read_humidity(bus: SMBus) -> float:
    """Trigger a humidity measurement and return the value in %RH."""
    bus.write_byte(SHT20_ADDR, TRIGGER_HUMI_HOLD)
    time.sleep(0.1)

    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_HUMI_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC

    return -6 + (125.0 * raw / 65536.0)


# =====================================================
# MAIN LOOP
# =====================================================
def main() -> None:
    with SMBus(I2C_BUS) as bus:
        while True:
            try:
                temp = read_temperature(bus)
                hum = read_humidity(bus)

                print(f"Temperature: {temp:.2f} °C")
                print(f"Humidity   : {hum:.2f} %RH")
                print("-" * 30)

            except OSError as e:
                # I2C read failed (sensor disconnected / bus busy / etc.)
                print(f"[ERROR] Failed to read SHT20: {e}")

            time.sleep(READ_INTERVAL)


if __name__ == "__main__":
    main()
