# -*- coding: utf-8 -*-
__version__ = "1.0.0(15) - per-tag RSSI/antenna diagnostic dump (rssi.json)"

import os
import serial
import time
import RPi.GPIO as GPIO
import json
from collections import Counter

from mutex import FileMutex


# =====================================================================================================================================
#      READER
#      Name                       : READER
#      Version                    : 1.0.0(15) - per-tag RSSI/antenna diagnostic dump (rssi.json)
#      Date Created               : 08-05-2026
#      Date Update                : 20-09-2026
#      Author                     : Saifuddin
# ======================================================================================================================================

# === log ===
from logger import get_logger
log = get_logger("reader")
# ===========


# =====================================================
#  DEFAULT VALUES - edit here to change the default.
# =====================================================

# --- RSSI ghost-tag filter (0xAA48/AA58 tag reads) ---
RSSI_THRESHOLD_DEFAULT = None

# --- adaptive RSSI threshold: TIGHTER filter when cabinet is nearly empty ---
RSSI_THRESHOLD_STRICT_DEFAULT       = -95
RSSI_THRESHOLD_STRICT_COUNT_DEFAULT = 10

# --- min_reads_per_scan ghost-tag filter ---
MIN_READS_PER_SCAN_DEFAULT = 3

# --- native "Read Count" ghost-tag filter ---
MIN_NATIVE_READ_COUNT_DEFAULT = 1


# --- auto-tuning (dwell time / quiet-stop timing) ---
AUTO_TUNE_PASSES_DEFAULT       = 3
AUTO_TUNE_QUIET_CYCLES_DEFAULT = 2.5

# =====================================================
#  ASYNC LISTEN TIME
# =====================================================
ASYNC_LISTEN_SECONDS = 8
ASYNC_LISTEN_BASELINE_SCAN_TIMEOUT = 14
ASYNC_TEARDOWN_RESERVE_SECONDS = 0.3


# =====================================================
#  READER MUTEX
# =====================================================
reader_mutex = FileMutex("reader")


def acquire_reader_mutex(wait=True, retry_interval=3.2, timeout=None):
    return reader_mutex.acquire(
        wait=wait,
        retry_interval=retry_interval,
        timeout=timeout,
        owner="READER"
    )


def release_reader_mutex():
    reader_mutex.release(owner="READER")


# =====================================================
#  GPIO READER SETUP
# =====================================================
GPIO.setwarnings(False)

SHUTDOWN_GPIO = 23
TIMEOUT       = 1
TRY_BAUDRATES = [921600]

GPIO.setmode(GPIO.BCM)
GPIO.setup(SHUTDOWN_GPIO, GPIO.OUT)
GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)


# =====================================================
#  COMMANDS
# =====================================================
CMD_START_ASYNC      = bytes.fromhex("FF13AA4D6F64756C6574656368AA48000000800375BB4D30")
CMD_STOP_ASYNC       = bytes.fromhex("FF0EAA4D6F64756C6574656368AA49F3BB0391")
CMD_SET_BAUD         = bytes.fromhex("FF14AA4D6F64756C6574656368AA400601000E10000FBB799F")
CMD_START_FIRMWARE   = bytes.fromhex("FF00041D0B")
CMD_SET_REGION       = bytes.fromhex("FF0197014BBC")
CMD_SET_PROTOCOL_GEN2 = bytes.fromhex("FF02930005517D")

CMD_GET_VERSION = bytes.fromhex("FF00031D0C")


def _calc_crc(msgbuf: bytes) -> int:
    calc_crc = 0xFFFF
    for i in range(1, len(msgbuf)):
        b = msgbuf[i]
        for bit in range(7, -1, -1):
            xor_flag = (calc_crc >> 15) & 1
            calc_crc = ((calc_crc << 1) | ((b >> bit) & 1)) & 0xFFFF
            if xor_flag:
                calc_crc ^= 0x1021
    return calc_crc


MODULETECH_MARKER = bytes.fromhex("4D6F64756C6574656368")
EXT_TERMINATOR     = 0xBB


def get_subcrc(data: bytes) -> int:
    return sum(data) & 0xFF


def build_ext_command(subcmd: bytes, subdata: bytes) -> bytes:
    if len(subcmd) != 2:
        raise ValueError("subcmd must be exactly 2 bytes")
    subcrc = get_subcrc(subcmd + subdata)
    data_field = MODULETECH_MARKER + subcmd + subdata + bytes([subcrc, EXT_TERMINATOR])
    frame_wo_crc = bytes([0xFF, len(data_field), 0xAA]) + data_field
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


class InvalidAntennaError(Exception):
    pass


def parse_antenna_list(config):
    raw = config.get("antenna")

    if not raw or not isinstance(raw, list):
        log.error(
            "[CONFIG] 'antenna' key missing/empty/invalid in config.json "
            "(expected a non-empty list, e.g. [\"1\",\"2\",\"3\",\"4\",\"5\"] or "
            "just [\"2\"]). Reader will NOT start until this is fixed."
        )
        raise InvalidAntennaError(
            "config.json must contain a non-empty 'antenna' list, "
            "e.g. [\"1\",\"2\",\"3\",\"4\",\"5\"] or [\"2\"]"
        )

    try:
        ant_list = sorted(set(int(str(a).strip()) for a in raw))
    except (ValueError, TypeError):
        log.error(
            "[CONFIG] 'antenna' list in config.json contains non-numeric "
            "value(s): %s. Reader will NOT start until this is fixed.", raw
        )
        raise InvalidAntennaError(
            "antenna list must contain numeric antenna IDs, got: %s" % raw
        )

    for a in ant_list:
        if not (1 <= a <= 32):
            log.error(
                "[CONFIG] Invalid antenna id %d in config.json "
                "(must be within 1-32). Reader will NOT start until this is fixed.", a
            )
            raise InvalidAntennaError("antenna id %d is out of valid range 1-32" % a)

    return ant_list


