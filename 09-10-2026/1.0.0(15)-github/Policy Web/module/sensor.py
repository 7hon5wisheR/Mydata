import json
import sqlite3
import logging
import os
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt

# ─── PATH ─────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, "sensor_data.db")

# ─── CONFIG ─────────────────────────────
CONFIG_FILE = os.path.join(BASE_DIR, "module.json")

MQTT_PORT      = 1883
MQTT_TOPIC     = "#"
MQTT_USER      = "guest"
MQTT_PASS      = "guest"
RETENTION_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
)
log = logging.getLogger(__name__)

# ─── LOAD CONFIG ─────────────────────────────
def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

# ─── DATABASE ─────────────────────────────
def get_conn():
    log.info("📂 DB PATH: %s", DB_FILE)

    if os.path.isdir(DB_FILE):
        raise Exception(f"{DB_FILE} itu folder, harus file!")

    conn = sqlite3.connect(DB_FILE, check_same_thread=False)

    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            mac TEXT,
            mac_address TEXT,
            hostname TEXT,
            ip_eth0 TEXT,
            session_id TEXT,
            recorded_at TEXT,
            inserted_at TEXT DEFAULT (datetime('now','localtime')),
            duration INTEGER,
            count INTEGER,
            door1 TEXT,
            light TEXT,
            lock1 TEXT,
            rfid_cmd TEXT,
            raw_payload TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            epc TEXT,
            status TEXT
        )
    """)

    conn.commit()
    log.info("✅ Database ready")


def already_saved(conn, mac, recorded_at):
    if not mac or not recorded_at:
        return False
    cur = conn.execute(
        "SELECT 1 FROM sensor_sessions WHERE mac=? AND recorded_at=? LIMIT 1",
        (mac, recorded_at)
    )
    return cur.fetchone() is not None


def insert_session(conn, topic, data, raw, recorded_at):
    cur = conn.execute("""
        INSERT INTO sensor_sessions
        (topic, mac, mac_address, hostname, ip_eth0, session_id,
         recorded_at, duration, count, door1, light, lock1, rfid_cmd, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        topic,
        data.get("mac"),
        data.get("macaddress") or data.get("macAddress"),
        data.get("hostname"),
        data.get("IP_ETH0"),
        data.get("sessionId"),
        recorded_at,  
        data.get("duration"),
        data.get("count"),
        data.get("DOOR1"),
        data.get("LIGHT"),
        data.get("LOCK1"),
        data.get("rfid") or data.get("cmd"),
        raw,
    ))
    return cur.lastrowid


def insert_inventory(conn, session_id, data):
    rows = []

    for epc in data.get("inventory", []):
        rows.append((session_id, epc, "present"))

    for epc in data.get("add", []):
        rows.append((session_id, epc, "add"))

    for epc in data.get("remove", []):
        rows.append((session_id, epc, "remove"))

    if rows:
        conn.executemany(
            "INSERT INTO inventory_items (session_id, epc, status) VALUES (?, ?, ?)",
            rows
        )


def purge_old_records(conn):
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM sensor_sessions WHERE recorded_at < ?", (cutoff,))
    conn.commit()

# ─── MQTT ─────────────────────────────
conn = None

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("✅ MQTT Connected")
        client.subscribe(MQTT_TOPIC)
    else:
        log.error("❌ MQTT Failed")


def on_message(client, userdata, msg):
    global conn

    topic = msg.topic
    raw = msg.payload.decode("utf-8", errors="replace").strip()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        data = json.loads(raw)
    except:
        recorded_at = now

        conn.execute("""
            INSERT INTO sensor_sessions (topic, raw_payload, recorded_at)
            VALUES (?, ?, ?)
        """, (topic, raw, recorded_at))
        conn.commit()

        log.info(f"{recorded_at} | {topic} | {raw}")
        return

    mac = data.get("mac") or data.get("macaddress")

  
    recorded_at = data.get("time") or now

    if already_saved(conn, mac, recorded_at):
        return

    try:
        sid = insert_session(conn, topic, data, raw, recorded_at)
        insert_inventory(conn, sid, data)

        conn.commit()

        log.info(f"{recorded_at} | {topic} | {raw[:80]}")

        purge_old_records(conn)

    except Exception as e:
        log.exception("DB ERROR: %s", e)
        conn.rollback()


def start():
    global conn

    conn = get_conn()
    init_db(conn)

    cfg = load_config()
    mqtt_host = cfg.get("ip_broker", "localhost")

    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    client.on_connect = on_connect
    client.on_message = on_message

    log.info("Connecting MQTT %s:%s", mqtt_host, MQTT_PORT)
    client.connect(mqtt_host, MQTT_PORT, 60)

    client.loop_forever()


if __name__ == "__main__":
    start()