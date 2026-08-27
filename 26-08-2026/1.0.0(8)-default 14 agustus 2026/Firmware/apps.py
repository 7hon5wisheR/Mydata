# -*- coding: utf-8 -*-
__version__ = "0.0.0.1"

# =====================================================================================================================================
#      APPS
#      Name                       : APPS
#      Version                    : 0.0.0.1 
#      Date Created               : 20-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================

from flask import Flask, render_template, request, jsonify
import subprocess, re, os, socket, json, threading, time, uuid, sys
import paho.mqtt.client as mqtt
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

import requests
from requests.auth import HTTPBasicAuth

# ======================================================
# RABBITMQ CONFIG
# ======================================================
RABBIT_USER  = "guest"
RABBIT_PASS  = "guest"
RABBIT_PORT  = 15672
# TTL untuk queue RabbitMQ = 3 hari (3 * 24 * 60 * 60 * 1000 ms)
QUEUE_TTL_MS = 259_200_000

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# ======================================================
# PATHS & CONFIG
# ======================================================
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
APPS_JSON_PATH   = os.path.join(BASE_DIR, "config.json")
CONF_DIR         = os.path.join(BASE_DIR, "conf.d")
BRIDGE_CONF_PATH = os.path.join(CONF_DIR, "bridge.conf")
CONFIG_FILE      = APPS_JSON_PATH
STATUS_JSON_PATH = os.path.join(BASE_DIR, "status.json")

# ======================================================
# HELPER FUNCTIONS
# ======================================================
def get_eth0_mac():
    try:
        with open("/sys/class/net/eth0/address") as f:
            return f.read().strip().lower()
    except Exception:
        return "00:00:00:00:00:00"

def get_mac_address(ifname="eth0"):
    try:
        with open(f"/sys/class/net/{ifname}/address") as f:
            return f.read().strip().lower()
    except Exception:
        mac = uuid.getnode()
        return ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(0, 48, 8))[::-1]

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_gateway_ip():
    try:
        out = subprocess.check_output("ip route", shell=True, text=True)
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def get_mac_from_arp(ip):
    try:
        subprocess.run(f"ping -c 1 {ip}", shell=True, stdout=subprocess.DEVNULL)
        out = subprocess.check_output("arp -a", shell=True, text=True)
        for line in out.splitlines():
            if ip in line:
                m = re.search(r"(([0-9a-f]{2}:){5}[0-9a-f]{2})", line.lower())
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None

def get_windows_gateway_mac():
    gw = get_gateway_ip()
    if gw:
        mac = get_mac_from_arp(gw)
        if mac:
            return mac
    return get_eth0_mac()

WINDOWS_MAC       = get_windows_gateway_mac().lower()
DEVICE_MAC        = socket.gethostname()
MQTT_TOPIC_CONFIG = "in"
TOPIC_CONFIG      = f"{DEVICE_MAC}/in"

# ======================================================
# RF MODE VALIDATION
# Must match reader.py's RF_MODE_TABLE keys exactly - a wrong
# rf_mode written to config.json would make reader.py refuse to
# start (InvalidRfModeError), so we reject bad values here first.
# ======================================================
VALID_RF_MODES = {"CB", "6F", "DC", "65", "2D", "73", "70", "67", "69", "6B", "71"}

# ======================================================
# TX POWER (dBm) VALIDATION
# Must match reader.py's POWER_TABLE keys exactly (25-33 dBm) - a
# power value written to config.json outside this range would make
# reader.py refuse to start (InvalidPowerError), so we reject bad
# values here first.
# ======================================================
POWER_MIN = 25
POWER_MAX = 33
VALID_POWER_DBM = set(range(POWER_MIN, POWER_MAX + 1))

# ======================================================
# LIGHT DIM (brightness level, %) VALIDATION
# Must match light.py's expectations for config.json's "dim" field:
# 0 = brightest, 100 = dimmest, offered in steps of 10. Unlike
# rf_mode/power/antenna, light.py's _get_configured_brightness()
# re-reads config.json's "dim" value on EVERY light_on() call rather
# than caching it at process startup - so writing a new "dim" value
# here must NEVER trigger a reboot. The next light_on() call simply
# picks up the new value on its own.
# ======================================================
DIM_MIN  = 0
DIM_MAX  = 100
DIM_STEP = 10
VALID_DIM = set(range(DIM_MIN, DIM_MAX + 1, DIM_STEP))

def normalize_dim(raw):
    """
    raw: an int or numeric string, e.g. 30, "30".

    Returns the validated int (0-100, step 10) on success, or None if
    raw is missing, non-numeric, or not one of VALID_DIM.
    """
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return None
    if v not in VALID_DIM:
        return None
    return v

# ======================================================
# ANTENNA VALIDATION
# reader.py's get_antenna_command() / build_enable_ant_command() accept
# ANY combination of individual antenna IDs (single antennas alone, e.g.
# just "4", or arbitrary multi-antenna combos, e.g. "1,3") - it is not
# restricted to a fixed handful of presets. This side mirrors that: we
# do not restrict to a small VALID_ANTENNA_COMBOS whitelist, we just
# validate that whatever antenna IDs were sent are well-formed and
# within the physical antenna range of this reader.
#
# ANTENNA_MIN/MAX reflect the physical antenna ports this hardware
# exposes. This deployment's reader is the Silion 8-port E710 module,
# which physically exposes 8 antenna ports, so validation here is
# scoped to 1-8. reader.py itself can technically handle antenna IDs
# up to 32 per the protocol doc, but 8 is the correct ceiling for THIS
# hardware - update ANTENNA_MAX here (and in module.py / module.html on
# the Windows side) together if the hardware ever changes.
# ======================================================
ANTENNA_MIN = 1
ANTENNA_MAX = 8

def normalize_antenna_list(raw):
    """
    raw: a list of antenna IDs (e.g. ["1","4"], ["4"], ["1","2","3"]) or a
    comma-separated string (e.g. "1,4", "4", "1,2,3").

    Accepts ANY combination of antenna IDs within ANTENNA_MIN..ANTENNA_MAX
    - a single antenna alone (e.g. just "4"), two together (e.g. "1,3"),
    three, all eight, in any order - not limited to a fixed preset list.

    Returns the canonical, deduplicated, sorted list of string IDs (e.g.
    ["1", "4"]) on success, or None if raw is empty, malformed, or
    contains an antenna ID outside the valid range.
    """
    try:
        if isinstance(raw, str):
            items = [x.strip() for x in raw.split(",") if x.strip()]
        elif isinstance(raw, list):
            items = [str(x).strip() for x in raw if str(x).strip()]
        else:
            return None
        if not items:
            return None

        ant_ints = []
        for x in items:
            if not x.isdigit():
                return None
            n = int(x)
            if not (ANTENNA_MIN <= n <= ANTENNA_MAX):
                return None
            ant_ints.append(n)
    except (ValueError, TypeError):
        return None

    ant_ints = sorted(set(ant_ints))
    return [str(n) for n in ant_ints]

