Overlay2 options (version 1.0.0.(18))
1_SYMLINK : config.json / bridge.conf are symlinks in the broker folder pointing to the persistent store. Broker code is NOT modified.
2_DIRECT  : no symlink and no copy in the broker folder; the patched broker code reads the persistent store directly.
Each folder has Overlay2_FIRMWARE.zip (store = /boot/firmware/broker) and Overlay2_USB.zip (store = /mnt/persist/broker).
Each of those contains Overlay2_Package.zip (scripts + EN/ID guides), Broker_with_Overlay2.zip and mosquitto.conf.
Install only ONE option on a Pi (run remove_overlay2.sh of the installed one first).
