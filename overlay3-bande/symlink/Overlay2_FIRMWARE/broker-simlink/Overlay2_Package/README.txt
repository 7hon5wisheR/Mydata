Overlay2 1.0.0.(18) - Saifuddin
Volatile SD (overlay) + persistent store for config.json and conf.d/bridge.conf: /boot/firmware/broker (default) or a USB ext4 drive (--usb). Hostname + IP restored at boot.
Install:  copy overlay2/ into /home/alice/broker/ (next to apps.py), then
          cd /home/alice/broker/overlay2 && sudo ./setup_overlay2.sh          (USB mode: --usb or --format /dev/sdX)
Verify:   sudo ./check_overlay2.sh      Uninstall: overlay OFF, sudo ./remove_overlay2.sh
Files:    overlay2/{setup_overlay2.sh, check_overlay2.sh, remove_overlay2.sh, files/{overlay2-prepare, overlay2-prepare.service, sitecustomize.py, mosquitto.conf.template}}
Docs:     docs/EN/Overlay2_Guide_EN.pdf, docs/ID/Overlay2_Guide_ID.pdf
Remove the old setup_overlay_persist.sh / broker-persist helper first; do not combine.