# ======================================================
# ADVANCED RF TUNING - LOCAL-ONLY (index.html @ :8082)
# inventory_mode, auto_tune_passes, auto_tune_quiet_cycles,
# rssi_threshold, rssi_threshold_strict, min_reads_per_scan.
#
# Unlike every other field above, these 6 are intentionally NOT part
# of ALLOWED_FIELDS in api_config() and are NOT handled anywhere in
# the MQTT on_message() listener. They can ONLY be changed by
# submitting the "Advanced RF Tuning" form on THIS device's own web
# page (http://<device-ip>:8082/), handled directly inside index()
# below - never over REST (/api/config) or MQTT ("in" topic).
# ======================================================
INVENTORY_MODE_MIN_LEN = 2
INVENTORY_MODE_MAX_LEN = 16

AUTO_TUNE_PASSES_MIN = 1
AUTO_TUNE_PASSES_MAX = 10

AUTO_TUNE_QUIET_CYCLES_MIN = 0.5
AUTO_TUNE_QUIET_CYCLES_MAX = 10.0

RSSI_THRESHOLD_MIN = -95
RSSI_THRESHOLD_MAX = -30

# rssi_threshold_strict: reader.py's same-cycle ghost-reject threshold
# (used only once the cycle's own tag count is already below
# rssi_threshold_strict_count - see reader.py). Same valid dBm range
# as the normal rssi_threshold above; it's a separate, typically
# tighter (higher/less-negative) value, not enforced to be stricter
# than rssi_threshold here - that relationship is the operator's call
# to tune per deployment.
RSSI_THRESHOLD_STRICT_MIN = -95
RSSI_THRESHOLD_STRICT_MAX = -30

MIN_READS_PER_SCAN_MIN = 1
MIN_READS_PER_SCAN_MAX = 20

def normalize_inventory_mode(raw):
    """
    raw: e.g. "AA48". Uppercased hex-style mode code, 2-16 chars.
    Returns the normalized string on success, or None if raw is
    missing, empty, too long/short, or contains non-hex characters.
    """
    if raw is None:
        return None
    v = str(raw).strip().upper()
    if not (INVENTORY_MODE_MIN_LEN <= len(v) <= INVENTORY_MODE_MAX_LEN):
        return None
    if not re.match(r'^[0-9A-F]+$', v):
        return None
    return v

def normalize_auto_tune_passes(raw):
    """raw: int or numeric string. Returns validated int or None."""
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return None
    if not (AUTO_TUNE_PASSES_MIN <= v <= AUTO_TUNE_PASSES_MAX):
        return None
    return v

def normalize_auto_tune_quiet_cycles(raw):
    """raw: float/int or numeric string. Returns validated float or None."""
    try:
        v = float(raw)
    except (ValueError, TypeError):
        return None
    if not (AUTO_TUNE_QUIET_CYCLES_MIN <= v <= AUTO_TUNE_QUIET_CYCLES_MAX):
        return None
    return v

def normalize_rssi_threshold(raw):
    """raw: int or numeric string (dBm, negative). Returns validated int or None."""
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return None
    if not (RSSI_THRESHOLD_MIN <= v <= RSSI_THRESHOLD_MAX):
        return None
    return v

def normalize_rssi_threshold_strict(raw):
    """raw: int or numeric string (dBm, negative). Returns validated int or None."""
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return None
    if not (RSSI_THRESHOLD_STRICT_MIN <= v <= RSSI_THRESHOLD_STRICT_MAX):
        return None
    return v

def normalize_min_reads_per_scan(raw):
    """raw: int or numeric string. Returns validated int or None."""
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return None
    if not (MIN_READS_PER_SCAN_MIN <= v <= MIN_READS_PER_SCAN_MAX):
        return None
    return v

# ======================================================
# RFID FILTER WHITELIST VALIDATION
# Only these exact tag-prefix values may be written to config.json's
# "rfid_filter" field via REST/MQTT on THIS side (apps.py / Raspberry
# Pi). module.py (Windows) does NOT enforce this whitelist - it just
# forwards whatever the user types, so this is the single point of
# enforcement.
#
# Every entry in the submitted list must be EXACTLY one of
# RFID_FILTER_WHITELIST (case-insensitive) - e.g. "8600,xxx" is
# rejected as a WHOLE (not partially applied) because "xxx" isn't
# whitelisted, even though "8600" alone would be fine. Valid examples:
# "8600", "E28", "8600,E28", "8600,6453,E28", etc. An empty list/string
# is allowed (clears the filter entirely - no tags filtered by prefix).
# ======================================================
RFID_FILTER_WHITELIST = {"8600", "E28", "6453"}

def normalize_rfid_filter(raw):
    """
    raw: a list of tag prefixes (e.g. ["8600","E28"]) or a
    comma-separated string (e.g. "8600,E28", "8600,6453,E28", "8600").

    Returns the validated, uppercased, deduplicated list on success
    (e.g. ["8600", "E28"]), or None if ANY entry is not exactly one of
    RFID_FILTER_WHITELIST - in which case the caller must reject the
    whole update and leave config.json's existing rfid_filter untouched.
    """
    if isinstance(raw, str):
        items = [x.strip().upper() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, list):
        items = [str(x).strip().upper() for x in raw if str(x).strip()]
    else:
        return None

    for tag in items:
        if tag not in RFID_FILTER_WHITELIST:
            return None

    # dedupe while preserving first-seen order
    seen = []
    for tag in items:
        if tag not in seen:
            seen.append(tag)
    return seen

# ======================================================
# STATUS.JSON HELPER
# ======================================================
def read_status_json():
    try:
        if os.path.exists(STATUS_JSON_PATH):
            with open(STATUS_JSON_PATH) as f:
                return json.load(f)
    except Exception as e:
        print(f"[STATUS] Failed to read status.json: {e}")
    return {}

