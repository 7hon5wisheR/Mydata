# utilitys.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - RENAME FIELD KEYS TO TITLE CASE"

import os
import json
import time
import shutil
import lock1
import light
import door
import temperature
import humidity
import mqtts
from network_utils import get_ip, get_local_ip, get_mac_address, get_hostname


# =====================================================================================================================================
#      UTILITYS
#      Name                       : UTILITYS
#      Version                    : 0.0.0.1
#      Date Created               : 05-05-2026
#      Updated                    : 21-05-2026
#      Changes                    : Fix abort path: set duration (from param)
#                                   and count (from rfid1) into restored_data
#                                   BEFORE building slim payload, so slim
#                                   payload correctly reflects both values
#      Author                     : Saifuddin
# ======================================================================================================================================


# === log ===
from logger import get_logger
log = get_logger("utilitys")
# ===========


BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

CONFIG_FILE = os.path.join(PROJECT_DIR, "config.json")
STATUS_FILE = os.path.join(PROJECT_DIR, "status.json")


# ==================================================
# STATIC INFO
# ==================================================
BROKER      = get_local_ip()
HOSTNAME    = get_hostname()
MAC         = get_mac_address("eth0")
MAC_ADDRESS = get_hostname()

log.info(" MACADDRESS: %s", MAC)
log.info(" CABINET:    %s", MAC_ADDRESS)
log.info(" BrokerAddr: %s", BROKER)
log.info(" HOSTNAME:   %s", HOSTNAME)


# ==================================================
# RFID FILE ROTATION
# ==================================================
def rotate_rfid_files():
    src = os.path.join(PROJECT_DIR, "rfid2.json")
    dst = os.path.join(PROJECT_DIR, "rfid1.json")
    if os.path.exists(src):
        try:
            shutil.copy(src, dst)
            log.info("[RFID] Snapshot rfid2.json -> rfid1.json")
        except Exception as e:
            log.error("[RFID] Snapshot failed: %s", e)


