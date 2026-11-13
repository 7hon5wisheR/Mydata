#!/usr/bin/env python3
"""
light_pwm.py
Manual CLI test tool for the PWM-driven light on the Raspberry Pi.

Usage:
    python3 light_pwm.py 100     # full brightness (100% PWM duty cycle)
    python3 light_pwm.py 50      # dim (50% PWM duty cycle)
    python3 light_pwm.py 0       # off (0% PWM duty cycle)
    python3 light_pwm.py --fade  # fade in/out loop (breathing effect)

============================================================
IMPORTANT - GPIO OWNERSHIP (READ BEFORE RUNNING)
============================================================
sublight.py (used by lightLockControl.py, running as its own
service) is the SOLE OWNER of GPIO pins 16 (ENABLE) and 18 (PWM)
in production. It ALSO reads config.json's "dim" field live every
time the light is turned on.

The old no-argument mode of this script used to poll config.json
forever and drive its OWN GPIO.PWM object on the SAME pins -
running that at the same time as lightLockControl.py caused two
independent PWM signals fighting over the same physical pins,
which is what made the light flicker / blink.

That no-argument watch mode has been REMOVED ENTIRELY (there is no
"else" branch that starts a background loop anymore - running with
no numeric arg and no --fade now just prints usage and exits). This
script is for one-off manual bench testing ONLY, and it refuses to
even start while lightlockcontrol's lock file is present, so it can
never become a second GPIO owner by accident (forgotten background
process, stale cron job, systemd unit, etc.):

    sudo systemctl stop lightlockcontrol   # stop the real owner first
    python3 light_pwm.py 50                # now safe to test manually
    sudo systemctl start lightlockcontrol  # resume normal operation

Do NOT add this script to any systemd unit / autostart / cron job.
Do NOT run it in the background (no &, no nohup) - it is meant to be
run interactively, once, and stopped with Ctrl+C when you're done.
============================================================
"""
import sys
import os
import time
import RPi.GPIO as GPIO

# =====================================================
# CONFIG
# =====================================================
ENABLE_PIN = 16   # driver enable / power gate pin
PWM_PIN = 18      # PWM signal pin (brightness control)
PWM_FREQ_HZ = 1000        # 1 kHz PWM frequency
STEP_DELAY_SEC = 0.015    # delay between each 1% duty-cycle step during fade

LOCK_PATH = "/tmp/lightlockcontrol.lock"


def check_owner_not_running() -> None:
    """
    Refuse to run if lightLockControl.py's lock file is present,
    i.e. the real GPIO owner service is active. This is what
    prevents this script from ever becoming a second GPIO.PWM
    owner on pins 16/18 again (which is what caused the blinking
    light previously - a stray light_pwm.py process running
    alongside lightLockControl.py).
    """
    if os.path.exists(LOCK_PATH):
        sys.exit(
            "[ERROR] lightlockcontrol service appears to be running "
            f"(found {LOCK_PATH}). Stop it first:\n"
            "    sudo systemctl stop lightlockcontrol\n"
            "then re-run this script."
        )


def setup_gpio() -> GPIO.PWM:
    """Configure GPIO pins and return a started PWM instance (starts at 0%)."""
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    GPIO.setup(ENABLE_PIN, GPIO.OUT)
    GPIO.output(ENABLE_PIN, GPIO.HIGH)

    GPIO.setup(PWM_PIN, GPIO.OUT)
    pwm = GPIO.PWM(PWM_PIN, PWM_FREQ_HZ)
    pwm.start(0)
    return pwm


def fade(pwm: GPIO.PWM, start: int, stop: int, step: int) -> None:
    """Fade the PWM duty cycle from `start` to `stop` (inclusive) by `step`."""
    for dc in range(start, stop, step):
        pwm.ChangeDutyCycle(dc)
        time.sleep(STEP_DELAY_SEC)


def parse_brightness(argv):
    """
    Parse a raw PWM brightness percentage from CLI args.
    Returns an int 0-100, or None if no numeric argument was given.
    """
    if len(argv) < 2:
        return None
    if argv[1].startswith("--"):
        return None
    try:
        value = int(argv[1])
    except ValueError:
        sys.exit(f"[ERROR] Brightness must be a number 0-100, got: '{argv[1]}'")
    if not 0 <= value <= 100:
        sys.exit(f"[ERROR] Brightness must be between 0 and 100, got: {value}")
    return value


def main() -> None:
    check_owner_not_running()

    fade_mode = "--fade" in sys.argv
    brightness = parse_brightness(sys.argv)

    if not fade_mode and brightness is None:
        sys.exit(
            "[ERROR] No-argument watch mode has been removed to avoid "
            "conflicting with lightLockControl.py / sublight.py.\n"
            "Usage: python3 light_pwm.py <0-100> | --fade"
        )

    pwm = setup_gpio()
    try:
        if fade_mode:
            print("[INFO] Fade mode requested, running fade loop. Press Ctrl+C to stop.")
            while True:
                fade(pwm, 100, -1, -1)   # fade out: 100% -> 0%
                fade(pwm, 0, 101, 1)     # fade in:  0%   -> 100%
        else:
            pwm.ChangeDutyCycle(brightness)
            print(f"[INFO] Light set to {brightness}% (raw PWM, from CLI arg). Press Ctrl+C to stop.")
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        pwm.stop()
        GPIO.output(ENABLE_PIN, GPIO.LOW)
        GPIO.cleanup()
        print("[INFO] Light off, GPIO cleaned up.")


if __name__ == "__main__":
    main()