# ======================================================
# AUTO-DETECT RABBITMQ IP
# ======================================================
def check_rabbitmq_port(ip, port=15672, timeout=1):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def auto_detect_windows_ip():
    print("[AUTO-DETECT] Starting RabbitMQ IP detection...")

    gateway = get_gateway_ip()
    if gateway:
        print(f"[AUTO-DETECT] Checking gateway: {gateway}")
        if check_rabbitmq_port(gateway):
            print(f"[AUTO-DETECT] RabbitMQ found at gateway: {gateway}")
            return gateway

    local_ip      = get_local_ip()
    subnet_prefix = ".".join(local_ip.split(".")[:3])

    subnets_to_scan = ["192.168.3"]
    if subnet_prefix not in subnets_to_scan:
        subnets_to_scan.insert(0, subnet_prefix)

    for subnet in subnets_to_scan:
        print(f"[AUTO-DETECT] Scanning subnet {subnet}.x...")
        priority_ips = [89, 1, 100, 254]

        for last_octet in priority_ips:
            ip = f"{subnet}.{last_octet}"
            if check_rabbitmq_port(ip):
                print(f"[AUTO-DETECT] RabbitMQ found at: {ip}")
                return ip

        for last_octet in range(1, 255):
            if last_octet in priority_ips:
                continue
            ip = f"{subnet}.{last_octet}"
            if check_rabbitmq_port(ip):
                print(f"[AUTO-DETECT] RabbitMQ found at: {ip}")
                return ip

    print("[AUTO-DETECT] RabbitMQ server not found")
    return None

# ======================================================
# CONFIG FILE
# ======================================================
def read_apps_json():
    if not os.path.exists(APPS_JSON_PATH):
        return {"ttl": 259200000, "ip_broker": ""}
    try:
        with open(APPS_JSON_PATH) as f:
            return json.load(f)
    except Exception:
        return {"ttl": 259200000, "ip_broker": ""}

def write_apps_json(data):
    existing = {}
    if os.path.exists(APPS_JSON_PATH):
        try:
            with open(APPS_JSON_PATH) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.update(data)
    with open(APPS_JSON_PATH, "w") as f:
        json.dump(existing, f, indent=4)

def load_config():
    default = {
        "location": "", "consstring": "", "hostname": socket.gethostname(),
        # Advanced RF tuning defaults - local-only, editable exclusively
        # via the index.html form at :8082 (see index() below).
        "inventory_mode":         "AA48",
        "auto_tune_passes":       3,
        "auto_tune_quiet_cycles": 2.5,
        "rssi_threshold":         -58,
        "rssi_threshold_strict":  -58,
        "min_reads_per_scan":     2,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                default.update(json.load(f))
        except Exception:
            pass
    return default

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)
    print("[CONFIG] config.json updated")

# ======================================================
# RF MODE HELPER
# -- Track the last known rf_mode to avoid repeated reboots
#    when the value has not actually changed. reader.py only
#    picks up rf_mode at process start, so a reboot is required
#    for a new rf_mode to take effect.
# ======================================================
_last_rf_mode_state = None   # e.g. "DC", "CB", ...

def apply_rf_mode_change(val, source="unknown"):
    """
    Apply an rf_mode change:
      Reboot the RPi so reader.py re-reads config.json and picks up
      the new rf_mode (get_rf_mode_command() is only called at startup).

    val    : one of VALID_RF_MODES (already validated by the caller)
    source : "MQTT" / "REST" (used for log messages only)

    Note:
      save_config() must be called BEFORE this function so that the
      reader picks up the correct value from config.json after reboot.
    """
    global _last_rf_mode_state

    val = val.strip().upper()
    if val not in VALID_RF_MODES:
        print(f"[RF_MODE] apply_rf_mode_change: invalid value '{val}'")
        return

    if val == _last_rf_mode_state:
        print(f"[RF_MODE] rf_mode is already '{val}', no change - skipping reboot")
        return

    _last_rf_mode_state = val

    print(f"[RF_MODE] rf_mode changed to '{val}' via {source} -> reboot")

    threading.Thread(target=reboot_after_delay, args=(3,), daemon=True).start()

# ======================================================
# TX POWER (dBm) HELPER
# -- Track the last known power (dBm) to avoid repeated reboots
#    when the value has not actually changed. reader.py only
#    picks up "power" at process start (get_power_command() is
#    only called at module load time), so a reboot is required
#    for a new power level to take effect.
# ======================================================
_last_power_state = None   # e.g. 30, 25, ...

def apply_power_change(val, source="unknown"):
    """
    Apply a TX power (dBm) change:
      Reboot the RPi so reader.py re-reads config.json and picks up
      the new power level (get_power_command() is only called at
      startup).

    val    : an int dBm value already validated against VALID_POWER_DBM
             by the caller
    source : "MQTT" / "REST" (used for log messages only)

    Note:
      save_config() must be called BEFORE this function so that the
      reader picks up the correct value from config.json after reboot.
    """
    global _last_power_state

    try:
        val = int(val)
    except (ValueError, TypeError):
        print(f"[POWER] apply_power_change: invalid value '{val}'")
        return

    if val not in VALID_POWER_DBM:
        print(f"[POWER] apply_power_change: invalid value '{val}'")
        return

    if val == _last_power_state:
        print(f"[POWER] power is already {val}dBm, no change - skipping reboot")
        return

    _last_power_state = val

    print(f"[POWER] power changed to {val}dBm via {source} -> reboot")

    threading.Thread(target=reboot_after_delay, args=(3,), daemon=True).start()

# ======================================================
# ANTENNA HELPER
# -- Track the last known antenna set to avoid repeated reboots
#    when the value has not actually changed. reader.py only
#    picks up "antenna" at process start (CMD_ENABLE_ANT is built
#    once at module load time), so a reboot is required for a new
#    antenna set to take effect.
# ======================================================
_last_antenna_state = None   # tuple of sorted antenna ints, e.g. (1,2,3,4)

def apply_antenna_change(val_list, source="unknown"):
    """
    Apply an antenna change:
      Reboot the RPi so reader.py re-reads config.json and picks up
      the new antenna set (get_antenna_command() is only called at startup).

    val_list : canonical list of antenna ID strings, e.g. ["1","4"]
               (already validated/normalized by the caller via
               normalize_antenna_list()) - can be ANY combination, not
               just a fixed preset.
    source   : "MQTT" / "REST" (used for log messages only)

    Note:
      save_config() must be called BEFORE this function so that the
      reader picks up the correct value from config.json after reboot.
    """
    global _last_antenna_state

    try:
        ant_tuple = tuple(sorted(int(x) for x in val_list))
    except (ValueError, TypeError):
        print(f"[ANTENNA] apply_antenna_change: invalid value '{val_list}'")
        return

    if ant_tuple == _last_antenna_state:
        print(f"[ANTENNA] antenna is already {list(ant_tuple)}, no change - skipping reboot")
        return

    _last_antenna_state = ant_tuple

    print(f"[ANTENNA] antenna changed to {list(ant_tuple)} via {source} -> reboot")

    threading.Thread(target=reboot_after_delay, args=(3,), daemon=True).start()