# ==================================================
# LOAD EPC FROM JSON
# ==================================================
def load_epc_from_json(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return set(data.get("inventory", []))
    except Exception:
        return set()


# ==================================================
# LOAD RFID FILTER
# ==================================================
def load_rfid_filter():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                flt = cfg.get("rfid_filter", [])
                if isinstance(flt, list):
                    return [str(x).upper() for x in flt if x]
    except Exception as e:
        log.error("[RFID] Config load failed: %s", e)
    return []


def apply_rfid_filter(epc_set):
    filters = load_rfid_filter()
    if not filters:
        return epc_set

    return {
        epc for epc in epc_set
        if any(epc.startswith(prefix) for prefix in filters)
    }


# ==================================================
# HELPER: STATUS JSON
# ==================================================
def load_status_json():
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def load_session_id():
    status = load_status_json()
    return status.get("sessionId", "")


# ==================================================
# SAVE EPC TO JSON + MQTT
# ==================================================
def save_epc_to_json(epc_seen, duration, aborted=False):

    # ---------- ABORT ----------
    if aborted:
        log.warning("[RFID] Scan aborted - restoring rfid1.json")
        try:
            src = os.path.join(PROJECT_DIR, "rfid1.json")
            dst = os.path.join(PROJECT_DIR, "rfid2.json")

            status     = load_status_json()
            session_id = load_session_id()
            hostname   = status.get("hostname", HOSTNAME)

            if os.path.exists(src):
                shutil.copy(src, dst)
                log.info("[RFID] Restored rfid1.json -> rfid2.json")

                with open(dst, "r") as f:
                    restored_data = json.load(f)

                # Take count & inventory from rfid1 (previous data)
                old_inventory = restored_data.get("inventory", [])
                old_count     = restored_data.get("count", len(old_inventory))

                # Update restored_data fields FIRST   slim payload reads from here
                restored_data["macaddress"]  = MAC_ADDRESS
                restored_data["hostname"]    = hostname
                restored_data["IP_ETH0"]     = get_ip("eth0")
                restored_data["sessionId"]   = session_id
                restored_data["time"]        = time.strftime("%Y-%m-%d %H:%M:%S")
                restored_data["duration"]    = duration    # <-- duration of the aborted scan
                restored_data["count"]       = old_count   # <-- count from previous data
                restored_data["inventory"]   = old_inventory
                restored_data["add"]         = []
                restored_data["remove"]      = []
                restored_data["Door1"]       = door.get_door1_status()
                restored_data["Light"]       = light.get_light_status()
                restored_data["Lock1"]       = lock1.get_lock1_status()
                restored_data["Temperature"] = temperature.get_temperature_status()
                restored_data["Humidity"]    = humidity.get_humidity_status()
                restored_data["aborted"]     = True
                restored_data["abort_info"]  = "Scan cancelled - previous data restored"
                restored_data["mac"]         = MAC_ADDRESS

                with open(dst, "w") as f:
                    json.dump(restored_data, f, indent=2)

                log.info("[RFID] Updated restored data | count=%d | duration=%s",
                         old_count, duration)

                # Slim payload - no inventory/add/remove/aborted/abort_info/time
                slim_payload = {
                    "macaddress":  restored_data["macaddress"],
                    "hostname":    restored_data["hostname"],
                    "IP_ETH0":     restored_data["IP_ETH0"],
                    "sessionId":   restored_data["sessionId"],
                    "duration":    restored_data["duration"],
                    "count":       restored_data["count"],
                    "Door1":       restored_data["Door1"],
                    "Light":       restored_data["Light"],
                    "Lock1":       restored_data["Lock1"],
                    "Temperature": restored_data["Temperature"],
                    "Humidity":    restored_data["Humidity"],
                    "mac":         MAC_ADDRESS,
                }
                try:
                    if mqtts.mqttc:
                        mqtts.mqttc.publish("out", json.dumps(slim_payload, separators=(',', ':')), qos=1)
                        log.info("[RFID] MQTT published slim payload (aborted scan)")
                    else:
                        log.warning("[RFID] mqttc not ready, skip publish")
                except Exception as e:
                    log.error("[RFID] MQTT publish failed: %s", e)

                return restored_data

            else:
                log.warning("[RFID] No rfid1.json to restore - sending abort notification")

                abort_data = {
                    "macaddress":  MAC_ADDRESS,
                    "hostname":    hostname,
                    "IP_ETH0":     get_ip("eth0"),
                    "sessionId":   session_id,
                    "time":        time.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration":    duration,   # <-- duration of the aborted scan
                    "count":       0,
                    "Door1":       door.get_door1_status(),
                    "Light":       light.get_light_status(),
                    "Lock1":       lock1.get_lock1_status(),
                    "Temperature": temperature.get_temperature_status(),
                    "Humidity":    humidity.get_humidity_status(),
                    "inventory":   [],
                    "add":         [],
                    "remove":      [],
                    "aborted":     True,
                    "abort_info":  "Scan cancelled - no previous data available",
                    "mac":         MAC_ADDRESS,
                }

                with open(dst, "w") as f:
                    json.dump(abort_data, f, indent=2)

                # Slim payload - no inventory/add/remove/aborted/abort_info/time
                slim_payload = {
                    "macaddress":  MAC_ADDRESS,
                    "hostname":    hostname,
                    "IP_ETH0":     get_ip("eth0"),
                    "sessionId":   session_id,
                    "duration":    duration,
                    "count":       0,
                    "Door1":       door.get_door1_status(),
                    "Light":       light.get_light_status(),
                    "Lock1":       lock1.get_lock1_status(),
                    "Temperature": temperature.get_temperature_status(),
                    "Humidity":    humidity.get_humidity_status(),
                    "mac":         MAC_ADDRESS,
                }
                try:
                    if mqtts.mqttc:
                        mqtts.mqttc.publish("out", json.dumps(slim_payload, separators=(',', ':')), qos=1)
                        log.info("[RFID] MQTT published slim payload (abort - no previous data)")
                    else:
                        log.warning("[RFID] mqttc not ready, skip publish")
                except Exception as e:
                    log.error("[RFID] MQTT publish failed: %s", e)

                return abort_data

        except Exception as e:
            log.error("[RFID] Restore/Publish failed: %s", e)

        return None

    # ---------- NORMAL SCAN ----------
    status     = load_status_json()
    session_id = load_session_id()
    hostname   = status.get("hostname", HOSTNAME)

    epc_raw = set(epc_seen.keys()) if isinstance(epc_seen, dict) else set(epc_seen)
    epc_new = apply_rfid_filter(epc_raw)
    epc_old = load_epc_from_json(os.path.join(PROJECT_DIR, "rfid1.json"))

    added   = list(epc_new - epc_old)
    removed = list(epc_old - epc_new)

    log.info(
        "[RFID] EPC raw=%d | filtered=%d | filter=%s",
        len(epc_raw), len(epc_new),
        load_rfid_filter() or "NONE"
    )

    json_data = {
        "macaddress":  MAC_ADDRESS,
        "hostname":    hostname,
        "IP_ETH0":     get_ip("eth0"),
        "sessionId":   session_id,
        "time":        time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration":    duration,
        "count":       len(epc_new),
        "Door1":       door.get_door1_status(),
        "Light":       light.get_light_status(),
        "Lock1":       lock1.get_lock1_status(),
        "Temperature": temperature.get_temperature_status(),
        "Humidity":    humidity.get_humidity_status(),
        "inventory":   list(epc_new),
        "add":         added,
        "remove":      removed
    }

    out_file = os.path.join(PROJECT_DIR, "rfid2.json")

    with open(out_file, "w") as f:
        json.dump(json_data, f, indent=2)

    log.info("[RFID] Saved rfid2.json | count=%d | add=%d | remove=%d",
             len(epc_new), len(added), len(removed))

    try:
        mqtts.publish_json_file(out_file)
        log.info("[RFID] MQTT published")
    except Exception as e:
        log.error("[RFID] MQTT publish failed: %s", e)

    return json_data
