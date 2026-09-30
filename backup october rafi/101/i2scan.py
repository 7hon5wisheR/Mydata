#!/usr/bin/env python3
"""Scan I2C bus 1 for responding devices (alternative to i2cdetect)."""

from smbus2 import SMBus

I2C_BUS = 1

print("     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f")
with SMBus(I2C_BUS) as bus:
    for row in range(0, 128, 16):
        line = f"{row:02x}: "
        for col in range(16):
            addr = row + col
            if addr < 0x03 or addr > 0x77:
                line += "   "
                continue
            try:
                bus.write_quick(addr)
                line += f"{addr:02x} "
            except OSError:
                line += "-- "
        print(line)