# ======================================================
# LIGHT DIM (brightness) HELPER
# -- Unlike rf_mode/power/antenna, dim does NOT need a
#    reboot: light.py's _get_configured_brightness() re-reads
#    config.json's "dim" value on every single light_on() call
#    (see light.py), so as soon as save_config() has written the
#    new value, the next time the lamp is turned on it will already
#    use the new brightness. No process restart required.
#    This helper only exists for consistent logging - it does NOT
#    spawn a reboot thread.
# ======================================================
_last_dim_state = None   # e.g. 0, 30, 100 ...

def apply_dim_change(val, source="unknown"):
    """
    Log a dim (brightness) change. Does NOT reboot - light.py picks up
    config.json's "dim" value live, on the next light_on() call.

    val    : an int 0-100 (step 10), already validated by the caller
             against VALID_DIM
    source : "MQTT" / "REST" (used for log messages only)
    """
    global _last_dim_state

    try:
        val = int(val)
    except (ValueError, TypeError):
        print(f"[DIM] apply_dim_change: invalid value '{val}'")
        return

    if val not in VALID_DIM:
        print(f"[DIM] apply_dim_change: invalid value '{val}'")
        return

    if val == _last_dim_state:
        print(f"[DIM] dim is already {val}%, no change")
        return

    _last_dim_state = val

    print(f"[DIM] dim changed to {val}% via {source} -> no reboot needed "
          f"(light.py reads config.json 'dim' live on next light_on())")

# ======================================================
# HOSTNAME CHANGE DETECTION
# ======================================================
def check_and_update_hostname():
    cfg              = load_config()
    current_hostname = socket.gethostname()
    saved_hostname   = cfg.get("hostname", "")

    if current_hostname != saved_hostname:
        print(f"[HOSTNAME] Changed: {saved_hostname} -> {current_hostname}")
        cfg["hostname"] = current_hostname
        save_config(cfg)

        data      = read_apps_json()
        broker_ip = data.get("ip_broker", "")
        if broker_ip:
            try:
                update_bridge_conf(broker_ip)
                ensure_queue_and_binding()
                publish_ip_broker(broker_ip)
                print("[HOSTNAME] Bridge updated successfully")
            except Exception as e:
                print(f"[HOSTNAME] Bridge update failed: {e}")

        return (True, saved_hostname, current_hostname)

    return (False, saved_hostname, current_hostname)

# ======================================================
# SYSTEM
# ======================================================
def apply_system_hostname(new_hostname):
    if not new_hostname or new_hostname == socket.gethostname():
        return False
    try:
        subprocess.run(
            ["sudo", "bash", "-c",
             f"echo '{new_hostname}' > /etc/hostname && "
             f"sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1\t{new_hostname}/' /etc/hosts && "
             f"hostname '{new_hostname}'"],
            check=True
        )
        print("[SYSTEM] Hostname updated:", new_hostname)
        return True
    except Exception as e:
        print("[ERROR] Hostname update failed:", e)
        return False


def reboot_after_delay(delay=3):
    time.sleep(delay)

    try:
        st         = read_status_json()
        session_id = st.get("sessionId", "")
        cfg_data   = read_apps_json()
        broker_ip  = cfg_data.get("ip_broker", "") or get_local_ip()

        payload = json.dumps({
            "cmd":       "REBOOT",
            "sessionId": session_id,
            "mac":       DEVICE_MAC,
        })

        c = mqtt.Client()
        c.connect(broker_ip, 1883, 10)
        c.publish("in", payload, qos=1, retain=False)
        c.loop(timeout=1.0)
        c.disconnect()
        print(f"[REBOOT] Published to broker={broker_ip} topic='in': {payload}")

    except Exception as e:
        print(f"[REBOOT] Failed to publish reboot to 'in': {e}")

    print("[REBOOT] Waiting 5s for mqtts.py to handle reboot...")
    time.sleep(5)
    print("[REBOOT] Safety fallback: executing sudo reboot -f ...")
    subprocess.run(["sudo", "reboot", "-f"])


# ======================================================
# MOSQUITTO
# ======================================================
def is_broker_online(ip, port=1883):
    try:
        c = mqtt.Client()
        c.connect(ip, port, 3)
        c.disconnect()
        return True
    except Exception:
        return False

def restart_mosquitto_service():
    subprocess.run(["sudo", "systemctl", "restart", "mosquitto"])

def update_bridge_conf(mosquitto_address, port="1883"):
    mac_clean = DEVICE_MAC.replace(":", "")
    conf = f"""
connection {mac_clean}
address {mosquitto_address}:{port}
clientid {mac_clean}
topic {DEVICE_MAC}/# both 0
topic in in 0
topic out out 0
topic config both 0
topic session both 0
topic reboot both 0
remote_username guest
remote_password guest
try_private false
cleansession false
bridge_protocol_version mqttv311
log_type error
""".strip()

    os.makedirs(CONF_DIR, exist_ok=True)
    with open(BRIDGE_CONF_PATH, "w") as f:
        f.write(conf + "\n")

    restart_mosquitto_service()

def publish_ip_broker(ip):
    try:
        c = mqtt.Client()
        c.connect("127.0.0.1", 1883, 60)
        c.publish(MQTT_TOPIC_CONFIG, json.dumps({"ip_broker": ip, "mac": "all"}))
        c.loop()
        time.sleep(0.2)
        c.disconnect()
    except Exception as e:
        print("[MQTT PUBLISH ERROR]", e)

# ======================================================
# APPLY BROKER
# ======================================================
def apply_broker(new_ip, publish=True, force=False):
    data   = read_apps_json()
    old_ip = data.get("ip_broker", "")

    if not force and new_ip == old_ip:
        print(f"[BROKER] ip_broker unchanged ({new_ip}), skipping apply.")
        return

    print(f"[BROKER] Applying new broker: {old_ip} -> {new_ip} (force={force})")

    write_apps_json({"ip_broker": new_ip})

    try:
        update_bridge_conf(new_ip)
        print(f"[BROKER] bridge.conf updated -> {new_ip}")
    except Exception as e:
        print(f"[BROKER] bridge.conf update failed: {e}")

    try:
        ensure_queue_and_binding()
        print(f"[BROKER] RabbitMQ queue/binding ensured")
    except Exception as e:
        print(f"[BROKER] RabbitMQ setup failed: {e}")

    if publish:
        try:
            publish_ip_broker(new_ip)
            print(f"[BROKER] ip_broker broadcast sent -> {new_ip}")
        except Exception as e:
            print(f"[BROKER] Broadcast failed: {e}")

