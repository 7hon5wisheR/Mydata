import sqlite3

def export_to_txt(db_path, output_file):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print("⏳ Mengambil data dari database...")

        # Ambil semua data tanpa LIMIT
        cursor.execute("""
            SELECT topic, raw_payload, recorded_at
            FROM sensor_sessions
            ORDER BY recorded_at DESC
        """)
        rows = cursor.fetchall()

        print(f"📦 Total data diambil: {len(rows)}")

        # Tulis ke file txt
        with open(output_file, "w", encoding="utf-8") as f:
            for topic, raw, recorded_at in rows:
                f.write(f"{recorded_at} | {topic} | {raw}\n")

            # Tambahkan summary
            total_sessions = cursor.execute(
                "SELECT COUNT(*) FROM sensor_sessions"
            ).fetchone()[0]

            total_inventory = cursor.execute(
                "SELECT COUNT(*) FROM inventory_items"
            ).fetchone()[0]

            f.write("\n")
            f.write(f"--- Total sessions  : {total_sessions}\n")
            f.write(f"--- Total inventory : {total_inventory}\n")

        conn.close()
        print(f"✅ Export selesai: {output_file}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    db_path = "sensor_data.db"
    output_file = "export_sensor_sessions.txt"

    export_to_txt(db_path, output_file)