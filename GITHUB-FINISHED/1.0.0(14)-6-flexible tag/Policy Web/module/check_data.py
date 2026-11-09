import sqlite3, json

conn = sqlite3.connect("sensor_data.db")

rows = conn.execute("""
    SELECT topic, raw_payload FROM sensor_sessions ORDER BY recorded_at DESC LIMIT 20
""").fetchall()

for topic, raw in rows:
    print(f"{topic} {raw}")

print(f"\n--- Total sessions  : {conn.execute('SELECT COUNT(*) FROM sensor_sessions').fetchone()[0]}")
print(f"--- Total inventory : {conn.execute('SELECT COUNT(*) FROM inventory_items').fetchone()[0]}")
conn.close()