# ======================================================
# RABBITMQ
# ======================================================
def get_windows_queue_name():
    gw_ip = get_gateway_ip()
    if gw_ip:
        try:
            out = subprocess.check_output(
                ["nbtscan", gw_ip], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                line = line.strip()
                if line.startswith(gw_ip):
                    parts = line.split()
                    if len(parts) >= 2:
                        hostname = re.sub(r"[^a-z0-9._-]", "_", parts[1].lower())
                        print("[WINDOWS HOSTNAME via nbtscan]", hostname)
                        return hostname
        except Exception as e:
            print("[NBTSCAN ERROR]", e)

    cfg      = load_config()
    location = cfg.get("location", "").strip()
    print("[FALLBACK -> location]", location)
    return location.lower()

def get_rabbit_api_base():
    ip = read_apps_json().get("ip_broker")
    return f"http://{ip}:{RABBIT_PORT}/api" if ip else None

def ensure_queue_and_binding():
    base = get_rabbit_api_base()
    if not base:
        return False

    windows_mac = get_windows_queue_name()
    device_mac  = DEVICE_MAC

    try:
        requests.put(
            f"{base}/queues/%2F/{windows_mac}",
            auth=HTTPBasicAuth(RABBIT_USER, RABBIT_PASS),
            json={"durable": True, "auto_delete": False,
                  "arguments": {"x-message-ttl": QUEUE_TTL_MS}},
            timeout=3,
        )
        for routing_key in [f"{windows_mac}.#", f"{device_mac}.#", "in", "out"]:
            requests.post(
                f"{base}/bindings/%2F/e/amq.topic/q/{windows_mac}",
                auth=HTTPBasicAuth(RABBIT_USER, RABBIT_PASS),
                json={"routing_key": routing_key, "arguments": {}},
                timeout=3,
            )
        print(f"[RABBIT OK] queue={windows_mac}")
        return True
    except Exception as e:
        print("[RABBIT ERROR]", e)
        return False

# ======================================================
# AUTO SET AFTER DELAY
# ======================================================
def auto_set_after_delay(delay=300):
    print(f"[AUTO-SET] Waiting {delay}s before auto-set...")
    time.sleep(delay)

    data      = read_apps_json()
    broker_ip = data.get("ip_broker", "")

    if not broker_ip:
        broker_ip = auto_detect_windows_ip()
        if broker_ip:
            apply_broker(broker_ip)
        else:
            print("[AUTO-SET] Auto-detect failed, skipping.")
            return
    else:
        apply_broker(broker_ip, publish=False)

    print(f"[AUTO-SET] Done. queue={get_windows_queue_name()}")

# ======================================================
# MQTT LISTENER
# ======================================================
def on_connect(client, userdata, flags, rc):
    client.subscribe(MQTT_TOPIC_CONFIG)

def on_message(client, userdata, msg):
    # NOTE: "inventory_mode", "auto_tune_passes", "auto_tune_quiet_cycles",
    # "rssi_threshold", "rssi_threshold_strict" and "min_reads_per_scan"
    # are intentionally NEVER handled in this listener - they are
    # local-only settings, editable exclusively via the "Advanced RF
    # Tuning" form on index.html @ :8082.
    try:
        payload       = json.loads(msg.payload.decode())
        device_in_msg = payload.get("mac", "")

        if "ip_broker" in payload:
            if device_in_msg in ("all", ""):
                new_ip = payload["ip_broker"]
                threading.Thread(
                    target=apply_broker, args=(new_ip, False), daemon=True
                ).start()
            if len(payload) == 2 and "mac" in payload:
                return

        if device_in_msg and device_in_msg not in ("all", DEVICE_MAC):
            return

        cfg              = load_config()
        updated          = False
        location_changed = False

        for k in ("location", "consstring"):
            if k in payload:
                if k == "location" and cfg.get("location") != payload[k]:
                    location_changed = True
                cfg[k]  = payload[k]
                updated = True

        if "hostname" in payload:
            cfg["hostname"] = payload["hostname"]
            if apply_system_hostname(cfg["hostname"]):
                threading.Thread(target=reboot_after_delay, daemon=True).start()
            updated = True

        if "rfid_filter" in payload:
            normalized = normalize_rfid_filter(payload["rfid_filter"])
            if normalized is not None:
                cfg["rfid_filter"] = normalized
                updated = True
            else:
                print(f"[MQTT] rfid_filter={payload['rfid_filter']!r} invalid - only "
                      f"{sorted(RFID_FILTER_WHITELIST)} are allowed, entire update rejected")

        if "scan_timeout" in payload:
            try:
                v = int(payload["scan_timeout"])
                if 12 <= v <= 20:
                    cfg["scan_timeout"] = v
                    updated = True
                else:
                    print(f"[MQTT] scan_timeout={v} out of range (12-20), ignored")
            except (ValueError, TypeError):
                pass

        # scan_newtag is intentionally NOT exposed via GET /api/config
        # (see api_config below) but is still accepted here on write,
        # within the 7-15 second range.
        if "scan_newtag" in payload:
            try:
                v = int(payload["scan_newtag"])
                if 7 <= v <= 15:
                    cfg["scan_newtag"] = v
                    updated = True
                else:
                    print(f"[MQTT] scan_newtag={v} out of range (7-15), ignored")
            except (ValueError, TypeError):
                pass

        rf_mode_val = None
        if "rf_mode" in payload:
            v = str(payload["rf_mode"]).strip().upper()
            if v in VALID_RF_MODES:
                cfg["rf_mode"] = v
                rf_mode_val    = v
                updated = True
            else:
                print(f"[MQTT] rf_mode='{v}' invalid, ignored. Valid: {', '.join(sorted(VALID_RF_MODES))}")

        power_val = None
        if "power" in payload:
            try:
                v = int(payload["power"])
                if v in VALID_POWER_DBM:
                    cfg["power"] = v
                    power_val    = v
                    updated = True
                else:
                    print(f"[MQTT] power={v} out of range ({POWER_MIN}-{POWER_MAX}), ignored")
            except (ValueError, TypeError):
                print(f"[MQTT] power={payload['power']!r} invalid (non-numeric), ignored")

        dim_val = None
        if "dim" in payload:
            v = normalize_dim(payload["dim"])
            if v is not None:
                cfg["dim"] = v
                dim_val    = v
                updated = True
            else:
                valid_list = ", ".join(str(x) for x in sorted(VALID_DIM))
                print(f"[MQTT] dim={payload['dim']!r} invalid, ignored. Valid: {valid_list}")

        antenna_val = None
        if "antenna" in payload:
            normalized = normalize_antenna_list(payload["antenna"])
            if normalized is not None:
                cfg["antenna"] = normalized
                antenna_val    = normalized
                updated = True
            else:
                print(f"[MQTT] antenna={payload['antenna']!r} invalid - must be antenna ID(s) "
                      f"between {ANTENNA_MIN} and {ANTENNA_MAX} (e.g. '4', '1,3', '1,2,3,4,5,6,7,8'), ignored")

        if updated:
            save_config(cfg)
            if location_changed:
                ensure_queue_and_binding()
                threading.Thread(target=reboot_after_delay, daemon=True).start()

        # Apply the rf_mode change AFTER save_config() has completed
        # so that reader.py reads the new value from config.json after reboot.
        if rf_mode_val:
            apply_rf_mode_change(rf_mode_val, source="MQTT")

        # Apply the power change AFTER save_config() has completed
        # so that reader.py reads the new value from config.json after reboot.
        if power_val:
            apply_power_change(power_val, source="MQTT")

        # Apply the dim change AFTER save_config() has completed.
        # NOTE: unlike rf_mode/power/antenna, this does NOT
        # reboot - light.py reads config.json's "dim" live on every
        # light_on() call.
        if dim_val is not None:
            apply_dim_change(dim_val, source="MQTT")

        # Apply the antenna change AFTER save_config() has completed
        # so that reader.py reads the new value from config.json after reboot.
        if antenna_val:
            apply_antenna_change(antenna_val, source="MQTT")

        if payload.get("cmd", "").upper() == "REBOOT" and device_in_msg == DEVICE_MAC:
            threading.Thread(target=reboot_after_delay, daemon=True).start()

    except Exception as e:
        print("[MQTT ERROR]", e)

def start_mqtt():
    c = mqtt.Client()
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(get_local_ip(), 1883, 60)
    c.loop_forever()

threading.Thread(target=start_mqtt, daemon=True).start()

# ======================================================
# FLASK ROUTES
# ======================================================
@app.route("/", methods=["GET", "POST"])
def index():
    data = read_apps_json()
    ip   = data.get("ip_broker", "")
    msg  = ""

    hostname_changed, old_hn, new_hn = check_and_update_hostname()
    if hostname_changed:
        msg = f"[AUTO] Hostname changed: {old_hn} -> {new_hn}"

    if not ip:
        detected_ip = auto_detect_windows_ip()
        if detected_ip:
            ip = detected_ip
            apply_broker(ip)
            msg = f"[AUTO] Broker detected: {ip}"
        else:
            ip = request.remote_addr
            apply_broker(ip)
            msg = "[AUTO] Broker set to client IP"

    if request.method == "POST" and request.form.get("form_name") != "advanced_tuning":
        ip = request.form.get("mosquitto_tls_address", "").strip()
        if ip:
            apply_broker(ip, force=True)
            msg = "Broker updated manually"

    # ==================================================
    # ADVANCED RF TUNING - LOCAL-ONLY (this form, this page, this port)
    # inventory_mode / auto_tune_passes / auto_tune_quiet_cycles /
    # rssi_threshold / rssi_threshold_strict / min_reads_per_scan are
    # deliberately NOT writable via /api/config or MQTT (see
    # ALLOWED_FIELDS / on_message above) - this POST branch is the
    # ONLY place in the whole app that can change them.
    # ==================================================
    tuning_msg = ""
    if request.method == "POST" and request.form.get("form_name") == "advanced_tuning":
        cfg_t  = load_config()
        errors = []

        im = normalize_inventory_mode(request.form.get("inventory_mode"))
        if im is None:
            errors.append(f"inventory_mode must be {INVENTORY_MODE_MIN_LEN}-{INVENTORY_MODE_MAX_LEN} hex characters")

        atp = normalize_auto_tune_passes(request.form.get("auto_tune_passes"))
        if atp is None:
            errors.append(f"auto_tune_passes must be between {AUTO_TUNE_PASSES_MIN} and {AUTO_TUNE_PASSES_MAX}")

        atqc = normalize_auto_tune_quiet_cycles(request.form.get("auto_tune_quiet_cycles"))
        if atqc is None:
            errors.append(f"auto_tune_quiet_cycles must be between {AUTO_TUNE_QUIET_CYCLES_MIN} and {AUTO_TUNE_QUIET_CYCLES_MAX}")

        rt = normalize_rssi_threshold(request.form.get("rssi_threshold"))
        if rt is None:
            errors.append(f"rssi_threshold must be between {RSSI_THRESHOLD_MIN} and {RSSI_THRESHOLD_MAX}")

        rts = normalize_rssi_threshold_strict(request.form.get("rssi_threshold_strict"))
        if rts is None:
            errors.append(f"rssi_threshold_strict must be between {RSSI_THRESHOLD_STRICT_MIN} and {RSSI_THRESHOLD_STRICT_MAX}")

        mrps = normalize_min_reads_per_scan(request.form.get("min_reads_per_scan"))
        if mrps is None:
            errors.append(f"min_reads_per_scan must be between {MIN_READS_PER_SCAN_MIN} and {MIN_READS_PER_SCAN_MAX}")

        if errors:
            tuning_msg = "Advanced tuning NOT saved: " + "; ".join(errors)
        else:
            changed = (
                cfg_t.get("inventory_mode")         != im   or
                cfg_t.get("auto_tune_passes")       != atp  or
                cfg_t.get("auto_tune_quiet_cycles") != atqc or
                cfg_t.get("rssi_threshold")         != rt   or
                cfg_t.get("rssi_threshold_strict")  != rts  or
                cfg_t.get("min_reads_per_scan")     != mrps
            )
            cfg_t["inventory_mode"]         = im
            cfg_t["auto_tune_passes"]       = atp
            cfg_t["auto_tune_quiet_cycles"] = atqc
            cfg_t["rssi_threshold"]         = rt
            cfg_t["rssi_threshold_strict"]  = rts
            cfg_t["min_reads_per_scan"]     = mrps
            save_config(cfg_t)
            print(f"[LOCAL TUNING @ 8082] inventory_mode={im} auto_tune_passes={atp} "
                  f"auto_tune_quiet_cycles={atqc} rssi_threshold={rt} "
                  f"rssi_threshold_strict={rts} min_reads_per_scan={mrps}")

            if changed:
                tuning_msg = "Advanced RF tuning updated. Rebooting to apply..."
                threading.Thread(target=reboot_after_delay, args=(3,), daemon=True).start()
            else:
                tuning_msg = "Advanced RF tuning saved (no change)."

    cfg = load_config()

    return render_template(
        "index.html",
        current_mosq=ip,
        mosq_status="ON" if is_broker_online(ip) else "OFF",
        message=msg,
        tuning_message=tuning_msg,
        ttl_value=data.get("ttl", 259200000),
        current_hostname=socket.gethostname(),
        current_version=cfg.get("version", __version__),
        current_inventory_mode=cfg.get("inventory_mode", "AA48"),
        current_auto_tune_passes=cfg.get("auto_tune_passes", 3),
        current_auto_tune_quiet_cycles=cfg.get("auto_tune_quiet_cycles", 2.5),
        current_rssi_threshold=cfg.get("rssi_threshold", -58),
        current_rssi_threshold_strict=cfg.get("rssi_threshold_strict", -58),
        current_min_reads_per_scan=cfg.get("min_reads_per_scan", 2),
    )

@app.route("/api/status", methods=["GET"])
def api_status():
    data = read_apps_json()
    hostname_changed, old_hn, new_hn = check_and_update_hostname()
    return jsonify({
        "hostname":         new_hn,
        "hostname_changed": hostname_changed,
        "old_hostname":     old_hn if hostname_changed else None,
        "broker_ip":        data.get("ip_broker", ""),
        "broker_online":    is_broker_online(data.get("ip_broker", "")),
    })

@app.route("/api/auto-detect", methods=["POST"])
def api_auto_detect():
    detected_ip = auto_detect_windows_ip()
    if detected_ip:
        apply_broker(detected_ip, force=True)
        return jsonify({"success": True, "ip": detected_ip,
                        "message": f"Broker auto-detected: {detected_ip}"})
    return jsonify({"success": False, "message": "RabbitMQ server not found"}), 404

# ======================================================
# API: CONFIG -- GET (read) + POST (write)
# ======================================================
@app.route("/api/config", methods=["GET", "POST"])
def api_config():

    if request.method == "GET":
        cfg  = load_config()
        data = read_apps_json()
        return jsonify({
            "location":     cfg.get("location", ""),
            "hostname":     cfg.get("hostname", ""),
            "rfid_filter":  cfg.get("rfid_filter", []),
            "scan_timeout": cfg.get("scan_timeout", 120),
            # NOTE: "scan_newtag" is intentionally NOT exposed here.
            # It's still a writable field (see ALLOWED_FIELDS / POST
            # handling below and the MQTT handler above), but it's
            # deliberately hidden from the read/info response.
            "rf_mode":      cfg.get("rf_mode", "DC"),
            "power":        cfg.get("power", 30),
            "dim":          cfg.get("dim", 0),
            "antenna":      cfg.get("antenna", ["1", "2", "3", "4"]),
            "ip_broker":    data.get("ip_broker", cfg.get("ip_broker", "")),
            # NOTE: "PORT" (the reader's serial device path, e.g. ttyAMA0)
            # is intentionally NOT exposed here. It's a physical wiring
            # setting, not something that should be readable/changeable
            # over the network UI/API - see ALLOWED_FIELDS below, which
            # also excludes it from writes.
            # Read-only firmware/config schema version, taken straight
            # from config.json's "version" field. Not writable via
            # REST/MQTT - see ALLOWED_FIELDS below, which deliberately
            # excludes "version". Falls back to the module's own
            # __version__ if config.json has no "version" key yet.
            "version":      cfg.get("version", __version__),
            # NOTE: "inventory_mode", "auto_tune_passes",
            # "auto_tune_quiet_cycles", "rssi_threshold",
            # "rssi_threshold_strict" and "min_reads_per_scan" are
            # intentionally NOT exposed here - same treatment as
            # "PORT". They are local-only settings, readable/writable
            # exclusively via the "Advanced RF Tuning" form on this
            # device's own web page at :8082.
        })

    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"status": "error", "message": "Request body is empty"}), 400

    cfg                 = load_config()
    updated_fields      = []
    rf_mode_val         = None   # store rf_mode value to apply after save
    power_val           = None   # store power value to apply after save
    dim_val             = None   # store dim value to apply after save (no reboot)
    antenna_val         = None   # store antenna value to apply after save

    # NOTE: "PORT" is intentionally excluded from ALLOWED_FIELDS - it's
    # the reader's physical serial device path (e.g. ttyAMA0) and must
    # never be changeable remotely via REST/MQTT. It stays in
    # config.json but is fully hidden from this API.
    #
    # NOTE: "version" is also intentionally excluded - it is a
    # read-only firmware/config schema marker written by the deployment
    # process, not something that should ever be changed remotely via
    # REST/MQTT.
    #
    # NOTE: "scan_newtag" IS still writable here even though it is no
    # longer exposed by the GET response above - the value can still
    # be set via REST/MQTT, it's just not surfaced in the read/info
    # endpoint.
    #
    # NOTE: "inventory_mode", "auto_tune_passes", "auto_tune_quiet_cycles",
    # "rssi_threshold", "rssi_threshold_strict" and "min_reads_per_scan"
    # are intentionally excluded from ALLOWED_FIELDS - they must NEVER
    # be changeable via REST/MQTT. They live in config.json but can
    # only be edited through the "Advanced RF Tuning" form on this
    # device's own web page at :8082 (see index() route above).
    ALLOWED_FIELDS = {
        "hostname", "location", "ip_broker",
        "rfid_filter", "scan_timeout", "scan_newtag",
        "rf_mode", "power", "dim", "antenna",
    }

    for field in ALLOWED_FIELDS:
        if field not in body:
            continue

        if field in ("scan_timeout", "scan_newtag"):
            try:
                v = int(body[field])
                if field == "scan_timeout" and not (12 <= v <= 20):
                    return jsonify({"status": "error",
                                    "message": "scan_timeout must be between 12 and 20"}), 400
                if field == "scan_newtag" and not (7 <= v <= 15):
                    return jsonify({"status": "error",
                                    "message": "scan_newtag must be between 7 and 15"}), 400
                cfg[field] = v
                updated_fields.append(field)
            except (ValueError, TypeError):
                return jsonify({"status": "error",
                                "message": f"{field} must be a number"}), 400

        elif field == "rfid_filter":
            normalized = normalize_rfid_filter(body[field])
            if normalized is None:
                return jsonify({"status": "error",
                                "message": f"rfid_filter contains invalid value(s); only "
                                           f"{sorted(RFID_FILTER_WHITELIST)} are allowed "
                                           f"(e.g. '8600', 'E28', '8600,E28', '8600,6453,E28')"}), 400
            cfg[field] = normalized
            updated_fields.append(field)

        elif field == "ip_broker":
            new_ip = str(body[field]).strip()
            if new_ip:
                threading.Thread(
                    target=apply_broker, args=(new_ip, False, True), daemon=True
                ).start()
                cfg[field] = new_ip
                updated_fields.append(field)

        elif field == "hostname":
            cfg[field] = body[field]
            updated_fields.append(field)
            if apply_system_hostname(body[field]):
                threading.Thread(target=reboot_after_delay, daemon=True).start()

        elif field == "rf_mode":
            val = str(body[field]).strip().upper()
            if val not in VALID_RF_MODES:
                valid_list = ", ".join(sorted(VALID_RF_MODES))
                return jsonify({"status": "error",
                                "message": f"rf_mode must be one of: {valid_list}"}), 400
            cfg[field]  = val
            rf_mode_val = val   # store to apply (reboot) after save
            updated_fields.append(field)

        elif field == "power":
            try:
                val = int(body[field])
            except (ValueError, TypeError):
                return jsonify({"status": "error",
                                "message": "power must be a number"}), 400
            if val not in VALID_POWER_DBM:
                return jsonify({"status": "error",
                                "message": f"power must be between {POWER_MIN} and {POWER_MAX} dBm"}), 400
            cfg[field] = val
            power_val  = val   # store to apply (reboot) after save
            updated_fields.append(field)

        elif field == "dim":
            val = normalize_dim(body[field])
            if val is None:
                valid_list = ", ".join(str(x) for x in sorted(VALID_DIM))
                return jsonify({"status": "error",
                                "message": f"dim must be one of: {valid_list}"}), 400
            cfg[field] = val
            dim_val    = val   # store to apply AFTER save - NO reboot for dim
            updated_fields.append(field)

        elif field == "antenna":
            normalized = normalize_antenna_list(body[field])
            if normalized is None:
                return jsonify({"status": "error",
                                "message": f"antenna must be one or more antenna IDs between "
                                           f"{ANTENNA_MIN} and {ANTENNA_MAX} (e.g. '4', '1,3', "
                                           f"'1,2,3,4,5,6,7,8')"}), 400
            cfg[field]  = normalized
            antenna_val = normalized   # store to apply (reboot) after save
            updated_fields.append(field)

        else:
            cfg[field] = body[field]
            updated_fields.append(field)

    if not updated_fields:
        return jsonify({"status": "error",
                        "message": "No recognised fields in request"}), 400

    # Save config first, THEN apply rf_mode/power/dim/antenna change.
    save_config(cfg)
    print(f"[REST CONFIG] Updated fields: {updated_fields}")

    # Apply the rf_mode change AFTER save_config() has completed.
    # reader.py only reads rf_mode at process startup, so a reboot is
    # required for the new mode to actually take effect on the RF side.
    if rf_mode_val:
        apply_rf_mode_change(rf_mode_val, source="REST")

    # Apply the power change AFTER save_config() has completed.
    # reader.py only reads "power" at process startup, so a reboot is
    # required for the new TX power level to actually take effect.
    if power_val:
        apply_power_change(power_val, source="REST")

    # Apply the dim change AFTER save_config() has completed.
    # NOTE: unlike rf_mode/power/antenna, this deliberately does
    # NOT reboot - light.py's _get_configured_brightness() re-reads
    # config.json's "dim" value live on every light_on() call, so the
    # new brightness takes effect the very next time the light turns on.
    if dim_val is not None:
        apply_dim_change(dim_val, source="REST")

    # Apply the antenna change AFTER save_config() has completed.
    # reader.py only reads "antenna" at process startup, so a reboot is
    # required for the new antenna set to actually take effect.
    if antenna_val:
        apply_antenna_change(antenna_val, source="REST")

    return jsonify({"status": "ok", "updated": updated_fields})

