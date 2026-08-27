#!/usr/bin/env python3
__version__ = "1.0.0(9) - light pwm"
"""
light_pwm.py

Sets a PWM-controlled light brightness on the Raspberry Pi via RPi.GPIO,
using the "pwm" value from config.json (0-100).

File layout assumed:
    project/
      config.json          <- read from here (one level above this file)
      library/
        light_pwm.py        <- this file

Runs until interrupted (Ctrl+C), holding the configured brightness
(software PWM needs the process alive to keep outputting the signal).
"""

# =====================================================================================================================================
#      LIGHT PWM
#      Name                       :  LIGHT PWM
#      Version                    : 1.0.0(9)
#      Date Created               : 27 -07-2026
#      Updated                    : 27 -07-2026
#      Author                     : Saifuddin
# ======================================================================================================================================

import json
import os
import time
import RPi.GPIO as GPIO

# =====================================================
# CONFIG
# =====================================================
ENABLE_PIN = 16   # driver enable / power gate pin
PWM_PIN = 18      # PWM signal pin (brightness control)

PWM_FREQ_HZ = 1000  # 1 kHz PWM frequency

# config.json sits one level above the library/ folder this file lives in
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config.json"
)


# =====================================================
# CONFIG LOADING
# =====================================================
def load_config() -> dict:
    """Load and parse config.json. Raises if the file is missing/invalid."""
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def get_pwm_brightness(cfg: dict) -> int:
    """
    Read and validate the "pwm" brightness value (0-100) from config.
    Fails loud on missing/invalid values instead of silently guessing.
    """
    value = cfg.get("pwm")

    if value is None:
        raise ValueError("config.json is missing the 'pwm' key")

    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"config.json 'pwm' must be a number 0-100, got: {value!r}")

    if not 0 <= value <= 100:
        raise ValueError(f"config.json 'pwm' must be between 0 and 100, got: {value}")

    return value


# =====================================================
# GPIO / PWM
# =====================================================
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


def main() -> None:
    cfg = load_config()
    brightness = get_pwm_brightness(cfg)

    pwm = setup_gpio()

    try:
        pwm.ChangeDutyCycle(brightness)
        print(f"[INFO] Light set to {brightness}% (from config.json). Press Ctrl+C to stop.")

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
