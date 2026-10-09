"""Taruh file ini sejajar dengan folder 'broker', lalu jalankan:
    python updateversion.py
Semua file di broker (termasuk subfolder library) yang berisi OLD akan diganti ke NEW.
"""
from pathlib import Path

OLD = "1.0.0(9)"
NEW = "1.0.0(10)"
FOLDER = Path("broker")

jumlah = 0
for f in FOLDER.rglob("*.py"):
    teks = f.read_text(encoding="utf-8")
    if OLD in teks:
        f.write_text(teks.replace(OLD, NEW), encoding="utf-8")
        print(f"Diupdate: {f}  ({teks.count(OLD)}x)")
        jumlah += 1

print(f"Selesai. {jumlah} file diubah dari {OLD} ke {NEW}")
