#!/usr/bin/env python3
import subprocess
import os
import sys
import signal
import time
import platform

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM   = platform.system()

process_rabbit = None
process_run    = None
process_radar  = None
process_light  = None
process_switch = None

# ----------------- SETCAP -----------------
def ensure_cap():
    targets = [
        "/usr/bin/python3.11",
        "/usr/bin/python3",
    ]

    # Find available Python binary
    python_bin = None
    for t in targets:
        if os.path.exists(t):
            python_bin = t
            break

    if not python_bin:
        try:
            python_bin = subprocess.check_output(
                ["readlink", "-f", sys.executable], text=True
            ).strip()
        except Exception:
            print("[SETCAP] Cannot find python binary!")
            return False

    # Check if capability is already set
    try:
        result = subprocess.run(
            ["getcap", python_bin], capture_output=True, text=True
        )
        if "cap_net_bind_service" in result.stdout:
            print(f"[SETCAP] Already set on {python_bin}")
            return True
    except Exception:
        pass

    # Auto setup sudoers so setcap can run without password
    sudoers_file = "/etc/sudoers.d/alice-setcap"
    current_user = os.environ.get("USER", "alice")
    sudoers_line = f"{current_user} ALL=(ALL) NOPASSWD: /usr/sbin/setcap\n"
    try:
        if not os.path.exists(sudoers_file):
            print("[SETCAP] Setting up sudoers for setcap...")
            ret = subprocess.run(
                ["sudo", "-n", "tee", sudoers_file],
                input=sudoers_line,
                capture_output=True, text=True
            )
            if ret.returncode == 0:
                subprocess.run(
                    ["sudo", "-n", "chmod", "440", sudoers_file],
                    capture_output=True
                )
                print("[SETCAP] Sudoers configured.")
            else:
                print(f"[SETCAP] Sudoers setup failed: {ret.stderr}")
    except Exception as e:
        print(f"[SETCAP] Sudoers error: {e}")

    # Set capability
    print(f"[SETCAP] Setting cap_net_bind_service on {python_bin}...")
    try:
        # Try sudo -n first (no password prompt)
        ret = subprocess.run(
            ["sudo", "-n", "setcap", "cap_net_bind_service=+ep", python_bin],
            capture_output=True, text=True
        )
        if ret.returncode == 0:
            print("[SETCAP] Success.")
            return True

        # If failed, retry without -n
        print(f"[SETCAP] sudo -n failed, trying without -n...")
        ret2 = subprocess.run(
            ["sudo", "setcap", "cap_net_bind_service=+ep", python_bin],
            capture_output=True, text=True
        )
        if ret2.returncode == 0:
            print("[SETCAP] Success.")
            return True
        else:
            print(f"[SETCAP] Failed: {ret2.stderr}")
            return False
    except Exception as e:
        print(f"[SETCAP] Error: {e}")
        return False

# ----------------- CLEANUP -----------------
def cleanup_processes(*args):
    global process_rabbit, process_run, process_radar, process_light, process_switch
    print("\n[send.py] Stopping all processes...")
    for name, proc in [
        ("apps.py",      process_rabbit),
        ("run",          process_run),
        ("radar",        process_radar),
        ("light",        process_light),
        ("switch",       process_switch),
    ]:
        if proc and proc.poll() is None:
            proc.terminate()
            print(f"{name} terminated")
    sys.exit(0)

signal.signal(signal.SIGINT,  cleanup_processes)
signal.signal(signal.SIGTERM, cleanup_processes)

# ----------------- START MODULE -----------------
def start_module(so_name, py_name, label):
    so_path = os.path.join(BASE_DIR, so_name)
    py_path = os.path.join(BASE_DIR, py_name)
    module  = so_name.replace(".so", "")

    if os.path.exists(so_path):
        cmd = [
            sys.executable, "-c",
            (
                f"import sys; sys.path.insert(0, {repr(BASE_DIR)}); "
                f"import {module}; {module}.main()"
            )
        ]
        used = so_name
    elif os.path.exists(py_path):
        cmd  = [sys.executable, py_path]
        used = f"{py_name} (fallback)"
    else:
        print(f"[ERROR] {label}: not found '{so_name}' or '{py_name}'!")
        return None, ""

    proc = subprocess.Popen(cmd)
    return proc, used

# ----------------- START APPS.PY -----------------
def start_apps():
    py_path = os.path.join(BASE_DIR, "apps.py")
    if not os.path.exists(py_path):
        print("[ERROR] apps.py not found!")
        return None
    proc = subprocess.Popen([sys.executable, py_path])
    print("apps.py started (port 80)")
    return proc

# ----------------- MAIN -----------------
if __name__ == "__main__":
    # Ensure Python has permission to bind port 80
    ensure_cap()

    try:
        process_rabbit = start_apps()

        process_run, label = start_module("run.so", "run.py", "run")
        if process_run:
            print(f"{label} started")

        process_radar, label = start_module("radarScanControl.so", "radarScanControl.py", "radar")
        if process_radar:
            print(f"{label} started")

        process_light, label = start_module("lightLockControl.so", "lightLockControl.py", "light")
        if process_light:
            print(f"{label} started")

        # NOTE: light_pwm.py/.so is intentionally NOT started here.
        # It is a manual, one-off bench-test CLI tool - lightLockControl.py
        # (via sublight.py) is the sole GPIO/PWM owner for pins 16/18 in
        # production, including live dim changes (sublight.start_dim_watcher()).
        # Auto-starting light_pwm.py here previously created a second,
        # permanently-running GPIO.PWM owner on the same pins, which fought
        # lightLockControl.py's signal and caused the light to flicker/blink.
        # Run light_pwm.py manually for bench testing only, after stopping
        # lightlockcontrol first - see light_pwm.py's own docstring.

        process_switch, label = start_module("switchStatus.so", "switchStatus.py", "switch")
        if process_switch:
            print(f"{label} started")

    except Exception as e:
        print(f"[ERROR] starting processes: {e}")
        sys.exit(1)

    print("[send.py] All processes running...")

    # ----------------- WATCHDOG LOOP -----------------
    while True:
        time.sleep(30)
        for name, proc in [
            ("apps.py",   process_rabbit),
            ("run",       process_run),
            ("radar",     process_radar),
            ("light",     process_light),
            ("switch",    process_switch),
        ]:
            if proc and proc.poll() is not None:
                print(f"[WATCHDOG] {name} stopped! Restarting service...")
                sys.exit(1)
