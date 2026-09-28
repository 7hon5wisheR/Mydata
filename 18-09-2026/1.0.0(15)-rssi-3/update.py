import os

OLD_VERSION = "1.0.0(14)"
NEW_VERSION = "1.0.0(15)"

# Folder tempat script ini berada
ROOT_FOLDER = os.path.dirname(os.path.abspath(__file__))

changed_files = []
skipped_files = []

for root, dirs, files in os.walk(ROOT_FOLDER):

    for filename in files:

        # Hanya proses file Python
        if not filename.endswith(".py"):
            continue

        filepath = os.path.join(root, filename)

        # Jangan mengubah script update_version.py sendiri
        if os.path.abspath(filepath) == os.path.abspath(__file__):
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Tidak ada versi yang dicari
            if OLD_VERSION not in content:
                skipped_files.append(filepath)
                continue

            # Ganti semua kemunculan
            new_content = content.replace(OLD_VERSION, NEW_VERSION)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

            changed_files.append(filepath)

        except Exception as e:
            print(f"GAGAL: {filepath}")
            print(f"       {e}")

print()
print("=" * 60)
print(f"Versi lama : {OLD_VERSION}")
print(f"Versi baru : {NEW_VERSION}")
print("=" * 60)

print()
print(f"File yang diubah: {len(changed_files)}")

for filepath in changed_files:
    print(f"  OK  {os.path.relpath(filepath, ROOT_FOLDER)}")

print()
print("Selesai.")