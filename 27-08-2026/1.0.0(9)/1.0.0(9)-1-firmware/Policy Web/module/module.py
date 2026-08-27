from flask import Flask, render_template, request, jsonify
import requests
import json
import socket
import paho.mqtt.client as mqtt
import threading
import os
import time
import re
import subprocess

app = Flask(__name__)

# ============================================================
# RF MODE TABLE
# Must match reader.py's RF_MODE_TABLE keys exactly. Only the
# CODE (e.g. "DC") gets written to config.json's "rf_mode" field -
# the description here is just for validation + logging on this
# side; the human-readable label lives in module.html.
# ============================================================
RF_MODE_INFO = {
    "CB": "1000+ tags/s : DSB, FM0, -78dBm (less sensitive)",
    "6F": "800+  tags/s : PR,  FM0, -78dBm",
    "DC": "700+  tags/s : DSB, M=2, -81dBm (more sensitive than CB)",
    "65": "550+  tags/s : PR,  M=2, -81dBm",
    "2D": "400+  tags/s : PR,  M=4, -84dBm",
    "73": "400+  tags/s : PR,  M=4, -84dBm",
    "70": "300+  tags/s : PR,  M=2, -84dBm",
    "67": "250+  tags/s : PR,  M=2, -84dBm",
    "69": "200+  tags/s : PR,  M=4, -87dBm",
    "6B": "150+  tags/s : PR,  M=4, -88dBm",
    "71": "50+   tags/s : PR,  M=8, -93dBm",
}
VALID_RF_MODES = set(RF_MODE_INFO.keys())

# ============================================================
# TX POWER (dBm)
# Must match reader.py's POWER_TABLE keys exactly (25-33 dBm).
# Only the integer dBm value gets written to config.json's
# "power" field - reader.py's get_power_command() is STRICT and
# will refuse to start the reader if config.json's "power" is
# missing or outside this range, so we validate it here first.
# ============================================================
POWER_MIN = 25
POWER_MAX = 33
VALID_POWER_DBM = set(range(POWER_MIN, POWER_MAX + 1))

# ============================================================
# LIGHT DIM (brightness level, %)
# Must match light.py's expectations for config.json's "dim"
# field: 0 = brightest, 100 = dimmest, in steps of 10. Unlike
# rf_mode/power/antenna, light.py's _get_configured_brightness()
# re-reads config.json's "dim" value on EVERY light_on() call
# (it is not cached at process startup), so changing "dim" never
# requires a reboot of the reader/RPi process - the next time the
# light is turned on, the new value is picked up automatically.
# ============================================================
DIM_MIN = 0
DIM_MAX = 100
DIM_STEP = 10
VALID_DIM = set(range(DIM_MIN, DIM_MAX + 1, DIM_STEP))

# ============================================================
# ANTENNA SELECTION
# reader.py's get_antenna_command() / build_enable_ant_command()
# supports ANY combination of individual antenna IDs (not just
# a fixed list of presets) - single antennas alone (e.g. just "3"),
# or arbitrary multi-antenna combos (e.g. "1,4"). So this side
# does not restrict the UI to a handful of preset combinations;
# instead it validates that whatever comma-separated antenna IDs
# were selected are well-formed and within the physical antenna
# range of this reader.
#
# ANTENNA_MIN/MAX reflect the physical antenna ports exposed by the
# module.html checkbox UI. This deployment's readers are the
# Silion 8-port E710 module, which physically exposes 8 antenna
# ports, so the UI (and this validation) is scoped to 1-8.
# reader.py itself can technically handle antenna IDs up to 32 per
# the protocol doc, but 8 is the correct ceiling for THIS hardware
# - update ANTENNA_MAX here (and in module.html / apps.py / the
# Raspberry Pi's ANTENNA_MAX) together if the hardware ever changes.
# ============================================================
ANTENNA_MIN = 1
ANTENNA_MAX = 8


