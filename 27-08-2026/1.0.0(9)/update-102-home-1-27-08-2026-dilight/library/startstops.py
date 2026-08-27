# startstop.py
__version__ = "1.0.0(9) - apply remove-debounce in scan_worker"

import os
import json
import time
import threading
import RPi.GPIO as GPIO
from network_utils import get_ip, get_local_ip, get_mac_address, get_hostname

# =====================================================================================================================================
#      START STOP
#      Name                       : START STOP
#      Version                    : 1.0.0(9) -APPLY-DEBOUNCE-IN-WORKER
#      Date Created               : 08-05-2026
#      Updated                    : 08-08-2026
#      Changes                    :
#        - scan_worker() now calls reader.apply_remove_debounce() on the
#          raw epc_seen result before saving to rfid2.json, using
#          config.json's "remove_confirm_cycles". Previously this only
#          happened inside reader.main() (via scan_with_zero_confirmation()),
#          which none of the production trigger paths (MQTT Startscan via
#          mqtts.py, door-close via radarScanControl.py, periodic startup
#          scan via run.py) actually call - they all go through
#          startstops.main() -> start_scan() -> scan_worker(), which called
#          reader.run_async_scan() directly and skipped debounce entirely.
#          This is why tag_debounce_state.json was never being created even
#          with "remove_confirm_cycles": 2 set in config.json.
#        - This is the SINGLE choke point all three trigger paths share, so
#          patching it here covers MQTT / door-sensor / periodic scans alike
#          without touching mqtts.py, radarScanControl.py, or run.py.
#        - zero-tag double-check (scan_with_zero_confirmation) is
#          intentionally NOT introduced here - only the debounce step was
#          missing/requested; adding zero-recheck here would change scan
#          timing/behavior beyond the scope of this fix.
#      Previous                   : 1.0.0(1) -FIX-OPEN-ABORT
#        - start_scan() no longer calls stop_evt.clear() — this prevents
#          the stop flag from handle_open() from being silently cleared
#          before the scan starts.
#        - Added stop_evt.is_set() guard after breaking from the door-wait loop
#          in main(), so if the door is opened during the 2-second timer,
#          the scan is aborted.
#        - Added stop_evt.is_set() guard at the beginning of start_scan()
#          before spawning the thread.
#      Author                     : Saifuddin
# ======================================================================================================================================

# === log ===
from logger import get_logger
log = get_logger("startstops")
# ==================

import reader
import utilitys

from reader import (
    SHUTDOWN_GPIO,
    set_baudrate,
    try_bootloader_scan
)

from door import DOOR1_GPIO

# ==================================================
# GLOBAL
# ==================================================
scan_thread  = None
proc_50      = None
STATUS_FILE  = "status.json"

DEBOUNCE_TIME = 0.05
CONFIRM_COUNT = 3
CONFIRM_DELAY = 0.01

# ==================================================
# STATIC INFO
# ==================================================
BROKER      = get_local_ip()
HOSTNAME    = get_hostname()
MAC         = get_mac_address("eth0")
MAC_ADDRESS = get_hostname()


# ==================================================
# DEBOUNCE (GPIO door-pin debounce - unrelated to tag remove-debounce)
# ==================================================
def read_stable(pin, confirm=CONFIRM_COUNT, delay=CONFIRM_DELAY):
    last  = GPIO.input(pin)
    count = 0

    while count < confirm:
        time.sleep(delay)
        val = GPIO.input(pin)

        if val == last:
            count += 1
        else:
            count = 0
            last  = val

    return last


# ==================================================
# DOOR HELPERS
# ==================================================
def is_door_open_raw():
    return read_stable(DOOR1_GPIO) == 1


def is_door_closed_raw():
    return read_stable(DOOR1_GPIO) == 0


# ==================================================
# SESSION ID
# ==================================================
def get_session_id_from_status():
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
                sid  = data.get("sessionId")

                if sid and isinstance(sid, str):
                    return sid.strip()

    except Exception as e:
        log.warning("[SCAN] Failed to read sessionId: %s", e)

    return ""


