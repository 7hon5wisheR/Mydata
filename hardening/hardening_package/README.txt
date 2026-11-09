HARDENING PACKAGE - lindungi SD card Raspberry (tanpa mengubah kode broker)

ISI
  1_COPY_TO_BROKER/hardening/sitecustomize.py    file BARU (penulisan atomik)
  1_COPY_TO_BROKER/harden_sd.sh                  file BARU (pengaturan OS)
  docs/EN_production/   dokumen bahasa Inggris (untuk produksi)
      1_Hardening_Steps_Standard.pdf             LANGKAH STANDAR  <-- IKUTI INI SAJA
      2_Optional_Journal_Advanced.pdf            langkah lanjutan OPSIONAL (berisiko, tidak perlu)
  docs/ID_indonesia/    dokumen bahasa Indonesia (isi sama)
      1_Langkah_Hardening_Standar.pdf            LANGKAH STANDAR  <-- IKUTI INI SAJA
      2_Opsional_Journal_Lanjutan.pdf            langkah lanjutan OPSIONAL (berisiko, tidak perlu)

LANGKAH SINGKAT (detail ada di dokumen 1)
  1. Pastikan overlay mati:  findmnt -no FSTYPE /   -> harus ext4
  2. Salin ISI folder 1_COPY_TO_BROKER ke /home/alice/broker/  (tidak ada file lama yang ditimpa)
  3. cd /home/alice/broker && chmod +x harden_sd.sh && sudo ./harden_sd.sh
  4. sudo reboot
  5. Cek:  findmnt /home/alice/broker/logs   (harus tmpfs)
           sudo systemctl show send -p Environment | grep PYTHONPATH
           systemctl status send
  6. Baru set "logger": "enable" di config.json (hanya jika logs/ = tmpfs)

JANGAN mengubah cmdline.txt. Dokumen 2 hanya untuk yang berpengalaman dan punya cadangan.
Belum diuji di Raspberry sungguhan: tes dulu di satu unit, termasuk cabut listrik berkali-kali.