def parse_antenna_value(value):
    """
    Parses the antenna value string sent from module.html - a
    comma-separated list of antenna IDs selected via checkboxes, e.g.
    "3" (antenna 3 only), "1,4" (antennas 1 and 4 only), "1,2,3,4,5,6,7,8"
    (all eight) - into a validated, deduplicated, sorted list of
    antenna ID strings, e.g. ["1", "4"]. This list is written as-is
    into config.json's "antenna" field, so whatever the user checks
    is exactly what gets enabled - no snapping to a preset.

    Raises ValueError with a human-readable message if the value is
    empty, malformed, or contains an antenna ID outside the valid
    range (ANTENNA_MIN..ANTENNA_MAX).
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("no antenna selected")

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("no antenna selected")

    ids = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"'{p}' is not a valid antenna number")
        n = int(p)
        if not (ANTENNA_MIN <= n <= ANTENNA_MAX):
            raise ValueError(
                f"antenna {n} is out of range ({ANTENNA_MIN}-{ANTENNA_MAX})"
            )
        ids.append(n)

    ids = sorted(set(ids))
    return [str(i) for i in ids]

# ============================================================
# CONFIG FILE
# ============================================================

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "module.json")

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

# ============================================================
# NETWORK HELPERS
# ============================================================

def check_port_open(ip, port, timeout=0.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except:
        return False

def get_default_gateways():
    gateways = []
    try:
        out = subprocess.check_output(
            "route print 0.0.0.0", shell=True, text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                gw = parts[2]
                if gw not in gateways:
                    gateways.append(gw)
        print(f"[DETECT] Gateways found: {gateways}")
    except Exception as e:
        print(f"[DETECT] route print error: {e}")
    return gateways

def get_all_local_subnets():
    subnets = []
    try:
        out = subprocess.check_output(
            "ipconfig", shell=True, text=True, stderr=subprocess.DEVNULL
        )
        for m in re.finditer(r"IPv4.*?:\s*(\d+\.\d+\.\d+)\.\d+", out):
            prefix = m.group(1)
            if not prefix.startswith("127.") and prefix not in subnets:
                subnets.append(prefix)
        print(f"[DETECT] Local subnets: {subnets}")
    except Exception as e:
        print(f"[DETECT] ipconfig error: {e}")
    return subnets

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# ============================================================
# AUTO-DETECT BROKER IP
# ============================================================

def get_ethernet_ip():
    env_ip = os.environ.get("ETHERNET_IP", "").strip()
    if env_ip:
        print(f"[DETECT] Using ENV ETHERNET_IP: {env_ip}")
        return env_ip

    print("[DETECT] Scanning Windows ICS subnet 192.168.137.x ...")
    for last in [1, 2, 254, 100]:
        candidate = f"192.168.137.{last}"
        print(f"[DETECT] Trying {candidate} ...")
        if check_port_open(candidate, 1883, timeout=1) or check_port_open(candidate, 15672, timeout=1):
            print(f"[DETECT] Broker found at ICS subnet: {candidate}")
            return candidate

    for gw in get_default_gateways():
        print(f"[DETECT] Checking gateway {gw} ...")
        if check_port_open(gw, 1883) or check_port_open(gw, 15672):
            print(f"[DETECT] Broker found at gateway: {gw}")
            return gw

    docker_internal_prefixes = ["192.168.65", "172.17", "172.18", "172.19",
                                 "172.20", "172.21", "172.22", "172.23",
                                 "172.24", "172.25", "172.26", "172.27",
                                 "172.28", "172.29", "172.30", "172.31"]
    for prefix in get_all_local_subnets():
        if any(prefix.startswith(d) for d in docker_internal_prefixes):
            print(f"[DETECT] Skipping Docker-internal subnet {prefix}.x")
            continue
        for last in [1, 254, 2, 100]:
            candidate = f"{prefix}.{last}"
            print(f"[DETECT] Scanning {candidate} ...")
            if check_port_open(candidate, 1883) or check_port_open(candidate, 15672):
                print(f"[DETECT] Broker found at: {candidate}")
                return candidate

    print("[DETECT] No broker found automatically.")
    return None

LOCAL_IP = get_local_ip()

BROKER_PORT = 1883

_watcher_pause = False
_publishing_ip_broker = False

_detected_ip = get_ethernet_ip()
config = load_config()
_saved_ip = config.get("ip_broker", "").strip()

if _saved_ip and _detected_ip and _saved_ip != _detected_ip:
    CURRENT_BROKER = _saved_ip
    print(f"[INIT] Saved IP ({_saved_ip}) != detected ({_detected_ip}) -> using saved")
elif _detected_ip:
    CURRENT_BROKER = _detected_ip
    if _saved_ip != _detected_ip:
        _watcher_pause = True
        config["ip_broker"] = CURRENT_BROKER
        save_config(config)
        _watcher_pause = False
    print(f"[INIT] Broker auto-detected: {CURRENT_BROKER}")
elif _saved_ip:
    CURRENT_BROKER = _saved_ip
    print(f"[INIT] Using saved broker from config: {CURRENT_BROKER}")
else:
    CURRENT_BROKER = ""
    print("[INIT] WARNING: No broker found. Waiting for manual update...")

def get_rabbit_api(broker_ip):
    return f"http://guest:guest@{broker_ip}:15672/api/queues"

# ============================================================
# PUBLISH IP_BROKER to MQTT
# ============================================================

def publish_ip_broker_to_mqtt(new_ip, broker_ip):
    global _publishing_ip_broker
    try:
        _publishing_ip_broker = True
        c = mqtt.Client()
        c.connect(broker_ip, BROKER_PORT, 60)
        c.loop_start()
        msg = json.dumps({"ip_broker": new_ip}, separators=(',', ':'))
        c.publish("in", msg).wait_for_publish()
        c.loop_stop()
        c.disconnect()
        print(f"[MQTT PUBLISH] ip_broker '{new_ip}' published to 'in'")
    except Exception as e:
        print(f"[MQTT PUBLISH] Failed to publish ip_broker: {e}")
    finally:
        time.sleep(0.5)
        _publishing_ip_broker = False

# ============================================================
# APPLY IP BROKER
# ============================================================

def apply_new_broker(new_ip, publish=True):
    global CURRENT_BROKER, config, _watcher_pause

    old_ip = CURRENT_BROKER
    _watcher_pause = True

    CURRENT_BROKER = new_ip

    config = load_config()
    config["ip_broker"] = new_ip
    save_config(config)

    print(f"[BROKER] Updated: {old_ip} -> {new_ip} (saved to module.json)")

    time.sleep(0.3)
    _watcher_pause = False

    if publish and new_ip:
        threading.Thread(
            target=publish_ip_broker_to_mqtt,
            args=(new_ip, new_ip),
            daemon=True
        ).start()

# ============================================================
# MQTT SUBSCRIPTION PATTERN
# ============================================================

MQTT_SUB_RE = re.compile(r"^mqtt-subscription-(.+?)qos\d+$")

def extract_cabinet_id_from_queue(name):
    m = MQTT_SUB_RE.match(name)
    if m:
        cabinet_id = m.group(1)
        if re.search(r"CAB\d+", cabinet_id, re.IGNORECASE):
            return cabinet_id
    return None

# ============================================================
# GET CABINETS FROM RABBITMQ
# ============================================================

def get_all_cabinet_queues(broker_ip):
    if not broker_ip:
        return []
    try:
        res = requests.get(get_rabbit_api(broker_ip), timeout=5)
        res.raise_for_status()
        data = res.json()
        found = []
        for q in data:
            name = q.get("name", "")
            if not name.startswith("mqtt-subscription-"):
                continue
            cabinet_id = extract_cabinet_id_from_queue(name)
            if cabinet_id:
                found.append((name, cabinet_id))
        return found
    except Exception as e:
        print("[WARN] RabbitMQ queue list fail:", e)
        return []

def delete_rabbit_queue(broker_ip, queue_name):
    try:
        import urllib.parse
        encoded = urllib.parse.quote(queue_name, safe='')
        url = f"http://guest:guest@{broker_ip}:15672/api/queues/%2F/{encoded}"
        r = requests.delete(url, timeout=5)
        if r.status_code in (200, 204):
            print(f"[DELETE] Queue deleted: {queue_name}")
            return True
        else:
            print(f"[DELETE] Failed {queue_name}: HTTP {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"[DELETE] Error deleting {queue_name}: {e}")
        return False

def delete_old_cabinet_queues(old_cabinets, new_hostname, broker_ip):
    if not broker_ip or not old_cabinets:
        return [], []

    suffix_re = re.compile(r"CAB\d+([A-Za-z]*)$", re.IGNORECASE)

    expected_new_ids = set()
    for cab in old_cabinets:
        m = suffix_re.search(cab)
        suffix = m.group(1) if m else ""
        expected_new_ids.add((new_hostname + suffix).upper())

    deleted = []
    failed  = []

    all_queues = get_all_cabinet_queues(broker_ip)

    try:
        res = requests.get(get_rabbit_api(broker_ip), timeout=5)
        all_raw = res.json()
    except:
        all_raw = []

    for queue_name, cabinet_id in all_queues:
        cab_upper = cabinet_id.upper()
        if cab_upper not in expected_new_ids:
            ok = delete_rabbit_queue(broker_ip, queue_name)
            (deleted if ok else failed).append(queue_name)

    old_ids_upper = {c.upper() for c in old_cabinets}
    for q in all_raw:
        qname = q.get("name", "")
        if qname.upper() in old_ids_upper:
            ok = delete_rabbit_queue(broker_ip, qname)
            (deleted if ok else failed).append(qname)

    return deleted, failed

def get_cabinets(broker_ip):
    if not broker_ip:
        print("[WARN] No broker IP configured")
        return []
    try:
        res = requests.get(get_rabbit_api(broker_ip), timeout=5)
        res.raise_for_status()
        data = res.json()

        cabinet_ids = set()

        for q in data:
            name = q.get("name", "")
            if not name.startswith("mqtt-subscription-"):
                continue
            cabinet_id = extract_cabinet_id_from_queue(name)
            if cabinet_id:
                cabinet_ids.add(cabinet_id)
                print(f"[RABBIT] Found cabinet: {name} -> {cabinet_id}")

        out = sorted(cabinet_ids)
        print(f"[RABBIT] Total cabinets found: {len(out)} -> {out}")
        return out

    except Exception as e:
        print("[WARN] RabbitMQ read fail:", e)
        return []

# ============================================================
# RPi CONFIG PROXY
#
# NOTE on "version": rpi_config_get() below simply forwards the full
# JSON body returned by the RPi's GET /api/config (apps.py) as-is,
# via resp.json() -> jsonify(data). Since apps.py's /api/config now
# includes a "version" field (read from config.json's "version" key),
# it is automatically included here too - no extra code needed on
# this side to surface it to module.html.
# ============================================================

def cabinet_id_to_rpi_ip(cabinet_id):
    m = re.search(r"CAB(\d+)([A-Za-z]?)$", cabinet_id, re.IGNORECASE)
    if not m:
        return None
    number     = int(m.group(1))
    suffix     = m.group(2).upper()
    letter_val = (ord(suffix) - ord('A') + 1) if suffix else 1
    last_octet = number * 100 + letter_val
    return f"192.168.137.{last_octet}"

def send_to_rpi(cabinet_id, payload: dict):
    ip = cabinet_id_to_rpi_ip(cabinet_id)
    if not ip:
        return False, f"Unable to resolve IP address for '{cabinet_id}'"
    try:
        resp = requests.post(f"http://{ip}:8082/api/config", json=payload, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            return True, "OK"
        return False, data.get("message", "Unknown error")
    except requests.exceptions.ConnectionError:
        return False, f"RPi {ip} Unreachable (port 8082)"
    except requests.exceptions.Timeout:
        return False, f"RPi {ip} timeout"
    except Exception as e:
        return False, str(e)

def reboot_rpi(cabinet_id):
    ip = cabinet_id_to_rpi_ip(cabinet_id)
    if not ip:
        return False, f"Unable to resolve IP address for '{cabinet_id}'"
    try:
        resp = requests.post(f"http://{ip}:8082/api/reboot", json={}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            return True, "Reboot command sent"
        return False, data.get("message", "Unknown error")
    except requests.exceptions.ConnectionError:
        return False, f"RPi {ip} Unreachable"
    except requests.exceptions.Timeout:
        return False, f"RPi {ip} timeout"
    except Exception as e:
        return False, str(e)

@app.route('/rpi/config/<cabinet_id>', methods=['GET'])
def rpi_config_get(cabinet_id):
    ip = cabinet_id_to_rpi_ip(cabinet_id)
    if not ip:
        return jsonify({"status": "error", "message": f"Unable to resolve IP address for '{cabinet_id}'"}), 400
    try:
        resp = requests.get(f"http://{ip}:8082/api/config", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        data["_rpi_ip"] = ip
        return jsonify(data)
    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": f" Can not connect to RPi {ip}"}), 503
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "message": f"RPi {ip} timeout"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/rpi/config/<cabinet_id>', methods=['POST'])
def rpi_config_post(cabinet_id):
    ip = cabinet_id_to_rpi_ip(cabinet_id)
    if not ip:
        return jsonify({"status": "error", "message": f"Unable to resolve IP address for '{cabinet_id}'"}), 400
    try:
        body = request.get_json(silent=True) or {}
        resp = requests.post(f"http://{ip}:8082/api/config", json=body, timeout=5)
        resp.raise_for_status()
        return jsonify(resp.json())
    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": f"RPi {ip} Unreachable (port 8082)"}), 503
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "message": f"RPi {ip} timeout"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# FLASK UI
# ============================================================

@app.route('/')
def index():
    reload_config_from_file()
    return render_template(
        'module.html',
        cabinets=get_cabinets(CURRENT_BROKER),
        broker=CURRENT_BROKER,
        windows_ip=LOCAL_IP,
        brokers={}
    )

# ============================================================
# API -- INFO BROKER
# ============================================================

@app.route('/api/broker', methods=['GET'])
def api_broker():
    return jsonify({
        "ip_broker": CURRENT_BROKER,
        "local_ip": LOCAL_IP,
        "locked": False,
        "source": "auto-detect / REST / MQTT-subscriber"
    })

# ============================================================
# API -- LIVE CABINET LIST
# Dipakai module.html untuk polling berkala (setInterval), supaya
# cabinet yang baru muncul di RabbitMQ (mis. MASCAB01C) langsung
# ke-enable di dropdown tanpa perlu reload halaman.
# ============================================================

@app.route('/api/cabinets', methods=['GET'])
def api_cabinets():
    return jsonify({
        "cabinets": get_cabinets(CURRENT_BROKER),
        "broker": CURRENT_BROKER
    })

# ============================================================
# SET BROKER
# ============================================================

@app.route('/set_broker', methods=['POST'])
def set_broker():
    global CURRENT_BROKER

    data = request.get_json(silent=True) or {}
    new_ip = data.get("ip_broker", "").strip()

    if not new_ip:
        return jsonify({"status": "error", "message": "ip_broker is required"}), 400

    if new_ip == CURRENT_BROKER:
        return jsonify({"status": "ok", "message": "Broker already set", "ip_broker": CURRENT_BROKER})

    apply_new_broker(new_ip, publish=True)

    return jsonify({
        "status": "ok",
        "message": f"Broker updated to {new_ip}",
        "ip_broker": CURRENT_BROKER
    })

# ============================================================
# SEND COMMAND -- USE REST API
# ============================================================

@app.route('/send', methods=['POST'])
def send_command():
    data   = request.get_json()
    option = data.get("option")
    value  = data.get("value")
    mac    = data.get("mac", "").strip()

    if not option:
        return jsonify({"status": "error", "message": "option empty"}), 400
    if not mac:
        return jsonify({"status": "error", "message": "cabinet ID empty"}), 400

    # ── reboot ──────────────────────────────────────────────
    if option == "reboot":
        ok, msg = reboot_rpi(mac)
        if ok:
            print(f"[REST] reboot -> {mac} OK")
            return jsonify({"status": "ok"})
        else:
            print(f"[REST] reboot -> {mac} FAIL: {msg}")
            return jsonify({"status": "error", "message": msg}), 500

    # ── ip_broker: broadcast ────────────────────────────────
    if option == "ip_broker":
        new_ip = (value or "").strip()
        if not new_ip:
            return jsonify({"status": "error", "message": "ip_broker value empty"}), 400

        old_broker   = CURRENT_BROKER
        all_cabinets = get_cabinets(old_broker)
        print(f"[BROADCAST] Cabinets from old broker ({old_broker}): {all_cabinets}")

        if not all_cabinets:
            all_cabinets = [mac]
            print(f"[BROADCAST] No cabinets found, fallback to selected: {mac}")

        apply_new_broker(new_ip, publish=False)

        print(f"[BROADCAST] Sending ip_broker={new_ip} to {len(all_cabinets)} cabinet(s): {all_cabinets}")

        payload  = {"ip_broker": new_ip}
        ok_list  = []
        err_list = []

        for cab in all_cabinets:
            ok, msg = send_to_rpi(cab, payload)
            if ok:
                ok_list.append(cab)
                print(f"[BROADCAST] ip_broker -> {cab} OK")
            else:
                err_list.append(f"{cab}: {msg}")
                print(f"[BROADCAST] ip_broker -> {cab} FAIL: {msg}")

        if err_list:
            return jsonify({
                "status": "partial" if ok_list else "error",
                "message": f"Sent to {len(ok_list)}/{len(all_cabinets)} cabinets",
                "ip_broker": new_ip,
                "ok": ok_list,
                "errors": err_list
            }), (207 if ok_list else 500)

        return jsonify({
            "status": "ok",
            "message": f"ip_broker sent to {len(ok_list)} cabinet(s): {', '.join(ok_list)}",
            "ip_broker": new_ip,
            "ok": ok_list
        })

    # ── other options ────────────────────────────────────────
    if option == "hostname":
        payload = {"hostname": str(value).strip()}

    elif option == "location":
        payload = {"location": str(value).strip()}

    elif option == "rfid_filter":
        raw  = (value or "").strip()
        tags = [x.strip().upper() for x in raw.split(",") if x.strip()]
        payload = {"rfid_filter": tags}

    elif option == "scan_timeout":
        try:
            v = int(value)
            if not (12 <= v <= 20):
                return jsonify({"status": "error", "message": "scan_timeout must be between 12 and 20 seconds"}), 400
            payload = {"scan_timeout": v}
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "scan_timeout must be a number (12 - 20)"}), 400

    elif option == "rf_mode":
        v = (value or "").strip().upper()
        if v not in VALID_RF_MODES:
            valid_list = ", ".join(sorted(VALID_RF_MODES))
            return jsonify({"status": "error",
                            "message": f"rf_mode must be one of: {valid_list}"}), 400
        payload = {"rf_mode": v}

    elif option == "power":
        # value arrives as an integer dBm string from module.html's
        # dedicated Power dropdown, e.g. "30". Must be one of the
        # verified dBm values (25-33) that reader.py's POWER_TABLE
        # supports.
        try:
            v = int(value)
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "power must be a number"}), 400
        if v not in VALID_POWER_DBM:
            return jsonify({"status": "error",
                            "message": f"power must be between {POWER_MIN} and {POWER_MAX} dBm"}), 400
        payload = {"power": v}

    elif option == "dim":
        # value arrives as an integer percentage string from
        # module.html's dedicated Dim dropdown, e.g. "30", in steps
        # of 10 (0-100). 0 = brightest, 100 = dimmest. This does NOT
        # trigger a reboot on the RPi side - light.py re-reads
        # config.json's "dim" on every light_on() call.
        try:
            v = int(value)
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "dim must be a number"}), 400
        if v not in VALID_DIM:
            valid_list = ", ".join(str(x) for x in sorted(VALID_DIM))
            return jsonify({"status": "error",
                            "message": f"dim must be one of: {valid_list}"}), 400
        payload = {"dim": v}

    elif option == "antenna":
        # value arrives as a comma-separated list of checked antenna
        # IDs from module.html, e.g. "3" or "1,4" or "1,2,3,4,5,6,7,8" -
        # ANY combination is accepted, not just a fixed preset.
        try:
            ant_list = parse_antenna_value(value)
        except ValueError as e:
            return jsonify({"status": "error", "message": f"antenna: {e}"}), 400
        payload = {"antenna": ant_list}

    else:
        return jsonify({"status": "error", "message": f"Unknown option: {option}"}), 400

    ok, msg = send_to_rpi(mac, payload)
    if ok:
        print(f"[REST] {option} -> {mac} OK")
        return jsonify({"status": "ok"})
    else:
        print(f"[REST] {option} -> {mac} FAIL: {msg}")
        return jsonify({"status": "error", "message": msg}), 500

# ============================================================
# MQTT SUBSCRIBER
# ============================================================

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        print(f"[MQTT SUB] topic={msg.topic} payload={payload}")

        if msg.topic == "in" and "ip_broker" in payload:
            mac_target = payload.get("mac", "")
            if mac_target not in ("all", ""):
                print(f"[MQTT SUB] ip_broker skipped, target mac='{mac_target}' not 'all'")
                return
            new_broker = payload["ip_broker"]
            if new_broker and new_broker != CURRENT_BROKER:
                print(f"[MQTT SUB] ip_broker broadcast received -> {new_broker}")
                apply_new_broker(new_broker, publish=False)

    except Exception as e:
        print("[MQTT SUB] parse error:", e)

def mqtt_subscriber():
    while True:
        try:
            if not CURRENT_BROKER:
                print("[MQTT SUB] No broker configured, retrying in 5s...")
                time.sleep(5)
                continue

            c = mqtt.Client()
            c.on_message = on_message
            c.connect(CURRENT_BROKER, BROKER_PORT, 60)
            c.subscribe("in")
            print(f"[MQTT SUB] Connected to {CURRENT_BROKER}, listening ip_broker broadcast only")
            c.loop_forever()

        except Exception as e:
            print("[MQTT SUB] reconnect in 5s:", e)
            time.sleep(5)

threading.Thread(target=mqtt_subscriber, daemon=True).start()

# ============================================================
# CONFIG WATCHER
# ============================================================

def reload_config_from_file():
    global CURRENT_BROKER, config
    try:
        new_cfg = load_config()
        new_ip = new_cfg.get("ip_broker", "").strip()
        if new_ip and new_ip != CURRENT_BROKER:
            print(f"[CONFIG RELOAD] ip_broker changed: {CURRENT_BROKER} -> {new_ip}")
            CURRENT_BROKER = new_ip
            config = new_cfg
    except Exception as e:
        print(f"[CONFIG RELOAD] Error: {e}")

# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    print("=" * 50)
    print(f"[START] Broker      : {CURRENT_BROKER or 'NOT CONFIGURED'}")
    print(f"[START] Local IP    : {LOCAL_IP}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5100, debug=False, use_reloader=False)
