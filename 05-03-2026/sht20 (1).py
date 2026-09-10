#!/usr/bin/env python3
"""
Read temperature & humidity from an SHT20 sensor (I2C) on Raspberry Pi.

Before starting the read loop, this script first checks whether the
sensor is actually present on the I2C bus at SHT20_ADDR. If it's not
detected, the script prints a clear message and exits instead of
looping with repeated I/O errors.
"""

import sys
import time
from smbus2 import SMBus

I2C_BUS = 1
SHT20_ADDR = 0x40

TRIGGER_TEMP_HOLD = 0xE3
TRIGGER_HUMI_HOLD = 0xE5


def is_device_present(bus: SMBus, addr: int) -> bool:
    """Check if a device responds at the given I2C address."""
    try:
        bus.write_quick(addr)
        return True
    except OSError:
        return False


def read_temperature(bus):
    bus.write_byte(SHT20_ADDR, TRIGGER_TEMP_HOLD)
    time.sleep(0.1)
    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_TEMP_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC
    temperature = -46.85 + (175.72 * raw / 65536.0)
    return temperature


def read_humidity(bus):
    bus.write_byte(SHT20_ADDR, TRIGGER_HUMI_HOLD)
    time.sleep(0.1)
    data = bus.read_i2c_block_data(SHT20_ADDR, TRIGGER_HUMI_HOLD, 2)
    raw = (data[0] << 8) | data[1]
    raw &= 0xFFFC
    humidity = -6 + (125.0 * raw / 65536.0)
    return humidity


def main():
    with SMBus(I2C_BUS) as bus:
        if not is_device_present(bus, SHT20_ADDR):
            print(f"[NOT FOUND] No device responding at address 0x{SHT20_ADDR:02x} on I2C bus {I2C_BUS}.")
            print("The sensor is likely not connected yet.")
            sys.exit(1)

        print(f"[FOUND] Device detected at address 0x{SHT20_ADDR:02x}. Starting readings...\n")

        while True:
            try:
                temp = read_temperature(bus)
                hum = read_humidity(bus)
                print(f"Temperature: {temp:.2f} °C")
                print(f"Humidity   : {hum:.2f} %RH")
                print("-" * 30)
            except OSError as e:
                print(f"Failed to read sensor: {e}")

            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
