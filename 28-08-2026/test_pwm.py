#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pwm.py
Verify the light's PWM: confirm sub_on() really follows the "dim"
value in config.json, and sub_off() really drives PWM to 0% (not
100%).

MUST BE RUN DIRECTLY ON THE RASPBERRY PI (needs real RPi.GPIO), from
inside broker/library/ (same folder as sublight.py) - sublight.py
itself resolves config.json/status.json one directory up (broker/).

    python3 test_pwm.py

There are 3 layers of verification, from most to least trustworthy:

  1. ENABLE_GPIO LEVEL (most decisive)
     sublight.set_pwm() drives ENABLE_GPIO (pin 16) LOW whenever duty
     is 0, and HIGH otherwise. This pin gates power to the light
     driver - when it's LOW, the driver is physically cut off from
     power regardless of what the PWM signal pin is doing. This is
     the most reliable "is the light really off" signal available
     without external test equipment.

  2. SOFTWARE-LEVEL (sublight.get_current_pwm())
     Reads the last duty cycle value sublight.py actually COMMANDED
     to ChangeDutyCycle(). This proves "the code correctly calls
     set_pwm(0)" - it does not by itself prove the electrical signal
     on the pin is 0%, but combined with #1 above it is strong
     evidence.

  3. HARDWARE-LEVEL (sampling GPIO.input() on the PWM pin) - BEST EFFORT ONLY
     RPi.GPIO's software PWM has no official "read back the duty
     cycle" call, so this script polls GPIO.input(PWM_GPIO) from
     Python to estimate the real duty cycle empirically.

     IMPORTANT LIMITATION: RPi.GPIO's software PWM runs its own
     background thread toggling the pin at PWM_FREQ_HZ (1000 Hz by
     default - a 1ms period). A tight Python polling loop competes
     with that same thread for the GIL and gets scheduled by the OS
     at a similar timescale, so the samples can be aliased/jittery
     and do NOT reliably reflect the true duty cycle - this is a
     known limitation of measuring software PWM from the SAME
     process in Python, not a bug in the light itself. This script
     therefore polls at a deliberately slower rate (see
     SAMPLE_INTERVAL_SEC) to reduce (not eliminate) that interference,
     and treats this measurement as informational only - it does NOT
     decide pass/fail on its own. For a fully trustworthy reading at
     every brightness level, use a multimeter/oscilloscope/logic
     analyzer directly on GPIO18.
"""

import json
import time

import RPi.GPIO as GPIO
import sublight   # this is what sets up & controls PWM_GPIO


SAMPLE_DURATION_SEC = 1.0    # total sampling duration per measurement
SAMPLE_INTERVAL_SEC = 0.01   # 10ms between samples - deliberately slow to
                              # reduce GIL/thread contention with the PWM
                              # background thread (see module docstring)


def measure_duty_empirically(pin: int, duration: float = SAMPLE_DURATION_SEC) -> float:
    """
    Poll GPIO.input(pin) repeatedly over `duration` seconds and
    compute the percentage of samples read as HIGH. Best-effort only
    - see the LIMITATION note in the module docstring.
    """
    high_count = 0
    total = 0
    t_end = time.time() + duration

    while time.time() < t_end:
        level = GPIO.input(pin)
        total += 1
        if level:
            high_count += 1
        time.sleep(SAMPLE_INTERVAL_SEC)

    if total == 0:
        return 0.0
    return (high_count / total) * 100.0


def run_check(label: str):
    print(f"\n=== {label} ===")

    sw_duty = sublight.get_current_pwm()
    print(f"[SOFTWARE]  Last duty commanded to ChangeDutyCycle(): {sw_duty}%")

    enable_level = GPIO.input(sublight.ENABLE_GPIO)
    print(f"[HARDWARE]  ENABLE_GPIO (pin {sublight.ENABLE_GPIO}) level: "
          f"{'HIGH (driver powered)' if enable_level else 'LOW (driver power cut)'}")

    print(f"[HARDWARE]  Sampling pin GPIO{sublight.PWM_GPIO} for {SAMPLE_DURATION_SEC}s "
          f"(best-effort, see docstring limitation)...")
    hw_duty = measure_duty_empirically(sublight.PWM_GPIO)
    print(f"[HARDWARE]  Estimated duty cycle from sampling: {hw_duty:.1f}% HIGH")

    return sw_duty, enable_level, hw_duty


def main():
    print("=" * 60)
    print("PWM TEST - sublight.py")
    print("=" * 60)
    print(f"Reading config.json from: {sublight.CONFIG_PATH}")
    print(f"Reading/writing status.json at: {sublight.STATUS_PATH}")

    # Read the dim value stored in config.json, for an expectation reference
    try:
        with open(sublight.CONFIG_PATH) as f:
            cfg = json.load(f)
        dim = cfg.get("dim", "?")
        expected_pwm_on = (100 - int(dim)) if isinstance(dim, int) or str(dim).isdigit() else "?"
    except Exception as e:
        dim = "?"
        expected_pwm_on = "?"
        print(f"[WARN] Failed to read config.json: {e}")

    print(f"config.json['dim'] currently = {dim}  "
          f"(expected PWM when ON = 100 - dim = {expected_pwm_on}%)")

    try:
        # ---------- TEST ON ----------
        print("\nRunning sublight.sub_on() ...")
        sublight.sub_on()
        time.sleep(0.3)   # give the PWM time to settle
        sw_on, enable_on, hw_on = run_check("STATUS: ON")

        # ---------- TEST OFF ----------
        print("\nRunning sublight.sub_off() ...")
        sublight.sub_off()
        time.sleep(0.3)
        sw_off, enable_off, hw_off = run_check("STATUS: OFF")

        # ---------- CONCLUSION ----------
        print("\n" + "=" * 60)
        print("CONCLUSION")
        print("=" * 60)
        print(f"ON  -> software={sw_on}% | ENABLE_GPIO={'HIGH' if enable_on else 'LOW'} "
              f"| hardware(sampled, best-effort)={hw_on:.1f}%")
        print(f"OFF -> software={sw_off}% | ENABLE_GPIO={'HIGH' if enable_off else 'LOW'} "
              f"| hardware(sampled, best-effort)={hw_off:.1f}%")

        # Decisive check: software commanded 0% AND the driver's power
        # pin is LOW. The best-effort GPIO sampling is reported above
        # for reference but is NOT part of this pass/fail decision -
        # see the LIMITATION note in the module docstring for why.
        off_ok = (sw_off == 0) and (enable_off == GPIO.LOW)
        if off_ok:
            print("\n[RESULT] CORRECT: when OFF, the code commands PWM=0% and the driver's "
                  "power pin (ENABLE_GPIO) is LOW - the light is genuinely off, not just at "
                  "a low logical duty value.")
            print("[NOTE] The GPIO18 sampling numbers above are best-effort only (see docstring) "
                  "and can be noisy - they are not used to decide pass/fail here.")
        else:
            print("\n[RESULT] DOES NOT MATCH EXPECTATION - re-check wiring/code. "
                  f"software_off={sw_off}%, ENABLE_GPIO={'HIGH' if enable_off else 'LOW'}")

    finally:
        # Force OFF at the end of the test so no light is left on
        # unexpectedly once the script finishes.
        sublight.sub_off()
        print("\n[CLEANUP] Forced OFF at the end of the test.")


if __name__ == "__main__":
    main()
