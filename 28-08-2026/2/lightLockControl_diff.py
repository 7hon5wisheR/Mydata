# === lightLockControl.py -- add these lines in main() ===
#
# def main():
#     client_id = f"{HOSTNAME}-lightlock"
#     mqttc_ll  = mqtt.Client(client_id=client_id, clean_session=True)
#     mqttc_ll.on_connect = on_connect
#     mqttc_ll.on_message = on_message
#     mqttc_ll.connect(BROKER, 1883, 60)
#
# +   # Start the in-process live "dim" watcher (replaces the old,
# +   # unsafe light_pwm.py no-arg watch mode - runs as a thread
# +   # inside THIS process, so GPIO 16/18 still only has one owner)
# +   sublight.start_dim_watcher()
# +
# +   # Write a lock file so light_pwm.py refuses to run while this
# +   # service (the real GPIO owner) is active
# +   with open("/tmp/lightlockcontrol.lock", "w") as f:
# +       f.write(str(os.getpid()))
#
#     log.info("[LIGHTLOCK] MQTT client started id=%s broker=%s", client_id, BROKER)
#     mqttc_ll.loop_forever()
