#!/usr/bin/env python3
__version__ = "0.0.0.1 - normalize cmd title-case"
import os
import sys
import time
import json
import threading
import RPi.GPIO as GPIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR  = os.path.join(BASE_DIR, "library")
sys.path.insert(0, LIB_DIR)

# =====================================================================================================================================
#      SWITCH STATUS
#      Name                       : SWITCH STATUS
#      Version                    : 0.0.0.1
#      Date Created               : 05-05-2026
#      Date Update                : 09-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================


# === LOG ===
from logger import get_logger
log = get_logger("switch")
# ==================

import door
import mqtts

log.info("[INIT] SWITCH STATUS MODE (REAL GPIO)")

STATUS_FILE = os.path.join(BASE_DIR, "status.json")

# ==================================================
# SESSION
# ==================================================
def get_session_id():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f).get("sessionId", "")
    except:
        return ""

# ==================================================
# GPIO SETUP
# ==================================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(door.DOOR1_GPIO, GPIO.IN)

# ==================================================
# DOOR MONITOR
# ==================================================
def monitor_door():
    prev = GPIO.input(door.DOOR1_GPIO)
    log.info("[INIT] Door monitor running (ACTIVE LOW)")
    log.info("[INIT] Initial state: %s", "CLOSED" if prev == 0 else "OPEN")
    while True:
        time.sleep(0.1)
        cur = GPIO.input(door.DOOR1_GPIO)
        # CLOSED -> OPEN
        if prev == 0 and cur == 1:
            log.info("[DOOR] OPENED")
            if mqtts.mqttc:
                mqtts.mqttc.publish(
                    mqtts.TOPIC_DOOR,
                    json.dumps({
                        "cmd":       "Open",
                        "sessionId": get_session_id(),
                        "mac":       mqtts.MAC_ADDRESS,
                        "hostname":  mqtts.HOSTNAME
                    }),
                    qos=1
                )
                log.info("[DOOR] Published OPENED to MQTT")
            else:
                log.warning("[DOOR] mqttc is None - OPENED not published!")
        # OPEN -> CLOSED
        elif prev == 1 and cur == 0:
            log.info("[DOOR] CLOSED")
            if mqtts.mqttc:
                mqtts.mqttc.publish(
                    mqtts.TOPIC_DOOR,
                    json.dumps({
                        "cmd":       "Close",
                        "sessionId": get_session_id(),
                        "mac":       mqtts.MAC_ADDRESS,
                        "hostname":  mqtts.HOSTNAME
                    }),
                    qos=1
                )
                log.info("[DOOR] Published CLOSE to MQTT")
            else:
                log.warning("[DOOR] mqttc is None - CLOSE not published!")
        prev = cur

# ==================================================
# MQTT SETUP (publish-only, no subscribe to "in")
# switchStatus only needs to publish door events to "out".
# Using mqtt_setup_safe() would also subscribe to "in" and
# trigger on_message -> causing duplicate status publishes.
# ==================================================
def mqtt_setup_publish_only():
    import paho.mqtt.client as mqtt_client

    def _on_connect(client, userdata, flags, rc):
        log.info("[SWITCH] MQTT connected rc=%s (publish-only, no subscribe to 'in')", rc)

    client_id = f"{mqtts.HOSTNAME}-switch"
    c = mqtt_client.Client(client_id=client_id, clean_session=True)
    c.on_connect = _on_connect
    c.connect(mqtts.BROKER, 1883, 60)
    c.loop_start()

    mqtts.mqttc = c
    log.info("[SWITCH] MQTT publish-only client started id=%s broker=%s", client_id, mqtts.BROKER)


# ==================================================
# MAIN
# ==================================================
def main():
    mqtt_setup_publish_only()
    threading.Thread(target=monitor_door, daemon=True).start()
    log.info("[INIT] Door monitor thread started")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
