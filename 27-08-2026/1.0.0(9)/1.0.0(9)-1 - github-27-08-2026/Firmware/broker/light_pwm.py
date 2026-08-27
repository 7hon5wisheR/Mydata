#!/usr/bin/env python3
"""
light_pwm.py
Control a PWM-driven light on the Raspberry Pi via RPi.GPIO.

Usage:
    python3 light_pwm.py 100     # full brightness (100% PWM duty cycle)
    python3 light_pwm.py 50      # dim (50% PWM duty cycle)
    python3 light_pwm.py 0       # off (0% PWM duty cycle)
    python3 light_pwm.py         # no argument -> LIVE-watch "dim" in config.json
    python3 light_pwm.py --fade  # fade in/out loop (breathing effect)

CONFIG.JSON "dim" FIELD
------------------------
config.json stores brightness as "dim": 0-100 (step 10), where the
scale is INVERTED relative to raw PWM duty cycle:
    dim = 0    -> brightest  -> PWM duty cycle = 100%
    dim = 100  -> dimmest    -> PWM duty cycle = 0%
    dim = 30   -> PWM duty cycle = 70%   (pwm = 100 - dim)

This matches apps.py: light.py's _get_configured_brightness() is
expected to re-read config.json's "dim" value live (no reboot needed),
so when this script is run with no CLI argument it now WATCHES
config.json continuously (polling every CONFIG_POLL_SEC seconds) and
applies any new "dim" value as soon as it changes on disk - no
restart required.

Once set, the script keeps running to hold the PWM signal
(software PWM needs the process alive). Press Ctrl+C to stop
and turn the light off cleanly.
"""
import sys
import os
import json
import time
import RPi.GPIO as GPIO

# =====================================================
# CONFIG
# =====================================================
ENABLE_PIN = 16   # driver enable / power gate pin
PWM_PIN = 18      # PWM signal pin (brightness control)
PWM_FREQ_HZ = 1000        # 1 kHz PWM frequency
STEP_DELAY_SEC = 0.015    # delay between each 1% duty-cycle step during fade
CONFIG_POLL_SEC = 0.5     # how often to check config.json for changes (no-arg mode)

# config.json is expected to live in the same directory as this script
# (same directory as apps.py).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DIM_MIN = 0
DIM_MAX = 100


def setup_gpio() -> GPIO.PWM:
    """Configure GPIO pins and return a started PWM instance (starts at 0%)."""
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    # Enable output (driver / power gate)
    GPIO.setup(ENABLE_PIN, GPIO.OUT)
    GPIO.output(ENABLE_PIN, GPIO.HIGH)

    # PWM output
    GPIO.setup(PWM_PIN, GPIO.OUT)
    pwm = GPIO.PWM(PWM_PIN, PWM_FREQ_HZ)
    pwm.start(0)
    return pwm


def fade(pwm: GPIO.PWM, start: int, stop: int, step: int) -> None:
    """Fade the PWM duty cycle from `start` to `stop` (inclusive) by `step`."""
    for dc in range(start, stop, step):
        pwm.ChangeDutyCycle(dc)
        time.sleep(STEP_DELAY_SEC)


def read_dim_from_config() -> int:
    """
    Read the "dim" field from config.json.

    Returns an int 0-100 (clamped), defaulting to 0 (brightest) if the
    file is missing, unreadable, or the field is missing/invalid.
    """
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        dim = int(cfg.get("dim", 0))
    except Exception as e:
        print(f"[WARN] Could not read 'dim' from {CONFIG_PATH}: {e}. Defaulting to 0 (brightest).")
        return 0

    if dim < DIM_MIN:
        dim = DIM_MIN
    elif dim > DIM_MAX:
        dim = DIM_MAX
    return dim


def dim_to_pwm(dim: int) -> int:
    """
    Convert config.json's "dim" value to a raw PWM duty cycle.

    dim=0 (brightest) -> pwm=100
    dim=100 (dimmest)  -> pwm=0
    """
    return DIM_MAX - dim


def watch_config_and_apply(pwm: GPIO.PWM) -> None:
    """
    No-arg mode: keep re-reading config.json's "dim" value forever and
    push any change to the PWM output live, without restarting the
    script.

    Change detection is based on the file's mtime (cheap - avoids
    re-parsing JSON every poll when nothing changed), but the dim
    value itself is also compared directly as a fallback in case the
    filesystem doesn't update mtimes finely enough (e.g. quick
    successive writes within the same tick).
    """
    last_mtime = None
    last_dim = None

    while True:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = None

        if mtime != last_mtime or last_dim is None:
            dim = read_dim_from_config()
            if dim != last_dim:
                brightness = dim_to_pwm(dim)
                pwm.ChangeDutyCycle(brightness)
                print(f"[INFO] Light updated to {brightness}% PWM (config dim={dim}).")
                last_dim = dim
            last_mtime = mtime

        time.sleep(CONFIG_POLL_SEC)


def parse_brightness(argv) -> int | None:
    """
    Parse a raw PWM brightness percentage from CLI args (NOT inverted -
    this is the literal PWM duty cycle, useful for manual testing).

    Returns an int 0-100, or None if no numeric argument was given.
    Exits with an error message if the argument is invalid.
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
    fade_mode = "--fade" in sys.argv
    brightness = parse_brightness(sys.argv)

    pwm = setup_gpio()
    try:
        if fade_mode:
            # Explicit --fade flag -> continuous fade in/out (breathing effect)
            print("[INFO] Fade mode requested, running fade loop. Press Ctrl+C to stop.")
            while True:
                fade(pwm, 100, -1, -1)   # fade out: 100% -> 0%
                fade(pwm, 0, 101, 1)     # fade in:  0%   -> 100%

        elif brightness is not None:
            # Explicit numeric CLI arg -> raw PWM duty cycle, set once and hold
            pwm.ChangeDutyCycle(brightness)
            print(f"[INFO] Light set to {brightness}% (raw PWM, from CLI arg). Press Ctrl+C to stop.")
            while True:
                time.sleep(1)

        else:
            # No argument -> LIVE-watch "dim" in config.json (inverted scale)
            # and keep applying it as it changes, no restart needed.
            print(f"[INFO] Watching {CONFIG_PATH} for 'dim' changes every {CONFIG_POLL_SEC}s. Press Ctrl+C to stop.")
            watch_config_and_apply(pwm)

    except KeyboardInterrupt:
        pass
    finally:
        pwm.stop()
        GPIO.output(ENABLE_PIN, GPIO.LOW)
        GPIO.cleanup()
        print("[INFO] Light off, GPIO cleaned up.")


if __name__ == "__main__":
    main()
