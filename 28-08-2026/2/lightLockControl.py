#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - switched to sublight (PWM dim control)"

import os
import sys
import time
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR  = os.path.join(BASE_DIR, "library")
sys.path.insert(0, LIB_DIR)


# =====================================================================================================================================
#      LIGHT LOCK CONTROL
#      Name                       : LIGHT LOCK CONTROL - nuitka .so compatible
#      Version                    : 1.0.0(9)
#      Date Created               : 05-05-2026
#      Updated                    : 27-08-2026
#      Changes                    : light.py replaced with sublight.py -
#                                   ON now reads brightness from config.json's
#                                   "dim" field (PWM), OFF forces PWM to 0%
#                                   WITHOUT touching config.json. Light status
#                                   is written into the existing status.json
#                                   ("Light" field) so run.py/mqtts.py (a
#                                   separate service) can read it - no new
#                                   status file, no web-side changes needed.
#      Author                     : Saifuddin
# ======================================================================================================================================


from logger import get_logger
log = get_logger("lightLockControl")

import paho.mqtt.client as mqtt
from network_utils import get_local_ip, get_mac_address, get_hostname
import sublight
import lock1

BROKER      = get_local_ip()
HOSTNAME    = get_hostname()
MAC         = get_mac_address("eth0")
MAC_ADDRESS = get_hostname()

log.info("[LIGHTLOCK] MACADDRESS : %s", MAC)
log.info("[LIGHTLOCK] CABINET    : %s", MAC_ADDRESS)
log.info("[LIGHTLOCK] BrokerAddr : %s", BROKER)
log.info("[LIGHTLOCK] HOSTNAME   : %s", HOSTNAME)


def on_connect(client, userdata, flags, rc):
    log.info("[LIGHTLOCK] Connected rc=%s", rc)
    client.subscribe("in", qos=1)
    log.info("[LIGHTLOCK] Subscribed to topic: in")


def on_message(client, userdata, msg):
    payload_raw = msg.payload.decode().strip()
    log.info("[LIGHTLOCK] Received topic=%s payload=%s", msg.topic, payload_raw)

    if msg.topic != "in":
        log.debug("[LIGHTLOCK] Ignored topic: %s", msg.topic)
        return

    try:
        data       = json.loads(payload_raw)
        light_cmd  = data.get("light",   "").strip()
        lock_cmd   = data.get("lock",    "").strip()
        mac_target = data.get("mac",     "").lower().strip()
        seconds    = data.get("seconds", None)

        log.debug("[LIGHTLOCK] Parsed light='%s' lock='%s' mac='%s' seconds=%s",
                  light_cmd, lock_cmd, mac_target, seconds)

    except Exception as e:
        log.error("[LIGHTLOCK] JSON parse error: %s", e)
        return

    if not light_cmd and not lock_cmd:
        log.debug("[LIGHTLOCK] No light/lock in payload, skip.")
        return

    if mac_target:
        is_match = (
            mac_target == "all" or
            mac_target == MAC.lower() or
            mac_target == MAC_ADDRESS.lower()
        )
        if not is_match:
            log.debug("[LIGHTLOCK] Ignored - mac '%s' doesn't match", mac_target)
            return
        log.debug("[LIGHTLOCK] MAC filter passed - target: '%s'", mac_target)

    light_upper = light_cmd.strip().title()
    lock_upper  = lock_cmd.strip().title()

    # =====================================================
    #  LIGHT (PWM via sublight.py)
    #  ON  : brightness diambil dari config.json["dim"] (live)
    #  OFF : paksa PWM 0% - config.json TIDAK diubah
    # =====================================================
    if light_upper == "On":
        if seconds:
            log.info("[LIGHT] On for %ss (timed)", seconds)
            sublight.sub_set(True, seconds=float(seconds))
        else:
            log.info("[LIGHT] On (permanent)")
            sublight.sub_on()

    if light_upper == "Off":
        if seconds:
            log.info("[LIGHT] Off for %ss (timed)", seconds)
            sublight.sub_set(False, seconds=float(seconds))
        else:
            log.info("[LIGHT] Off (permanent)")
            sublight.sub_off()

    if lock_upper == "Lock":
        if seconds:
            log.info("[LOCK] Lock for %ss (timed)", seconds)
            lock1.lock1_set(True, seconds=float(seconds))
        else:
            log.info("[LOCK] Lock (permanent)")
            lock1.lock1_set(True)

    if lock_upper == "Unlock":
        if seconds:
            log.info("[LOCK] Unlock for %ss (timed)", seconds)
            lock1.lock1_set(False, seconds=float(seconds))
        else:
            log.info("[LOCK] Unlock (permanent)")
            lock1.lock1_set(False)

    log.info("[LIGHTLOCK] Hardware executed. Status update handled by mqtts.py.")


# ==================================================
# MAIN
# ==================================================
def main():
    client_id = f"{HOSTNAME}-lightlock"
    mqttc_ll  = mqtt.Client(client_id=client_id, clean_session=True)
    mqttc_ll.on_connect = on_connect
    mqttc_ll.on_message = on_message
    mqttc_ll.connect(BROKER, 1883, 60)

    # Start the in-process live "dim" watcher (replaces the old,
    # unsafe light_pwm.py no-arg watch mode - runs as a thread
    # inside THIS process, so GPIO 16/18 still only has one owner)
    sublight.start_dim_watcher()

    # Write a lock file so light_pwm.py refuses to run while this
    # service (the real GPIO owner) is active
    with open("/tmp/lightlockcontrol.lock", "w") as f:
        f.write(str(os.getpid()))

    log.info("[LIGHTLOCK] MQTT client started id=%s broker=%s", client_id, BROKER)
    mqttc_ll.loop_forever()


if __name__ == "__main__":
    main()