def build_enable_ant_command(ant_list):
    ant_list = sorted(set(ant_list))
    if not ant_list:
        raise InvalidAntennaError("antenna list is empty")
    for a in ant_list:
        if not (1 <= a <= 32):
            raise InvalidAntennaError("antenna id %d is out of valid range 1-32" % a)

    payload = bytes([0x02])
    for a in ant_list:
        payload += bytes([a, a])

    data_length = len(payload)
    frame_wo_crc = bytes([0xFF, data_length, 0x91]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_antenna_command(config):
    ant_list = parse_antenna_list(config)
    cmd = build_enable_ant_command(ant_list)
    log.info(
        "[CONFIG] antenna=%s selected from config.json -> CMD=%s "
        "(built fresh every time - works for any count/combination)",
        ant_list, cmd.hex().upper()
    )
    return cmd


class InvalidPowerError(Exception):
    pass


POWER_MIN_DBM = 25
POWER_MAX_DBM = 33
POWER_SETTING_TIME = 0x01F4


def build_power_command(ant_list, dbm):
    ant_list = sorted(set(ant_list))
    if not ant_list:
        raise InvalidAntennaError("antenna list is empty (cannot build power command)")
    for a in ant_list:
        if not (1 <= a <= 32):
            raise InvalidAntennaError("antenna id %d is out of valid range 1-32" % a)

    power_val = int(round(dbm * 100))
    payload = bytes([0x04])
    for a in ant_list:
        payload += (
            bytes([a])
            + power_val.to_bytes(2, "big")
            + power_val.to_bytes(2, "big")
            + POWER_SETTING_TIME.to_bytes(2, "big")
        )

    data_length = len(payload)
    frame_wo_crc = bytes([0xFF, data_length, 0x91]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_power_command(config):
    raw = config.get("power", "")

    try:
        dbm = int(str(raw).strip())
    except (ValueError, TypeError):
        log.error(
            "[CONFIG] 'power' key missing/non-numeric in config.json: %r "
            "(expected an integer dBm value, e.g. 30). Reader will NOT "
            "start until this is fixed.", raw
        )
        raise InvalidPowerError(
            "config.json must contain a numeric 'power' value in dBm, got: %r" % (raw,)
        )

    if not (POWER_MIN_DBM <= dbm <= POWER_MAX_DBM):
        log.error(
            "[CONFIG] INVALID power=%d in config.json. Valid range: %d-%d dBm. "
            "Reader will NOT start until this is fixed.",
            dbm, POWER_MIN_DBM, POWER_MAX_DBM
        )
        raise InvalidPowerError(
            "power=%d is not valid. Must be %d-%d dBm" % (dbm, POWER_MIN_DBM, POWER_MAX_DBM)
        )

    ant_list = parse_antenna_list(config)
    cmd = build_power_command(ant_list, dbm)

    log.info(
        "[CONFIG] power=%ddBm antenna=%s -> CMD=%s "
        "(built fresh for exactly these antennas - %d antenna(s), not a fixed count)",
        dbm, ant_list, cmd.hex().upper(), len(ant_list)
    )
    return cmd


class InvalidDwellError(Exception):
    pass


DWELL_MIN_MS     = 20
DWELL_MAX_MS     = 60000
DWELL_DEFAULT_MS = 2000


def build_dwell_command(dwell_ms):
    dwell_ms = int(dwell_ms)
    if not (DWELL_MIN_MS <= dwell_ms <= DWELL_MAX_MS):
        raise InvalidDwellError(
            "antenna_dwell_ms=%d out of valid range %d-%d" % (dwell_ms, DWELL_MIN_MS, DWELL_MAX_MS)
        )

    payload = bytes([0x02]) + dwell_ms.to_bytes(4, "big")
    data_length = len(payload)
    frame_wo_crc = bytes([0xFF, data_length, 0x95]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_dwell_command(config):
    raw = config.get("antenna_dwell_ms", DWELL_DEFAULT_MS)

    try:
        dwell_ms = int(str(raw).strip())
    except (ValueError, TypeError):
        log.error(
            "[CONFIG] 'antenna_dwell_ms' in config.json is non-numeric: %r. "
            "Falling back to default %dms.", raw, DWELL_DEFAULT_MS
        )
        dwell_ms = DWELL_DEFAULT_MS

    try:
        cmd = build_dwell_command(dwell_ms)
    except InvalidDwellError as e:
        log.error(
            "[CONFIG] %s. Falling back to default %dms.", e, DWELL_DEFAULT_MS
        )
        dwell_ms = DWELL_DEFAULT_MS
        cmd = build_dwell_command(dwell_ms)

    log.info(
        "[CONFIG] (manual mode) antenna_dwell_ms=%d selected -> CMD=%s "
        "(module's own factory default if this command is never sent is 4000ms)",
        dwell_ms, cmd.hex().upper()
    )
    return cmd, dwell_ms


def compute_auto_timing(scan_timeout_s, antenna_count, passes_target=3, quiet_cycles=1):
    antenna_count = max(1, int(antenna_count))
    passes_target = max(1, int(passes_target))
    scan_timeout_s = max(0.001, float(scan_timeout_s))

    dwell_ms_ideal = (scan_timeout_s * 1000.0) / (antenna_count * passes_target)
    dwell_ms = int(round(dwell_ms_ideal))
    dwell_clamped = min(max(dwell_ms, DWELL_MIN_MS), DWELL_MAX_MS)

    one_cycle_s = (antenna_count * dwell_clamped) / 1000.0
    quiet_stop_s = one_cycle_s * max(1.0, float(quiet_cycles))

    quiet_stop_s = max(quiet_stop_s, one_cycle_s)
    quiet_stop_s = min(quiet_stop_s, max(scan_timeout_s - 0.5, one_cycle_s))

    achieved_passes = scan_timeout_s * 1000.0 / (antenna_count * dwell_clamped)

    return {
        "dwell_ms":        dwell_clamped,
        "dwell_ms_ideal":  dwell_ms,
        "dwell_clamped":   dwell_clamped != dwell_ms,
        "one_cycle_s":     round(one_cycle_s, 3),
        "quiet_stop_s":    round(quiet_stop_s, 2),
        "passes_target":   passes_target,
        "passes_achieved": round(achieved_passes, 2),
        "antenna_count":   antenna_count,
        "scan_timeout_s":  scan_timeout_s,
    }


def get_auto_dwell_command(timing):
    return build_dwell_command(timing["dwell_ms"])


RF_MODE_TABLE = {
    "CB": bytes.fromhex("FF039B0502CBDE23"),
    "6F": bytes.fromhex("FF039B05026FDE87"),
    "DC": bytes.fromhex("FF039B0502DCDE34"),
    "65": bytes.fromhex("FF039B050265DE8D"),
    "2D": bytes.fromhex("FF039B05022DDEC5"),
    "73": bytes.fromhex("FF039B050273DE9B"),
    "70": bytes.fromhex("FF039B050270DE98"),
    "67": bytes.fromhex("FF039B050267DE8F"),
    "69": bytes.fromhex("FF039B050269DE81"),
    "6B": bytes.fromhex("FF039B05026BDE83"),
    "71": bytes.fromhex("FF039B050271DE99"),
}

RF_MODE_DEFAULT = "DC"


class InvalidRfModeError(Exception):
    pass


def get_rf_mode_command(config):
    raw = str(config.get("rf_mode", "")).strip().upper()

    if raw in RF_MODE_TABLE:
        log.info("[CONFIG] rf_mode=%s selected from config.json", raw)
        return RF_MODE_TABLE[raw]

    valid_list = ", ".join(RF_MODE_TABLE.keys())
    log.error(
        "[CONFIG] INVALID rf_mode='%s' in config.json. Valid values: %s. "
        "Reader will NOT start until this is fixed.",
        raw, valid_list
    )
    raise InvalidRfModeError(
        "rf_mode='%s' is not valid. Must be one of: %s" % (raw, valid_list)
    )


CMD_SET_SESSION = bytes.fromhex("FF039B050001DCE9")

CMD_SET_TARGET_DYNAMIC_AB = bytes.fromhex("FF049B05010000A3FD")
CMD_SET_TARGET_DYNAMIC_BA = bytes.fromhex("FF049B05010001A3FC")
CMD_SET_TARGET_STATIC_A   = bytes.fromhex("FF049B05010100A2FD")
CMD_SET_TARGET_STATIC_B   = bytes.fromhex("FF049B05010101A2FC")

CMD_SET_Q_DYNAMIC  = bytes.fromhex("FF039B051200CEE8")
CMD_SET_Q_STATIC_8 = bytes.fromhex("FF049B0512010880A7")
CMD_SET_Q_STATIC_9 = bytes.fromhex("FF049B0512010980A6")
CMD_SET_Q_STATIC   = bytes.fromhex("FF049B0512010A80A5")


class InvalidQError(Exception):
    pass


Q_MIN = 0
Q_MAX = 15


def build_q_command(static: bool, q_value: int = None) -> bytes:
    if static:
        if q_value is None or not (Q_MIN <= int(q_value) <= Q_MAX):
            raise InvalidQError("static Q value must be %d-%d, got: %r" % (Q_MIN, Q_MAX, q_value))
        payload = bytes([0x05, 0x12, 0x01, int(q_value)])
    else:
        payload = bytes([0x05, 0x12, 0x00])

    frame_wo_crc = bytes([0xFF, len(payload), 0x9B]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_q_command(config):
    q_mode = str(config.get("q_mode", "dynamic")).strip().lower()

    if q_mode == "static":
        raw_q = config.get("q_value", 8)
        try:
            q_value = int(str(raw_q).strip())
            cmd = build_q_command(True, q_value)
        except (InvalidQError, ValueError, TypeError) as e:
            log.warning(
                "[CONFIG] q_mode=static but q_value=%r invalid (%s) - "
                "falling back to dynamic Q.", raw_q, e
            )
            return build_q_command(False), "dynamic", None

        log.info(
            "[CONFIG] q_mode=static q_value=%d -> CMD=%s "
            "(fixed slot count = 2^%d = %d slots per antenna round)",
            q_value, cmd.hex().upper(), q_value, 2 ** q_value
        )
        return cmd, "static", q_value

    if q_mode != "dynamic":
        log.warning(
            "[CONFIG] q_mode='%s' not recognized (use 'dynamic' or "
            "'static') - falling back to dynamic Q.", q_mode
        )

    cmd = build_q_command(False)
    log.info("[CONFIG] q_mode=dynamic -> CMD=%s (module's own default)", cmd.hex().upper())
    return cmd, "dynamic", None


SELECT_EPC_BANK_ADDRESS = 0x00000020


def build_select_filter_subdata(metadata_flags, search_flags, prefix_bytes):
    if not (1 <= len(prefix_bytes) <= 31):
        raise ValueError("Select filter prefix must be 1-31 bytes (8-248 bits)")

    option = 0x04
    access_password = bytes(4)
    select_address = SELECT_EPC_BANK_ADDRESS.to_bytes(4, "big")
    select_bitlen = len(prefix_bytes) * 8
    select_data_length = bytes([select_bitlen])

    return (
        metadata_flags.to_bytes(2, "big")
        + bytes([option])
        + search_flags.to_bytes(2, "big")
        + access_password
        + select_address
        + select_data_length
        + prefix_bytes
    )


def build_unfiltered_subdata(metadata_flags, search_flags):
    return (
        metadata_flags.to_bytes(2, "big")
        + bytes([0x00])
        + search_flags.to_bytes(2, "big")
    )


def get_start_async_full_command(config):
    METADATA_FLAGS = 0x003F
    SEARCH_FLAGS   = 0x8003

    raw_prefixes = config.get("rfid_filter", ["86"])

    if isinstance(raw_prefixes, list) and len(raw_prefixes) == 1:
        prefix_hex = str(raw_prefixes[0]).strip()
        try:
            prefix_bytes = bytes.fromhex(prefix_hex)
            if len(prefix_bytes) < 1:
                raise ValueError("empty prefix")
        except ValueError:
            log.warning(
                "[CONFIG] rfid_filter prefix '%s' is not valid hex - "
                "RF-level Select filter DISABLED this cycle, falling back "
                "to unfiltered CMD_START_ASYNC_FULL (software rfid_filter "
                "still applies).", prefix_hex
            )
        else:
            subdata = build_select_filter_subdata(METADATA_FLAGS, SEARCH_FLAGS, prefix_bytes)
            cmd = build_ext_command(bytes.fromhex("AA48"), subdata)
            log.info(
                "[CONFIG] RF-level Select filter ENABLED: EPC bank, prefix=%s "
                "(%d bits, bit offset 0x%X) -> module will only interrogate "
                "matching tags -> CMD=%s",
                prefix_hex.upper(), len(prefix_bytes) * 8, SELECT_EPC_BANK_ADDRESS,
                cmd.hex().upper()
            )
            return cmd
    else:
        log.info(
            "[CONFIG] RF-level Select filter NOT used (rfid_filter has %d "
            "entries in config.json, need exactly 1 for this module's "
            "single-rule AA48 Select) - falling back to unfiltered "
            "CMD_START_ASYNC_FULL.",
            len(raw_prefixes) if isinstance(raw_prefixes, list) else 0
        )

    subdata = build_unfiltered_subdata(METADATA_FLAGS, SEARCH_FLAGS)
    cmd = build_ext_command(bytes.fromhex("AA48"), subdata)
    log.info("[CONFIG] CMD_START_ASYNC_FULL (unfiltered) = %s", cmd.hex().upper())
    return cmd


AA58_SUPPORTED_REGIONS = {
    "CHINA", "CE_LOW", "CE_HIGH", "CE_LOW_AND_HIGH",
    "INDIA", "RUSSIA", "PHILIPPINES", "ISRAEL",
    "JAPAN", "JAPAN2", "JAPAN3",
}


def build_ex_dense_subdata(dense_mode: bool, metadata_flags: int, search_flags: int, option: int = 0x00) -> bytes:
    exconfig = bytearray(20)
    exconfig[0] = 0x00 if dense_mode else 0x01
    return (
        bytes(exconfig)
        + metadata_flags.to_bytes(2, "big")
        + bytes([option])
        + search_flags.to_bytes(2, "big")
    )


def get_start_async_ex_command(config):
    METADATA_FLAGS = 0x003F
    SEARCH_FLAGS   = 0x8003

    dense_mode = bool(config.get("dense_mode", True))
    subdata = build_ex_dense_subdata(dense_mode, METADATA_FLAGS, SEARCH_FLAGS)
    cmd = build_ext_command(bytes.fromhex("AA58"), subdata)

    log.info(
        "[CONFIG] CMD_START_ASYNC_EX (0xAA58) built: dense_mode=%s "
        "(ExConfigData[0]=0x%02X) -> CMD=%s",
        dense_mode, 0x00 if dense_mode else 0x01, cmd.hex().upper()
    )
    return cmd


def get_stop_async_ex_command():
    return build_ext_command(bytes.fromhex("AA59"), b"")


def warn_if_region_unsupported_for_ex(region_name):
    if region_name and region_name.upper() not in AA58_SUPPORTED_REGIONS:
        log.warning(
            "[CONFIG] inventory_mode=AA58 selected, but module reports "
            "certification region=%s, which the protocol doc does NOT list "
            "as supporting 0xAA58 (supported: %s). The module may reject "
            "AA58 or fall back to unexpected behavior - this is "
            "informational only, the scan is not blocked.",
            region_name, ", ".join(sorted(AA58_SUPPORTED_REGIONS))
        )


RSSI_FILTER_MIN_DBM = -128
RSSI_FILTER_MAX_DBM = -1

RSSI_FILTER_MIN_FIRMWARE_DATE_RAW = "20240424"


def build_rssi_filter_subdata(enabled: bool, threshold_dbm: int = None) -> bytes:
    if not enabled:
        return bytes([0x01, 0x00, 0x00, 0x00])

    if threshold_dbm is None or not (RSSI_FILTER_MIN_DBM <= int(threshold_dbm) <= RSSI_FILTER_MAX_DBM):
        raise ValueError(
            "rssi threshold must be an integer dBm in %d..%d, got: %r"
            % (RSSI_FILTER_MIN_DBM, RSSI_FILTER_MAX_DBM, threshold_dbm)
        )

    value_byte = int(threshold_dbm) & 0xFF
    return bytes([0x01, 0xAA, value_byte, 0x00])


def build_rssi_filter_get_subdata() -> bytes:
    return bytes([0x00])


def get_rssi_filter_command(config):
    raw = config.get("rssi_threshold", RSSI_THRESHOLD_DEFAULT)

    if raw is None:
        subdata = build_rssi_filter_subdata(enabled=False)
        cmd = build_ext_command(bytes.fromhex("AA5B"), subdata)
        log.info(
            "[CONFIG] rssi_threshold not set - RSSI filter CANCELLED at "
            "module level (0xAA5B) -> CMD=%s",
            cmd.hex().upper()
        )
        return cmd, None

    try:
        dbm = int(str(raw).strip())
        subdata = build_rssi_filter_subdata(enabled=True, threshold_dbm=dbm)
    except (ValueError, TypeError) as e:
        log.warning(
            "[CONFIG] rssi threshold value %r is invalid (%s) - "
            "falling back to filter CANCELLED at module level.", raw, e
        )
        subdata = build_rssi_filter_subdata(enabled=False)
        cmd = build_ext_command(bytes.fromhex("AA5B"), subdata)
        return cmd, None

    cmd = build_ext_command(bytes.fromhex("AA5B"), subdata)
    log.info(
        "[CONFIG] rssi_threshold=%ddBm -> RSSI filter ENABLED at module "
        "level (0xAA5B): module itself will not upload reads weaker than "
        "%ddBm (they never cross the serial link at all) -> CMD=%s",
        dbm, dbm, cmd.hex().upper()
    )
    return cmd, dbm


def warn_if_firmware_predates_rssi_filter(firmware_date_raw, rssi_threshold_configured):
    if rssi_threshold_configured is None or not firmware_date_raw:
        return
    try:
        if len(firmware_date_raw) == 8 and firmware_date_raw < RSSI_FILTER_MIN_FIRMWARE_DATE_RAW:
            log.warning(
                "[CONFIG] rssi_threshold=%s is configured, but module "
                "firmware date (raw=%s) looks older than the doc's "
                "minimum for 0xAA5B support (%s per sec 10.8). The module "
                "may silently ignore the RSSI filter command - this is "
                "informational only, the scan is not blocked.",
                rssi_threshold_configured, firmware_date_raw, RSSI_FILTER_MIN_FIRMWARE_DATE_RAW
            )
    except Exception:
        pass


BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RSSI_JSON_PATH = os.path.join(BASE_DIR, "rssi.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        log.error("[CONFIG] Failed to load config.json: %s", e)
        return {}


cfg  = load_config()
PORT = "/dev/" + cfg.get("PORT", "ttyS0")
log.info("[READER] Serial PORT=%s", PORT)

CMD_SET_RFMODE      = get_rf_mode_command(cfg)
CMD_ENABLE_ANT       = get_antenna_command(cfg)
CMD_SET_POWER_ALL    = get_power_command(cfg)

CMD_SET_ANTENNA_DWELL, ANTENNA_DWELL_MS = get_dwell_command(cfg)

CMD_START_ASYNC_FULL = get_start_async_full_command(cfg)

CMD_START_ASYNC_EX = get_start_async_ex_command(cfg)
CMD_STOP_ASYNC_EX  = get_stop_async_ex_command()

INVENTORY_MODE = str(cfg.get("inventory_mode", "AA48")).strip().upper()
if INVENTORY_MODE not in ("AA48", "AA58"):
    log.warning(
        "[CONFIG] inventory_mode='%s' in config.json is not 'AA48' or "
        "'AA58' - falling back to 'AA48'.", INVENTORY_MODE
    )
    INVENTORY_MODE = "AA48"
log.info("[READER] inventory_mode=%s", INVENTORY_MODE)


_raw_prefixes        = cfg.get("rfid_filter", ["86"])
EPC_ALLOWED_PREFIXES = tuple(p.upper() for p in _raw_prefixes)
log.info("[READER] EPC prefix filter: %s", EPC_ALLOWED_PREFIXES)


def is_allowed_epc(epc: str) -> bool:
    return any(epc.startswith(p) for p in EPC_ALLOWED_PREFIXES)


try:
    ALLOWED_ANTENNAS = frozenset(int(str(a).strip()) for a in cfg.get("antenna", []))
except (ValueError, TypeError):
    ALLOWED_ANTENNAS = frozenset()
log.info("[READER] Antenna allow-list (tags outside this are dropped): %s", sorted(ALLOWED_ANTENNAS))


def is_allowed_antenna(ant) -> bool:
    if ant is None:
        return False
    return ant in ALLOWED_ANTENNAS


ANTENNA_PORT_COUNT_MAP = {
    0x0: 1, 0x1: 2, 0x2: 4, 0x3: 8, 0x4: 16, 0x5: 32,
}

CHIP_TYPE_MAP = {
    0x31: "E710", 0x32: "E510", 0x33: "E310", 0x34: "E910",
}

REGION_MAP = {
    0x00: "CHINA", 0x01: "FCC", 0x02: "JAPAN", 0x03: "CE_LOW", 0x04: "KOREA",
    0x05: "CE_HIGH", 0x06: "HK", 0x07: "TAIWAN", 0x08: "MALAYSIA",
    0x09: "SOUTH_AFRICA", 0x0a: "BRAZIL", 0x0b: "THAILAND", 0x0c: "SINGAPORE",
    0x0d: "AUSTRALIA", 0x0e: "INDIA", 0x0f: "URUGUAY", 0x10: "VIETNAM",
    0x11: "ISRAEL", 0x12: "PHILIPPINES", 0x13: "INDONESIA", 0x14: "NEW_ZEALAND",
    0x15: "PERU", 0x16: "RUSSIA", 0x17: "CE_LOW_AND_HIGH", 0x18: "JAPAN2",
    0x19: "JAPAN3",
}


def query_module_version(ser, timeout=1.0):
    try:
        ser.reset_input_buffer()
        ser.write(CMD_GET_VERSION)
        time.sleep(0.2)
        resp = ser.read(64)
    except Exception as e:
        log.warning("[READER] GET VERSION: serial error: %s", e)
        return None

    if not resp or len(resp) < 27 or resp[0] != 0xFF or resp[2] != 0x03:
        log.warning(
            "[READER] GET VERSION: no/invalid response (raw=%s)",
            resp.hex().upper() if resp else "EMPTY"
        )
        return None

    status = (resp[3] << 8) | resp[4]
    if status != 0:
        log.warning("[READER] GET VERSION: STATUS=%04X (module reported an error)", status)
        return None

    bootloader_ver = resp[5:9]
    hardware_ver   = resp[9:13]
    firmware_date  = resp[13:17]
    firmware_ver   = resp[17:21]
    protocol_ver   = resp[21:25]

    chip_byte    = hardware_ver[0]
    portcls_byte = hardware_ver[1]
    region_byte  = hardware_ver[2]
    hwrev_byte   = hardware_ver[3]

    port_count_code = portcls_byte & 0x0F
    port_count      = ANTENNA_PORT_COUNT_MAP.get(port_count_code)
    chip_name       = CHIP_TYPE_MAP.get(chip_byte, "0x%02X" % chip_byte)
    region_name     = REGION_MAP.get(region_byte, "0x%02X" % region_byte)

    info = {
        "bootloader_version": bootloader_ver.hex().upper(),
        "hardware_version_raw": hardware_ver.hex().upper(),
        "chip_type": chip_name,
        "antenna_port_count": port_count,
        "region": region_name,
        "hardware_revision": hwrev_byte,
        "firmware_date_raw": firmware_date.hex().upper(),
        "firmware_date": (
            "20%02X.%02X.%02X" % (firmware_date[1], firmware_date[2], firmware_date[3])
            if firmware_date[0] == 0x20 else firmware_date.hex().upper()
        ),
        "firmware_version": firmware_ver.hex().upper(),
        "firmware_version_decoded": _decode_yymmdd_rev(firmware_ver),
        "supported_protocol": protocol_ver.hex().upper(),
    }
    return info


def _decode_yymmdd_rev(b: bytes):
    yy, mm, dd, rev = b[0], b[1], b[2], b[3]
    if 1 <= mm <= 12 and 1 <= dd <= 31:
        return "20%02X.%02X.%02X rev%d" % (yy, mm, dd, rev)
    return None


def log_module_version(baud):
    try:
        with serial.Serial(PORT, baudrate=baud, timeout=TIMEOUT) as ser:
            info = query_module_version(ser)
    except Exception as e:
        log.warning("[READER] Could not query module version: %s", e)
        return None

    if not info:
        log.warning("[READER] Module version info unavailable this run.")
        return None

    fw_display = info["firmware_version_decoded"] or info["firmware_version"]
    log.info(
        "[READER] Module SOFTWARE/FIRMWARE VERSION = %s  "
        "(chip=%s antenna_ports=%s region=%s hw_rev=%s bootloader=%s "
        "compiled=%s protocol=%s raw_fw=%s)",
        fw_display, info["chip_type"], info["antenna_port_count"], info["region"],
        info["hardware_revision"], info["bootloader_version"],
        info["firmware_date"], info["supported_protocol"], info["firmware_version"]
    )

    port_count = info["antenna_port_count"]
    if port_count is not None and ALLOWED_ANTENNAS:
        over_limit = sorted(a for a in ALLOWED_ANTENNAS if a > port_count)
        if over_limit:
            log.warning(
                "[CONFIG] config.json's 'antenna' list includes %s but this "
                "module only has %d physical antenna port(s) (chip=%s). "
                "Those antenna IDs cannot possibly return tags on this "
                "hardware - this is informational only, the scan is not "
                "blocked.",
                over_limit, port_count, info["chip_type"]
            )

    return info


def send_command(ser, cmd, delay=0.2, desc=None):
    if desc:
        log.debug("[READER] >> %s", desc)
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(delay)
    return ser.read_all()


def send_command_checked(ser, cmd, name, delay=0.15):
    ser.write(cmd)
    time.sleep(delay)
    resp = ser.read(64)

    if not resp:
        log.warning("[READER] %s: NO RESPONSE", name)
        return False

    if resp[0] != 0xFF:
        log.warning("[READER] %s: INVALID HEADER %s", name, resp.hex())
        return False

    status = (resp[3] << 8) | resp[4]
    if status == 0:
        log.debug("[READER] %s: SUCCESS", name)
        return True

    log.warning("[READER] %s: STATUS=%04X RAW=%s", name, status, resp.hex())
    return False


AUTO_DETECT_BAUDS     = [921600, 115200, 57600, 38400, 19200, 9600]
CMD_SAVE_DEFAULT_BAUD = bytes.fromhex("FF14AA4D6F64756C6574656368AA6701000E10000FBBF89C")


def _try_firmware_at_baud(baud):
    try:
        ser = serial.Serial(PORT, baudrate=baud, timeout=TIMEOUT)
        for _ in range(3):
            ser.reset_input_buffer()
            ser.write(CMD_START_FIRMWARE)
            time.sleep(0.3)
            resp = ser.read_all()
            if resp and resp[0] == 0xFF:
                return ser
        ser.close()
    except Exception as e:
        log.debug("[READER] Baud %s not responding: %s", baud, e)
    return None


def _change_baud_to_921600(ser):
    try:
        ser.reset_input_buffer()
        ser.write(CMD_SET_BAUD)
        time.sleep(0.5)
        resp = ser.read_all()
        if resp and len(resp) >= 5:
            status = (resp[3] << 8) | resp[4]
            if status == 0:
                log.info("[READER] Baud change command accepted")
                ser.reset_input_buffer()
                ser.write(CMD_SAVE_DEFAULT_BAUD)
                time.sleep(0.5)
                resp2 = ser.read_all()
                if resp2 and len(resp2) >= 5:
                    s2 = (resp2[3] << 8) | resp2[4]
                    if s2 == 0:
                        log.info("[READER] Baud 921600 saved to flash (permanent)")
                    else:
                        log.warning("[READER] Save to flash failed: STATUS=%04X", s2)
                return True
            else:
                log.warning("[READER] Baud change rejected: STATUS=%04X", status)
    except Exception as e:
        log.error("[READER] Baud change error: %s", e)
    return False


def set_baudrate():
    try:
        with serial.Serial(PORT, baudrate=921600, timeout=TIMEOUT) as ser:
            send_command(ser, CMD_SET_BAUD, desc="Set Baudrate")
        time.sleep(1)
        log.info("[READER] Baudrate set OK (921600)")
        return
    except Exception as e:
        log.warning("[READER] 921600 failed, trying auto-detect: %s", e)

    log.info("[READER] Auto-detecting baud rate...")
    for baud in AUTO_DETECT_BAUDS:
        if baud == 921600:
            continue
        log.info("[READER] Trying baud %s...", baud)
        ser = _try_firmware_at_baud(baud)
        if ser:
            log.info("[READER] Reader found at baud %s - changing to 921600...", baud)
            _change_baud_to_921600(ser)
            ser.close()
            time.sleep(1.5)
            log.info("[READER] Baud change done, reconnecting at 921600")
            return

    log.error("[READER] Auto-detect failed - no reader found on any baud rate")


def try_bootloader_scan():
    all_bauds = [921600] + [b for b in AUTO_DETECT_BAUDS if b != 921600]

    for baud in all_bauds:
        try:
            log.info("[READER] Connecting at %s", baud)
            with serial.Serial(PORT, baudrate=baud, timeout=TIMEOUT) as ser:
                for _ in range(3):
                    if send_command(ser, CMD_START_FIRMWARE).startswith(b'\xFF'):
                        log.info("[READER] Application mode @ %s", baud)
                        if baud != 921600:
                            log.warning(
                                "[READER] Reader at non-target baud %s, "
                                "will be corrected on next set_baudrate()", baud
                            )
                        return baud
        except Exception as e:
            log.debug("[READER] Baud %s error: %s", baud, e)

    log.error("[READER] No valid baud rate found!")
    return None


def is_valid_epc(epc: str) -> bool:
    if len(epc) != 24:
        return False
    if epc == "0" * 24:
        return False
    if epc.startswith("0000"):
        return False
    try:
        int(epc, 16)
    except ValueError:
        return False
    return True


HEARTBEAT_MARKER = b"XTSJ"


def _frame_is_heartbeat(buf, idx):
    return bytes(buf[idx + 5:idx + 9]) == HEARTBEAT_MARKER


def parse_fast_mode_correct(buf: bytearray):
    tags = []
    idx  = 0
    blen = len(buf)

    while idx + 3 <= blen:
        if buf[idx] != 0xFF or buf[idx + 2] != 0xAA:
            idx += 1
            continue

        datalen = buf[idx + 1]

        frame_end = idx + 7 + datalen

        if frame_end > blen:
            break

        frame_wo_crc = buf[idx:frame_end - 2]
        recv_crc = (buf[frame_end - 2] << 8) | buf[frame_end - 1]
        if _calc_crc(bytes(frame_wo_crc)) != recv_crc:
            idx += 1
            continue

        if _frame_is_heartbeat(buf, idx):
            log.debug("[PARSE] Heartbeat packet received - skipped (not a tag)")
            idx = frame_end
            continue

        try:
            status = (buf[idx + 3] << 8) | buf[idx + 4]
            if status != 0:
                idx = frame_end
                continue

            metaflag = (buf[idx + 5] << 8) | buf[idx + 6]
            p = idx + 7
            data_end = frame_end - 2

            read_count = rssi = ant = None

            if metaflag & 0x0001: read_count = buf[p]; p += 1
            if metaflag & 0x0002: rssi = buf[p] - 256 if buf[p] > 127 else buf[p]; p += 1
            if metaflag & 0x0004: ant = buf[p]; p += 1
            if metaflag & 0x0008: p += 3
            if metaflag & 0x0010: p += 4
            if metaflag & 0x0020: p += 2
            if metaflag & 0x0080: p += 2

            if p >= data_end:
                idx = frame_end
                continue

            epc_total_len = buf[p]; p += 1
            epc_len   = epc_total_len - 4
            epc_start = p + 2
            epc_end   = epc_start + epc_len

            if epc_len < 0 or epc_end > data_end:
                idx = frame_end
                continue

            epc = buf[epc_start:epc_end].hex().upper()

            if is_valid_epc(epc):
                if not is_allowed_antenna(ant):
                    log.debug(
                        "[FILTER] Tag from ANT=%s not in configured antenna %s - dropped: %s",
                        ant, sorted(ALLOWED_ANTENNAS), epc
                    )
                    idx = frame_end
                    continue

                if not is_allowed_epc(epc):
                    log.debug("[FILTER] EPC prefix not allowed %s: %s", EPC_ALLOWED_PREFIXES, epc)
                    idx = frame_end
                    continue

                tags.append({
                    "EPC":   epc,
                    "ANT":   ant,
                    "RSSI":  rssi,
                    "COUNT": read_count or 1
                })

            idx = frame_end

        except Exception as e:
            log.debug("[PARSE] Unexpected error parsing valid-CRC frame: %s", e)
            idx = frame_end

    return tags, buf[idx:]


def power_cycle_reader(off_time=1.5, on_settle=1.0):
    log.info(
        "[SCAN] Power-cycling reader (off=%.1fs, settle=%.1fs)",
        off_time, on_settle
    )
    GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
    time.sleep(off_time)
    GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)
    time.sleep(on_settle)


def power_cycle_before_first_scan():
    power_cycle_reader(off_time=1.5, on_settle=1.0)


# =====================================================
#  RSSI/ANTENNA DIAGNOSTIC 
# =====================================================
def write_rssi_json(published_epc_seen, epc_antenna_rssi, epc_best_rssi,
                     scan_meta, path=RSSI_JSON_PATH):
    """
    published_epc_seen : {epc: 1} - the final, post-ghost-filter result
                          (same dict returned as result["epc_seen"]).
    epc_antenna_rssi   : {epc: {ant: best_rssi_on_that_antenna}}
    epc_best_rssi       : {epc: best_rssi_overall} - already tracked
                          elsewhere in run_async_scan() for the RSSI
                          ghost-reject step; reused here so there's only
                          one source of truth for "best RSSI" per tag.
    scan_meta           : dict of scan-level info (timestamp, duration,
                          antennas configured, etc.) written alongside
                          the per-tag data.
    """
    tags_out = {}
    for epc in published_epc_seen:
        ant_map = epc_antenna_rssi.get(epc, {})
        tags_out[epc] = {
            "rssi_best": epc_best_rssi.get(epc),
            "antenna_count": len(ant_map),
            "antennas": {str(ant): rssi for ant, rssi in sorted(ant_map.items())},
        }

    payload = {
        "generated_at": scan_meta.get("timestamp"),
        "scan_timeout_s": scan_meta.get("scan_timeout_s"),
        "duration_s": scan_meta.get("duration_s"),
        "antennas_configured": scan_meta.get("antennas_configured"),
        "unique_tag_count": len(tags_out),
        "tags": tags_out,
    }

    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)  # atomic on POSIX - never leaves a half-written rssi.json
        log.info(
            "[RSSI-JSON] Wrote %s (%d tag(s), antennas_configured=%s)",
            path, len(tags_out), scan_meta.get("antennas_configured")
        )
    except Exception as e:
        log.warning("[RSSI-JSON] Failed to write %s: %s", path, e)


def run_async_scan(baud, stop_event=None, cycle_start=None):
    """
    cycle_start: time.time() timestamp of when the OUTER caller's scan
    cycle truly began. Used to compute the ADAPTIVE listen window and
    the REAL published duration, so both stay honest against
    scan_timeout regardless of how long setup overhead actually took
    this cycle.
    """
    cfg = load_config()
    if cycle_start is None:
        cycle_start = time.time()

    SCAN_TIMEOUT_RAW = float(cfg.get("scan_timeout", 24))

    ASYNC_LISTEN_TARGET = ASYNC_LISTEN_SECONDS + max(
        0.0, SCAN_TIMEOUT_RAW - ASYNC_LISTEN_BASELINE_SCAN_TIMEOUT
    )

    MAX_SCAN_TIME = min(ASYNC_LISTEN_TARGET, SCAN_TIMEOUT_RAW)

    log.info(
        "[CONFIG] scan_timeout(config)=%.1fs listen_target_base(kode)=%.1fs "
        "@baseline_scan_timeout=%.1fs -> listen_target_this_cycle=%.1fs "
        "- estimasi awal MAX_SCAN_TIME=%.1fs, akan disesuaikan ADAPTIF "
        "setelah setup commands terkirim berdasarkan overhead nyata cycle ini.",
        SCAN_TIMEOUT_RAW, ASYNC_LISTEN_SECONDS, ASYNC_LISTEN_BASELINE_SCAN_TIMEOUT,
        ASYNC_LISTEN_TARGET, MAX_SCAN_TIME
    )

    STABLE_THRESHOLD = int(cfg.get("stable_stop_threshold", 0))
    AUTO_TUNE        = bool(cfg.get("auto_tune_timing", True))

    rssi_filter_cmd, rssi_threshold_used = get_rssi_filter_command(cfg)

    RSSI_STRICT_COUNT_BOUNDARY = int(cfg.get("rssi_threshold_strict_count", RSSI_THRESHOLD_STRICT_COUNT_DEFAULT))
    RSSI_STRICT_DBM            = int(cfg.get("rssi_threshold_strict", RSSI_THRESHOLD_STRICT_DEFAULT))

    MIN_READS_PER_SCAN = max(1, int(cfg.get("min_reads_per_scan", MIN_READS_PER_SCAN_DEFAULT)))

    MIN_NATIVE_READ_COUNT = max(1, int(cfg.get("min_native_read_count", MIN_NATIVE_READ_COUNT_DEFAULT)))

    scan_inventory_mode = str(cfg.get("inventory_mode", "AA48")).strip().upper()
    if scan_inventory_mode not in ("AA48", "AA58"):
        scan_inventory_mode = "AA48"
    use_ex_inventory = (scan_inventory_mode == "AA58")

    if use_ex_inventory:
        start_cmd = get_start_async_ex_command(cfg)
        stop_cmd  = CMD_STOP_ASYNC_EX
        q_cmd, q_mode_used, q_value_used = None, None, None
    else:
        start_cmd = CMD_START_ASYNC_FULL
        stop_cmd  = CMD_STOP_ASYNC
        q_cmd, q_mode_used, q_value_used = get_q_command(cfg)

    try:
        ant_list_now      = parse_antenna_list(cfg)
        antenna_count_now = len(ant_list_now)
    except InvalidAntennaError:
        ant_list_now      = sorted(ALLOWED_ANTENNAS) or [1]
        antenna_count_now = len(ant_list_now)
        log.warning(
            "[CONFIG] Could not re-parse 'antenna' list this cycle - "
            "falling back to the antenna list used at startup (%s) for "
            "auto-tuning.", ant_list_now
        )

    if AUTO_TUNE:
        passes_target  = int(cfg.get("auto_tune_passes", AUTO_TUNE_PASSES_DEFAULT))
        quiet_cycles   = float(cfg.get("auto_tune_quiet_cycles", AUTO_TUNE_QUIET_CYCLES_DEFAULT))
        timing = compute_auto_timing(
            MAX_SCAN_TIME, antenna_count_now,
            passes_target=passes_target, quiet_cycles=quiet_cycles
        )
        dwell_cmd     = get_auto_dwell_command(timing)
        ANTENNA_DWELL = timing["dwell_ms"]
        QUIET_NEW_TAG = timing["quiet_stop_s"]

        log.info(
            "[AUTO-TUNE] scan_timeout=%.1fs antenna_count=%d passes_target=%d "
            "-> dwell_ms=%d (ideal=%.1f%s) one_cycle=%.2fs quiet_stop=%.2fs "
            "(%.2f cycles) passes_achieved=%.2f",
            MAX_SCAN_TIME, antenna_count_now, passes_target,
            timing["dwell_ms"], timing["dwell_ms_ideal"],
            " CLAMPED" if timing["dwell_clamped"] else "",
            timing["one_cycle_s"], timing["quiet_stop_s"], quiet_cycles,
            timing["passes_achieved"]
        )
        if cfg.get("scan_newtag") is not None:
            log.debug(
                "[AUTO-TUNE] config.json 'scan_newtag'=%s is IGNORED while "
                "auto_tune_timing is on (default/true). Set "
                "\"auto_tune_timing\": false to use it.", cfg.get("scan_newtag")
            )
    else:
        dwell_cmd, ANTENNA_DWELL = get_dwell_command(cfg)
        QUIET_NEW_TAG = float(cfg.get("scan_newtag", 10))
        log.info(
            "[MANUAL] auto_tune_timing=false -> antenna_dwell_ms=%d (from "
            "config/default) quiet_stop(scan_newtag)=%.1fs",
            ANTENNA_DWELL, QUIET_NEW_TAG
        )

    log.info(
        "[CONFIG] scan_timeout=%.0fs quiet_stop=%.1fs stable_threshold=%d "
        "rssi=%s(module-level 0xAA5B) prefix=%s "
        "power=%sdBm antenna_allowed=%s dwell_ms=%d auto_tune=%s inventory_mode=%s q=%s%s "
        "min_reads_per_scan=%d min_native_read_count=%d rssi_strict=%ddBm(if count<%d)",
        MAX_SCAN_TIME, QUIET_NEW_TAG, STABLE_THRESHOLD, rssi_threshold_used, EPC_ALLOWED_PREFIXES,
        cfg.get("power"), sorted(ALLOWED_ANTENNAS), ANTENNA_DWELL, AUTO_TUNE,
        scan_inventory_mode,
        "n/a(AA58)" if use_ex_inventory else q_mode_used,
        "" if (use_ex_inventory or q_value_used is None) else ("=%d" % q_value_used),
        MIN_READS_PER_SCAN, MIN_NATIVE_READ_COUNT, RSSI_STRICT_DBM, RSSI_STRICT_COUNT_BOUNDARY
    )

    epc_seen  = {}
    epc_native_max_count = {}
    epc_best_rssi = {}
    epc_antenna_rssi = {}  # NEW in 1.0.0(15): {epc: {ant: best_rssi_on_that_antenna}} - see write_rssi_json()
    buffer    = bytearray()

    if os.path.exists("stop.flag"):
        try:
            os.remove("stop.flag")
        except Exception:
            pass

    start_time        = time.time()
    last_new_tag_time = start_time
    aborted           = False
    total_reads       = 0
    antenna_gate_open = False

    GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)

    try:
        with serial.Serial(PORT, baudrate=baud, timeout=0.05) as ser:

            send_command_checked(ser, CMD_SET_REGION,            "SET REGION")
            send_command_checked(ser, CMD_SET_PROTOCOL_GEN2,     "SET GEN2")
            send_command_checked(ser, CMD_SET_POWER_ALL,         "SET POWER")

            rssi_ok = send_command_checked(ser, rssi_filter_cmd, "SET RSSI FILTER (0xAA5B)", delay=0.15)
            if not rssi_ok:
                log.warning(
                    "[SCAN] SET RSSI FILTER (0xAA5B) not confirmed - module "
                    "may be on firmware older than 202404024 (doc sec 10.8) "
                    "or may not support this command. Weak reads will NOT "
                    "be filtered this cycle (there is no software fallback "
                    "as of 1.0.0(6))."
                )

            antenna_gate_open = send_command_checked(ser, CMD_ENABLE_ANT, "ENABLE ANT", delay=0.3)

            if not antenna_gate_open:
                log.warning(
                    "[SCAN] ENABLE ANT status not confirmed (no/failed response). "
                    "This does not stop the scan - proceeding to START_ASYNC anyway."
                )

            dwell_ok = send_command_checked(
                ser, dwell_cmd, "SET ANTENNA DWELL", delay=0.15
            )
            if not dwell_ok:
                log.warning(
                    "[SCAN] SET ANTENNA DWELL not confirmed - module may fall "
                    "back to its own 4000ms/antenna default, which can make "
                    "round-robin timing less predictable within scan_timeout."
                )

            if use_ex_inventory:
                log.info(
                    "[SCAN] inventory_mode=AA58 (EX dense-mode inventory) - "
                    "SET RFMODE/SESSION/TARGET/Q skipped, module handles "
                    "anti-collision internally for this command."
                )
            else:
                send_command_checked(ser, CMD_SET_RFMODE,            "SET RFMODE",     delay=0.5)
                send_command_checked(ser, CMD_SET_SESSION,           "SET SESSION 1",  delay=0.3)
                send_command_checked(ser, CMD_SET_TARGET_DYNAMIC_AB, "SET TARGET AB")
                q_ok = send_command_checked(
                    ser, q_cmd,
                    "SET Q (%s%s)" % (q_mode_used, "=%d" % q_value_used if q_value_used is not None else "")
                )
                if not q_ok:
                    log.warning(
                        "[SCAN] SET Q not confirmed - module may keep whatever "
                        "Q setting was last successfully applied (could be a "
                        "previous cycle's value, static or dynamic)."
                    )

            send_command(ser, start_cmd)
            time.sleep(0.3)

            setup_elapsed = time.time() - start_time
            start_time        = time.time()
            last_new_tag_time = start_time

            elapsed_real_since_cycle_start = start_time - cycle_start
            remaining_budget = (
                SCAN_TIMEOUT_RAW - elapsed_real_since_cycle_start - ASYNC_TEARDOWN_RESERVE_SECONDS
            )

            if remaining_budget <= 0:
                MAX_SCAN_TIME = 0.0
                log.error(
                    "[SCAN] Overhead since cycle_start (%.2fs) has already consumed"
                    "the entire scan_timeout=%.1fs budget (after subtracting"
                    "teardown_reserve=%.1fs). The listen window is set to 0.00s -"
                    "NO time remaining to read tags in this cycle. "
                    "total duration is kept as close as possible to scan_timeout,"
                    "NOT forced through like the previous version (3.0s floor"
                    "has been removed). If this happens frequently, scan_timeout=%.1fs"
                    "is too small for the actual hardware overhead — increase it "
                    "scan_timeout at config.json.",
                    elapsed_real_since_cycle_start, SCAN_TIMEOUT_RAW,
                    ASYNC_TEARDOWN_RESERVE_SECONDS, SCAN_TIMEOUT_RAW
                )
            elif remaining_budget < ASYNC_LISTEN_TARGET:

                MAX_SCAN_TIME = remaining_budget
                log.info(
                    "[SCAN] Listen time di-FLEX-kan turun dari target %.1fs "
                    "menjadi %.2fs (bukan dipaksa minimum lagi) karena "
                    "overhead cycle ini %.2fs lebih besar dari biasanya "
                    "(scan_timeout=%.1fs, teardown_reserve=%.1fs). Total "
                    "durasi tetap dijaga <= scan_timeout.",
                    ASYNC_LISTEN_TARGET, MAX_SCAN_TIME,
                    elapsed_real_since_cycle_start, SCAN_TIMEOUT_RAW,
                    ASYNC_TEARDOWN_RESERVE_SECONDS
                )
            else:

                MAX_SCAN_TIME = ASYNC_LISTEN_TARGET

            log.info(
                "[SCAN] Setup took %.2fs (region/gen2/power/antenna/dwell%s/"
                "start-async). Real elapsed sejak cycle_start=%.2fs -> "
                "listen ADAPTIF disesuaikan jadi %.2fs (target_this_cycle=%.1fs, "
                "budget scan_timeout=%.1fs, teardown_reserve=%.1fs).",
                setup_elapsed, "" if use_ex_inventory else "/rfmode/session/target/q",
                elapsed_real_since_cycle_start, MAX_SCAN_TIME,
                ASYNC_LISTEN_TARGET, SCAN_TIMEOUT_RAW, ASYNC_TEARDOWN_RESERVE_SECONDS
            )

            log.info(
                "[SCAN] Started (%s) - max=%.2fs quiet=%.1fs dwell_ms=%d antenna_gate_open=%s",
                scan_inventory_mode, MAX_SCAN_TIME, QUIET_NEW_TAG, ANTENNA_DWELL, antenna_gate_open
            )

            while True:

                if os.path.exists("stop.flag"):
                    log.info("[SCAN] stop.flag detected - aborting")
                    aborted = True
                    try:
                        os.remove("stop.flag")
                    except Exception:
                        pass
                    break

                now     = time.time()
                elapsed = now - start_time

                if elapsed >= MAX_SCAN_TIME:
                    log.info("[SCAN] Max scan time %.2fs reached - stopping", MAX_SCAN_TIME)
                    break

                if stop_event and stop_event.is_set():
                    aborted = True
                    log.info("[SCAN] Stop event signalled - aborting")
                    break

                chunk = ser.read(16384)
                if chunk:
                    buffer.extend(chunk)

                tags, buffer = parse_fast_mode_correct(buffer)

                for tag in tags:
                    epc = tag["EPC"]
                    total_reads += 1

                    native_rc = epc_native_max_count.get(epc, 0)
                    if tag["COUNT"] > native_rc:
                        epc_native_max_count[epc] = tag["COUNT"]

                    if tag["RSSI"] is not None:
                        best_rssi = epc_best_rssi.get(epc)
                        if best_rssi is None or tag["RSSI"] > best_rssi:
                            epc_best_rssi[epc] = tag["RSSI"]

                        # NEW in 1.0.0(15): per-antenna best RSSI for the
                        # rssi.json diagnostic dump - see write_rssi_json().
                        if tag["ANT"] is not None:
                            ant_map = epc_antenna_rssi.setdefault(epc, {})
                            prev = ant_map.get(tag["ANT"])
                            if prev is None or tag["RSSI"] > prev:
                                ant_map[tag["ANT"]] = tag["RSSI"]

                    if epc not in epc_seen:
                        epc_seen[epc]     = 1
                        last_new_tag_time = now

                        elapsed_safe = max(elapsed, 0.001)
                        rate = len(epc_seen) / elapsed_safe
                        dup  = total_reads / len(epc_seen)

                        log.info(
                            "[%.1fs] ANT%s NEW #%03d EPC:%s RSSI:%s NativeRC:%s Rate:%.1f/s Total:%d Dup:%.1fx",
                            elapsed, tag['ANT'], len(epc_seen), epc, tag['RSSI'], tag['COUNT'],
                            rate, total_reads, dup
                        )
                    else:
                        epc_seen[epc] += 1

                unique         = len(epc_seen)
                time_since_new = now - last_new_tag_time
                dup_ratio      = total_reads / unique if unique > 0 else 1.0

                if STABLE_THRESHOLD > 0 and unique >= STABLE_THRESHOLD and dup_ratio > 15:
                    log.info(
                        "[SCAN] Stable stop: %d tags dup=%.1fx threshold=%d",
                        unique, dup_ratio, STABLE_THRESHOLD
                    )
                    break

                if time_since_new > QUIET_NEW_TAG:
                    log.info(
                        "[SCAN] Quiet stop: %d tags, no new tag for %.1fs (quiet_stop=%.1fs)",
                        unique, time_since_new, QUIET_NEW_TAG
                    )
                    break

    except Exception as e:
        log.error("[SCAN] Async scan error: %s", e)

    finally:
        try:
            with serial.Serial(PORT, baudrate=baud, timeout=0.05) as ser:
                send_command(ser, stop_cmd)
        except Exception:
            pass

        GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
        log.info("[SCAN] Async scan stopped")

    duration_real = time.time() - cycle_start

    # =====================================================
    #  DURATION CLAMP - NEW in 1.0.0(17)
    # =====================================================
    if duration_real > SCAN_TIMEOUT_RAW:
        log.warning(
            "[SCAN] duration_real=%.2fs melebihi scan_timeout=%.1fs dari "
            "config.json (selisih %.2fs, kemungkinan overhead teardown "
            "lebih besar dari ASYNC_TEARDOWN_RESERVE_SECONDS=%.1fs). "
            "Nilai duration yang DIPUBLISH di-clamp ke %.1fs agar tidak "
            "pernah melebihi scan_timeout.",
            duration_real, SCAN_TIMEOUT_RAW, duration_real - SCAN_TIMEOUT_RAW,
            ASYNC_TEARDOWN_RESERVE_SECONDS, SCAN_TIMEOUT_RAW
        )
        duration = int(round(SCAN_TIMEOUT_RAW))
    else:
        duration = int(round(duration_real))

    unique   = len(epc_seen)
    log.info(
        "[SCAN] Done unique=%d duration=%.1fs (published, clamped<=scan_timeout) "
        "duration_real=%.2fs aborted=%s antenna_gate_open=%s",
        unique, duration, duration_real, aborted, antenna_gate_open
    )

    if aborted:
        return {
            "epc_seen": {}, "duration": duration, "aborted": True,
            "antenna_gate_open": antenna_gate_open,
        }

    suspected_ghosts = [epc for epc, count in epc_seen.items() if count < MIN_READS_PER_SCAN]
    if suspected_ghosts:
        log.info(
            "[GHOST-FILTER] min_reads_per_scan=%d: dropped %d tag(s) read "
            "too few times to trust this cycle: %s",
            MIN_READS_PER_SCAN, len(suspected_ghosts),
            ", ".join("%s(x%d)" % (epc, epc_seen[epc]) for epc in suspected_ghosts)
        )

    weak_native_ghosts = [
        epc for epc in epc_seen
        if epc_native_max_count.get(epc, 0) < MIN_NATIVE_READ_COUNT
    ]
    if weak_native_ghosts:
        log.info(
            "[GHOST-FILTER] min_native_read_count=%d: dropped %d tag(s) whose "
            "best single-round module Read Count never reached the threshold: %s",
            MIN_NATIVE_READ_COUNT, len(weak_native_ghosts),
            ", ".join("%s(NativeRC=%d)" % (epc, epc_native_max_count.get(epc, 0)) for epc in weak_native_ghosts)
        )

    published_epc_seen = {
        epc: 1 for epc, count in epc_seen.items()
        if count >= MIN_READS_PER_SCAN
        and epc_native_max_count.get(epc, 0) >= MIN_NATIVE_READ_COUNT
    }

    if len(published_epc_seen) < RSSI_STRICT_COUNT_BOUNDARY:
        weak_rssi_ghosts = [
            epc for epc in published_epc_seen
            if epc_best_rssi.get(epc, -999) < RSSI_STRICT_DBM
        ]
        if weak_rssi_ghosts:
            log.info(
                "[GHOST-FILTER] rssi_threshold_strict=%ddBm (triggered: this "
                "cycle's count %d < rssi_threshold_strict_count=%d): dropped "
                "%d tag(s) whose best RSSI this scan never reached the "
                "strict threshold: %s",
                RSSI_STRICT_DBM, len(published_epc_seen), RSSI_STRICT_COUNT_BOUNDARY,
                len(weak_rssi_ghosts),
                ", ".join("%s(RSSI=%s)" % (epc, epc_best_rssi.get(epc)) for epc in weak_rssi_ghosts)
            )
            for epc in weak_rssi_ghosts:
                del published_epc_seen[epc]


    write_rssi_json(
        published_epc_seen, epc_antenna_rssi, epc_best_rssi,
        scan_meta={
            "timestamp": time.time(),
            "scan_timeout_s": SCAN_TIMEOUT_RAW,
            "duration_s": duration,
            "antennas_configured": ant_list_now,
        },
    )

    return {
        "epc_seen": published_epc_seen, "duration": duration, "aborted": False,
        "antenna_gate_open": antenna_gate_open,
    }


def scan_with_zero_confirmation(baud, stop_event=None, cycle_start=None):
    cfg_now           = load_config()
    if cycle_start is None:
        cycle_start = time.time()
    recheck_attempts  = int(cfg_now.get("zero_recheck_attempts", 0))
    recheck_delay     = float(cfg_now.get("zero_recheck_delay", 1.5))

    result   = run_async_scan(baud, stop_event=stop_event, cycle_start=cycle_start)
    attempts = 1

    if result["aborted"]:
        result["zero_confirmed"] = False
        result["scan_attempts"]  = attempts
        result["publish_safe"]   = False
        log.info("[SCAN] Aborted on attempt 1 - publish_safe=False (not eligible for zero-check)")
        return result

    if len(result["epc_seen"]) > 0:
        result["zero_confirmed"] = False
        result["scan_attempts"]  = attempts
        result["publish_safe"]   = True
        return result

    if recheck_attempts == 0:
        log.info(
            "[SCAN] ZERO tags on attempt 1/1 - zero_recheck_attempts=0 "
            "(default since 1.0.0(8)), publishing immediately as confirmed "
            "zero. No second scan performed - scan_timeout stays a hard "
            "ceiling."
        )
    else:
        log.warning(
            "[SCAN] ZERO tags on attempt 1/%d - treating as UNCONFIRMED. Nothing "
            "will be published until it's re-verified with %d recheck attempt(s).",
            recheck_attempts + 1, recheck_attempts
        )

    for i in range(recheck_attempts):
        if stop_event and stop_event.is_set():
            log.info("[SCAN] Stop requested during zero-recheck - aborting recheck loop")
            result["zero_confirmed"] = False
            result["scan_attempts"]  = attempts
            result["publish_safe"]   = False
            return result

        power_cycle_reader(off_time=recheck_delay, on_settle=1.0)

        recheck_baud = try_bootloader_scan() or baud

        recheck_cycle_start = time.time()
        result    = run_async_scan(recheck_baud, stop_event=stop_event, cycle_start=recheck_cycle_start)
        attempts += 1

        if result["aborted"]:
            result["zero_confirmed"] = False
            result["scan_attempts"]  = attempts
            result["publish_safe"]   = False
            log.info("[SCAN] Recheck attempt %d aborted - publish_safe=False", i + 1)
            return result

        if len(result["epc_seen"]) > 0:
            log.info(
                "[SCAN] Recheck attempt %d/%d found %d tag(s) - the earlier "
                "ZERO was a false alarm. Using this reading, NOT zero.",
                i + 1, recheck_attempts, len(result["epc_seen"])
            )
            result["zero_confirmed"] = False
            result["scan_attempts"]  = attempts
            result["publish_safe"]   = True
            return result

        log.warning("[SCAN] Recheck attempt %d/%d STILL zero.", i + 1, recheck_attempts)

    if recheck_attempts == 0:
        log.info(
            "[SCAN] Zero confirmed on the single attempt (zero_recheck_attempts=0) - "
            "publishing as genuinely empty."
        )
    else:
        log.error(
            "[SCAN] CONFIRMED ZERO after %d total attempt(s) (1 initial + %d "
            "recheck(s)), all agreeing the compartment is genuinely empty. "
            "This result is now safe to publish.",
            attempts, recheck_attempts
        )
    result["zero_confirmed"] = True
    result["scan_attempts"]  = attempts
    result["publish_safe"]   = True
    return result


def main(stop_event=None):
    acquire_reader_mutex()
    cycle_start = time.time()
    try:
        log.info("[READER] Starting... (reader.py %s)", __version__)

        power_cycle_before_first_scan()

        set_baudrate()
        baud = try_bootloader_scan()
        if not baud:
            log.error("[READER] Failed to connect to reader")
            return None

        info = log_module_version(baud)
        if INVENTORY_MODE == "AA58" and info:
            warn_if_region_unsupported_for_ex(info.get("region"))
        if info:
            warn_if_firmware_predates_rssi_filter(
                info.get("firmware_date_raw"), load_config().get("rssi_threshold")
            )

        result = scan_with_zero_confirmation(baud, stop_event, cycle_start=cycle_start)

        if not result["publish_safe"]:
            log.warning(
                "[READER] Result NOT safe to publish (unconfirmed zero or "
                "aborted scan) - discarding this cycle. attempts=%d",
                result.get("scan_attempts", 1)
            )
        else:
            log.info(
                "[READER] Result safe to publish: unique=%d zero_confirmed=%s "
                "attempts=%d antenna_gate_open=%s",
                len(result["epc_seen"]), result["zero_confirmed"],
                result["scan_attempts"], result["antenna_gate_open"]
            )

        return result

    finally:
        GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
        release_reader_mutex()
        log.info("[READER] RF disabled, mutex released")


def check_version_only():
    log.info("[READER] --version check (reader.py %s)", __version__)
    baud = try_bootloader_scan()
    if not baud:
        print("Failed to connect to reader - check serial connection/PORT in config.json")
        GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
        return

    info = log_module_version(baud)
    GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)

    if not info:
        print("Failed to read module version (no/invalid response).")
        return

    fw_display = info["firmware_version_decoded"] or info["firmware_version"]
    print("=" * 60)
    print("reader.py script version : %s" % __version__.split(" - ")[0])
    print("-" * 60)
    print("Module firmware version  : %s" % fw_display)
    print("Firmware compiled        : %s" % info["firmware_date"])
    print("Bootloader version       : %s" % info["bootloader_version"])
    print("Chip type                : %s" % info["chip_type"])
    print("Antenna ports (physical) : %s" % info["antenna_port_count"])
    print("Certification region     : %s" % info["region"])
    print("Hardware revision        : %s" % info["hardware_revision"])
    print("Supported protocol       : %s" % info["supported_protocol"])
    print("Antenna dwell time       : %dms (manual/static default - actual "
          "scans auto-tune this per cycle unless auto_tune_timing=false)"
          % ANTENNA_DWELL_MS)
    print("RF-level Select filter   : see [CONFIG] log line above for status")
    print("Inventory command mode   : %s%s" % (
        INVENTORY_MODE,
        " (dense_mode=%s)" % bool(cfg.get("dense_mode", True)) if INVENTORY_MODE == "AA58" else ""
    ))
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if "--version" in sys.argv or "-v" in sys.argv:
        check_version_only()
    else:
        main()