# ======================================================
# API: REBOOT
# ======================================================
@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    print("[REBOOT] Reboot command received via REST")
    threading.Thread(target=reboot_after_delay, args=(3,), daemon=True).start()
    return jsonify({"status": "ok", "message": "Rebooting in 3 seconds..."})

# ======================================================
# MAIN
# ======================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Apps+Web Starting...")
    print("=" * 60)

    # Sync _last_rf_mode_state / _last_power_state / _last_dim_state /
    # _last_antenna_state with the saved config at startup so that the
    # first real change is always detected as a change.
    _cfg_startup = load_config()
    _last_rf_mode_state = _cfg_startup.get("rf_mode", "DC")
    print(f"[RF_MODE] Startup state: {_last_rf_mode_state}")

    try:
        _last_power_state = int(_cfg_startup.get("power", 30))
    except (ValueError, TypeError):
        _last_power_state = 30
    print(f"[POWER] Startup state: {_last_power_state}dBm")

    try:
        _last_dim_state = int(_cfg_startup.get("dim", 0))
    except (ValueError, TypeError):
        _last_dim_state = 0
    print(f"[DIM] Startup state: {_last_dim_state}%")

    _startup_antenna = _cfg_startup.get("antenna", ["1", "2", "3", "4"])
    try:
        _last_antenna_state = tuple(sorted(int(x) for x in _startup_antenna))
    except (ValueError, TypeError):
        _last_antenna_state = (1, 2, 3, 4)
    print(f"[ANTENNA] Startup state: {list(_last_antenna_state)}")

    print(f"[VERSION] config.json version: {_cfg_startup.get('version', __version__)}")

    hostname_changed, old_hn, new_hn = check_and_update_hostname()
    if hostname_changed:
        print(f"[STARTUP] Hostname changed: {old_hn} -> {new_hn}")
    else:
        print(f"[STARTUP] Current hostname: {new_hn}")

    data      = read_apps_json()
    broker_ip = data.get("ip_broker", "")

    if not broker_ip:
        detected_ip = auto_detect_windows_ip()
        if detected_ip:
            broker_ip = detected_ip
            print(f"[STARTUP] Broker IP auto-detected: {broker_ip}")
            apply_broker(broker_ip)
        else:
            print("[STARTUP] Auto-detect failed, will be set on first access")
    else:
        print(f"[STARTUP] Broker IP configured: {broker_ip}")
        try:

            apply_broker(broker_ip, publish=True, force=True)
            print("[STARTUP] Bridge + queue setup OK")
        except Exception as e:
            print(f"[STARTUP] Bridge setup failed: {e}")

    threading.Thread(target=auto_set_after_delay, args=(300,), daemon=True).start()

    print(f"\nApps+Web running on http://{get_local_ip()}:8082\n")
    serve(app, host="0.0.0.0", port=8082)