# ==================================================
# SCAN WORKER
# ==================================================
def scan_worker(stop_evt):

    try:
        utilitys.rotate_rfid_files()

        GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)

        log.info("[SCAN] Reader enabled")

        time.sleep(1.0)

        # FIX:
        # Check stop_evt after 1-second sleep —
        # the door may have opened during this time
        if stop_evt.is_set():
            log.warning("[SCAN] scan_worker: stop_evt set after reader enable delay, aborting")
            return

        set_baudrate()

        time.sleep(0.5)

        # FIX:
        # Check again after set_baudrate delay
        if stop_evt.is_set():
            log.warning("[SCAN] scan_worker: stop_evt set after baudrate delay, aborting")
            return

        baud = try_bootloader_scan()

        if not baud:
            log.error("[SCAN] Reader not responding")
            return

        log.info("[SCAN] Starting async scan...")

        scan_result = reader.run_async_scan(
            baud,
            stop_event=stop_evt
        )

        # =====================================================
        # NEW in 1.0.0(2): apply tag remove-debounce here.
        #
        # This is the single choke point shared by every production
        # trigger (MQTT Startscan via mqtts.py, door-close via
        # radarScanControl.py, and the periodic startup scan via
        # run.py) - all of them call startstops.main() -> start_scan()
        # -> scan_worker(), so patching only here covers all three
        # without touching those files.
        #
        # reader.run_async_scan() returns the RAW per-cycle result with
        # no debouncing applied (that only happened before inside
        # reader.main(), which none of these callers actually invoke).
        # We replicate just the debounce step here - NOT the zero-tag
        # double-check, which would change scan timing/behavior beyond
        # what was asked for.
        #
        # If the scan was aborted, there's nothing meaningful to
        # debounce/publish - skip, same as reader.main() does.
        # =====================================================
        if not scan_result.get("aborted"):
            try:
                cfg_now        = reader.load_config()
                confirm_cycles = int(cfg_now.get("remove_confirm_cycles", 1))

                published_epcs, debounce_stats = reader.apply_remove_debounce(
                    scan_result.get("epc_seen", {}).keys(), confirm_cycles
                )

                scan_result["epc_seen"] = {epc: 1 for epc in published_epcs}
                scan_result["debounce"] = debounce_stats

                log.info(
                    "[SCAN] Debounce applied: remove_confirm_cycles=%d "
                    "grace_kept=%d dropped_after_grace=%d unique_after=%d",
                    confirm_cycles,
                    debounce_stats.get("debounced_in_grace", 0),
                    debounce_stats.get("dropped_after_grace", 0),
                    len(scan_result["epc_seen"])
                )
            except Exception as e:
                log.error("[SCAN] Debounce step failed, publishing raw result: %s", e)

        utilitys.save_epc_to_json(
            scan_result.get("epc_seen", []),
            scan_result.get("duration", 0),
            aborted=scan_result.get("aborted", False)
        )

        if scan_result.get("aborted"):
            log.warning("[SCAN] Scan aborted - result discarded")
        else:
            log.info("[SCAN] Scan completed & saved")

    except Exception as e:
        log.error("[SCAN] scan_worker error: %s", e)

    finally:
        GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
        log.info("[SCAN] Reader disabled")


# ==================================================
# START SCAN
# ==================================================
def start_scan(stop_evt, scan_lock):
    global scan_thread

    # FIX BUG 3:
    # Do not clear stop_evt here!
    #
    # stop_evt.clear() in the previous version could erase
    # the OPEN signal already set by handle_open()
    # before it had a chance to be checked.
    #
    # stop_evt is already cleared in the correct place
    # (main() or handle_close()).

    # FIX:
    # Guard — if the door has already opened again
    # before scan starts, abort
    if stop_evt.is_set():

        log.warning("[SCAN] start_scan() called but stop_evt already set - door was opened, aborting")

        if scan_lock.locked():
            scan_lock.release()
            log.info("[SCAN] Mutex released (start_scan abort)")

        return

    if scan_thread and scan_thread.is_alive():
        log.warning("[SCAN] Scan already running")
        return

    def worker_with_mutex():
        try:
            scan_worker(stop_evt)

        finally:
            if scan_lock.locked():
                scan_lock.release()
                log.info("[SCAN] Mutex released")

    log.info("[SCAN] Spawning scan thread")

    scan_thread = threading.Thread(
        target=worker_with_mutex,
        daemon=True
    )

    scan_thread.start()


# ==================================================
# STOP ALL
# ==================================================
def stop_all(stop_evt):
    global proc_50, scan_thread

    log.info("[SCAN] Stop requested")

    stop_evt.set()

    if scan_thread and scan_thread.is_alive():

        log.info("[SCAN] Waiting for scan thread to finish...")

        scan_thread.join(timeout=8)

        if scan_thread.is_alive():
            log.warning("[SCAN] WARNING: scan thread still alive!")
        else:
            log.info("[SCAN] Scan thread finished")

    if proc_50 and proc_50.poll() is None:
        proc_50.terminate()
        proc_50.wait()
        proc_50 = None


# ==================================================
# RUN 50x
# ==================================================
def run_50x():
    global proc_50

    import subprocess

    log.info("[50x] Starting batch")

    proc_50 = subprocess.Popen(["python3", "test50.py"])

    proc_50.wait()

    log.info("[50x] Completed")


