#!/usr/bin/env python3
import subprocess
import os
import sys
import signal
import time
import platform

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM = platform.system()

process_rabbit = None
process_run = None
process_radar = None
process_light = None
process_switch = None

# ======================================================
# AUTO SETCAP 
# ======================================================
def ensure_cap():
    python_bin = subprocess.check_output(
        ["readlink", "-f", sys.executable], text=True
    ).strip()

    result = subprocess.run(
        ["getcap", python_bin], capture_output=True, text=True
    )
    if "cap_net_bind_service" in result.stdout:
        print(f"[SETCAP] Already set on {python_bin}")
        return

    print(f"[SETCAP] Setting cap_net_bind_service on {python_bin}...")
    ret = subprocess.run(
        ["sudo", "setcap", "cap_net_bind_service=+ep", python_bin]
    )
    if ret.returncode == 0:
        print("[SETCAP] Success.")
    else:
        print("[SETCAP] Failed! apps.py failed to bind port 80.")

# ======================================================

def cleanup_processes(*args):
    global process_rabbit, process_run, process_radar, process_light, process_switch
    print("\nStopping send.py...")
    if process_rabbit and process_rabbit.poll() is None:
        process_rabbit.terminate()
        print("apps.py terminated")
    if process_run and process_run.poll() is None:
        process_run.terminate()
        print("run.py terminated")
    if process_radar and process_radar.poll() is None:
        process_radar.terminate()
        print("radarScanControl.py terminated")
    if process_light and process_light.poll() is None:
        process_light.terminate()
        print("lightLockControl.py terminated")
    if process_switch and process_switch.poll() is None:
        process_switch.terminate()
        print("switchStatus.py terminated")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup_processes)
signal.signal(signal.SIGTERM, cleanup_processes)

if __name__ == "__main__":
    ensure_cap()  

    try:
        process_rabbit = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "apps.py")]
        )
        print("apps.py started (http://<host>:80)")

        process_run = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "run.py")]
        )
        print("run.py started")

        process_radar = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "radarScanControl.py")]
        )
        print("radarScanControl.py started")

        process_light = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "lightLockControl.py")]
        )
        print("lightLockControl.py started")

        process_switch = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "switchStatus.py")]
        )
        print("switchStatus.py started")

    except Exception as e:
        print("Error starting processes:", e)

    print("[send.py] All processes started. Running in background...")
    while True:
        time.sleep(60)