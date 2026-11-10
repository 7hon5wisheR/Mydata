HARDENING / OVERLAY PACKAGE - lindungi SD card Raspberry tanpa mengubah kode broker
Protect the Raspberry Pi SD card without changing any broker code

PILIH SATU / CHOOSE ONE
  A_HARDENING        (REKOMENDASI / RECOMMENDED)  tanpa overlay, data selalu permanen
                     no overlay, all data always persistent.
                     Panduan / Guide: docs/ID/A1_*.pdf  |  docs/EN/A1_*.pdf
  B_OVERLAY_PERSIST  overlay + HANYA config.json, bridge.conf, hostname, IP eth0 permanen (status/rfid/log di RAM). BELUM DIUJI di Raspberry fisik -> UJI DI BENCH DULU.
                     overlay + ONLY config.json, bridge.conf, hostname, eth0 IP persistent (status/rfid/logs in RAM). NOT TESTED on a physical Pi -> BENCH TEST FIRST.
                     Panduan / Guide: docs/ID/B1_*.pdf  |  docs/EN/B1_*.pdf

JANGAN pasang A dan B sekaligus. / Do NOT install A and B together.

LANGKAH SINGKAT A (detail: A1)
  1. findmnt -no FSTYPE /   -> ext4 (overlay mati / overlay off)
  2. Salin ISI A_HARDENING ke /home/alice/broker/ (tidak ada file lama yang ditimpa / no existing file is overwritten)
  3. cd /home/alice/broker && chmod +x harden_sd.sh && sudo ./harden_sd.sh && sudo reboot
  4. findmnt /home/alice/broker/logs  -> tmpfs

LANGKAH SINGKAT B v2 (detail: B1) - alur master -> clone 40 kabinet
  Master (dikerjakan Anda, bukan teknisi):
  1. findmnt -no FSTYPE /   -> ext4 ; saklar DIP master = IP master (mis. .101)
  2. Salin ISI B_OVERLAY_PERSIST ke /home/alice/broker/ (timpa file B lama)
  3. chmod +x *.sh overlay_persist/broker-persist && sudo ./setup_overlay_persist.sh
     sudo systemctl restart send ipservice && sudo ./verify_overlay_persist.sh
  4. sudo raspi-config nonint do_overlayfs 0 && sudo reboot ; lalu verify lagi
  5. Jalankan tes B1 bagian 5 (terutama Tes 5-9: ganti saklar DIP, mati lampu, clone nyata)
  6. Kembalikan master (saklar .101, hostname/bridge.conf nama master), verify, sudo poweroff, cabut SD, buat image
  Teknisi: lihat docs/*/B2_* (atur saklar DIP, nyalakan, tunggu satu reboot otomatis)
  Matikan overlay / disable overlay: sudo raspi-config nonint do_overlayfs 1 && sudo reboot
  Update broker saat overlay aktif / update with overlay on: sudo ./update_broker.sh /home/alice/new_broker

  BARU di v2: IP eth0 (/etc/netplan/90-NM-*.yaml) ikut permanen; jeda mulai ipservice; verify_overlay_persist.sh.
  NEW in v2: eth0 IP profile is persistent; ipservice start delay; verify_overlay_persist.sh.

docs/*/A2_*  langkah lanjutan OPSIONAL (cmdline.txt) - berisiko, tidak perlu / optional, risky, not needed
docs/*/B2_*  lembar teknisi / technician sheet
docs/*/C*    perbandingan overlay vs hardening / comparison

Buat image cadangan SD card sebelum memulai jika memungkinkan. / Make an SD card image backup first if possible.