# ==================================================
# MAIN ENTRY
# ==================================================
def main(
    scan_lock,
    stop_evt,
    scan_session_id="",
    mqttc=None,
    topic_status=None,
    topic_door=None,
    trigger_time="",
    trigger_sig=""
):
    global scan_thread

    if scan_thread and scan_thread.is_alive():

        log.warning("[SCAN] Waiting for previous scan thread to finish...")

        stop_evt.set()

        scan_thread.join(timeout=8)

        if scan_thread.is_alive():
            log.warning("[SCAN] WARNING: Previous thread still alive after 8s!")
        else:
            log.info("[SCAN] Previous scan thread finished OK")

        stop_evt.clear()

    log.info("[SCAN] Acquiring mutex...")

    acquired = scan_lock.acquire(blocking=True, timeout=5)

    if not acquired:
        log.error("[SCAN] Busy - mutex timeout, skip")
        return

    log.info("[SCAN] Mutex acquired")

    try:
        stop_evt.clear()

        # ===============================
        # WAIT FOR DOOR CLOSED 2s (DEBOUNCED)
        # ===============================
        door_closed_time = None

        log.info("[SCAN] Waiting for door CLOSED for 2 seconds...")

        prev_door = read_stable(DOOR1_GPIO)

        log.info("[SCAN] Door initial: %s", "OPEN" if prev_door == 1 else "CLOSED")

        while True:

            if stop_evt.is_set():

                log.info("[SCAN] Stop during door wait")

                if scan_lock.locked():
                    scan_lock.release()

                return

            time.sleep(0.05)

            raw = GPIO.input(DOOR1_GPIO)

            if raw != prev_door:

                time.sleep(DEBOUNCE_TIME)

                stable = read_stable(DOOR1_GPIO)

                if stable != prev_door:

                    if stable == 0:
                        log.info("[DOOR] CLOSED (debounced)")
                    else:
                        log.info("[DOOR] OPENED (debounced)")

                    prev_door = stable

                else:
                    log.debug("[DOOR] Bounce ignored raw=%s stable=%s", raw, stable)

            # Timer logic
            if prev_door == 0:  # CLOSED

                if door_closed_time is None:

                    door_closed_time = time.time()

                    log.info("[SCAN] Door CLOSED confirmed, timer started")

                elif time.time() - door_closed_time >= 2:

                    current_sid = get_session_id_from_status()

                    if mqttc and topic_door:

                        try:
                            mqttc.publish(
                                topic_door,
                                json.dumps({
                                    "mac":       MAC_ADDRESS,
                                    "hostname":  HOSTNAME,
                                    "cmd":       "Scanning",
                                    "sessionId": current_sid,
                                    "time":      trigger_time,
                                    "sig":       trigger_sig
                                }),
                                qos=1
                            )

                            log.info("[MQTT] SCANNING sent sessionId=%s time='%s' sig='%s'",
                                     current_sid, trigger_time, trigger_sig)

                        except Exception as e:
                            log.error("[MQTT] SCANNING publish failed: %s", e)

                    break

            else:  # OPEN

                if door_closed_time is not None:
                    log.info("[SCAN] Door reopened, timer reset")
                    door_closed_time = None

        # FIX BUG 1:
        # Check stop_evt after exiting loop
        # (break after 2 seconds).
        #
        # Scenario:
        # The door opens again exactly when the 2-second timer completes.
        #
        # handle_open() already called stop_evt.set(),
        # but the loop already broke,
        # so the in-loop check never saw it.
        if stop_evt.is_set():

            log.warning("[SCAN] Door opened right at 2s mark - aborting before scan starts")

            if scan_lock.locked():
                scan_lock.release()
                log.info("[SCAN] Mutex released (post-break abort)")

            return

        # Continue to scan only if the door is truly still closed
        start_scan(stop_evt, scan_lock)

        # FIX: wait for the actual hardware scan to finish before main()
        # returns.
        #
        # start_scan() only SPAWNS scan_thread and returns immediately
        # (non-blocking) - by design, so the door-wait loop above never
        # blocks whoever called main(). But main() is ALWAYS invoked
        # from a background thread by every caller (mqtts.py's
        # _scan_worker, radarScanControl.py's door-scan worker,
        # run.py's periodic_scan) - so it is always safe for main()
        # itself to block here.
        #
        # Without this join, a caller's SCAN_MUTEX.release() (which
        # runs in their own `finally` block, immediately after main()
        # returns) fires the instant start_scan() spawns the thread -
        # i.e. right as the REAL hardware scan (baud detection, opening
        # the serial port, reading tags) is just STARTING, not when it's
        # actually done. That let a second Startscan/door-close event
        # slip past the mutex and open the SAME serial port while the
        # first scan was still mid-read, producing "No valid baud rate
        # found" / "Reader not responding" and a false zero-tag result -
        # exactly the failure seen in production logs.
        if scan_thread:
            scan_thread.join()
            log.info("[SCAN] main(): hardware scan thread finished, returning")

    except Exception as e:

        log.error("[SCAN] main() error: %s", e)

        if scan_lock.locked():
            scan_lock.release()
            log.info("[SCAN] Mutex released (error path)")
