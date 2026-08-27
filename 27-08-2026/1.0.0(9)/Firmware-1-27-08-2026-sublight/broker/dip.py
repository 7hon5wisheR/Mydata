#!/usr/bin/env python3

import RPi.GPIO as GPIO
import subprocess
import time

# ===========================================
# Configuration
# ===========================================

CONNECTION = "netplan-eth0"
INTERFACE  = "eth0"

NETWORK = "192.168.137."
GATEWAY = "192.168.137.1"
DNS     = "192.168.137.1"
PREFIX  = "24"

# GPIO (BCM)
PIN_BIT0 = 22      # GPIO22 (LSB)
PIN_BIT1 = 5       # GPIO5
PIN_BIT2 = 6       # GPIO6
PIN_BIT3 = 26      # GPIO26 (MSB)

# ===========================================
# GPIO Setup
# ===========================================

GPIO.setmode(GPIO.BCM)

GPIO.setup(PIN_BIT0, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_BIT1, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_BIT2, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_BIT3, GPIO.IN, pull_up_down=GPIO.PUD_UP)

last_decimal = -1

# ===========================================
# Get Current IP
# ===========================================

def get_current_ip():
    try:
        result = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", INTERFACE],
            text=True
        )

        # Example:
        # 2: eth0 inet 192.168.137.110/24 brd ...
        return result.split()[3].split("/")[0]

    except Exception:
        return None


# ===========================================
# Change Static IP
# ===========================================

def set_static_ip(decimal):

    desired_ip = NETWORK + str(100 + decimal)
    current_ip = get_current_ip()

    print(f"Current IP : {current_ip}")
    print(f"Desired IP : {desired_ip}")

    # Already correct
    if current_ip == desired_ip:
        print("IP already matches DIP switch. Nothing to do.")
        return

    try:

        print("Updating NetworkManager configuration...")

        subprocess.run([
            "nmcli",
            "connection",
            "modify",
            CONNECTION,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{desired_ip}/{PREFIX}",
            "ipv4.gateway", GATEWAY,
            "ipv4.dns", DNS
        ], check=True)

        print("Bringing connection DOWN...")
        subprocess.run(
            ["nmcli", "connection", "down", CONNECTION],
            check=True
        )

        time.sleep(2)

        print("Bringing connection UP...")
        subprocess.run(
            ["nmcli", "connection", "up", CONNECTION],
            check=True
        )

        print("IP successfully changed.")

    except subprocess.CalledProcessError as e:
        print("ERROR:", e)


# ===========================================
# Main Loop
# ===========================================

try:

    print("Monitoring DIP switches...")
    print("0000 = Disabled")
    print("0001 = 192.168.137.101")
    print("1111 = 192.168.137.115")

    while True:

        # Pull-up resistors:
        # OFF = 1
        # ON  = 0
        # Invert so ON becomes 1

        b0 = 1 - GPIO.input(PIN_BIT0)
        b1 = 1 - GPIO.input(PIN_BIT1)
        b2 = 1 - GPIO.input(PIN_BIT2)
        b3 = 1 - GPIO.input(PIN_BIT3)

        decimal = (b3 << 3) | (b2 << 2) | (b1 << 1) | b0

        if decimal != last_decimal:

            last_decimal = decimal

            binary = f"{b3}{b2}{b1}{b0}"

            print("\n===================================")
            print(f"DIP      : {binary}")
            print(f"Decimal  : {decimal}")

            if decimal == 0:

                print("Status   : DISABLED")
                print("No IP change.")

            else:

                desired_ip = NETWORK + str(100 + decimal)

                print(f"Target IP: {desired_ip}")

                set_static_ip(decimal)

            print("===================================")

        time.sleep(0.2)

except KeyboardInterrupt:

    print("\nStopping...")

finally:

    GPIO.cleanup()
