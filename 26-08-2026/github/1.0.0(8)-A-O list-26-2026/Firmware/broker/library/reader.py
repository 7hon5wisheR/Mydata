# -*- coding: utf-8 -*-
__version__ = "1.0.0(7) "

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
#      Version                    : 1.0.0(7) 
#      Date Created               : 08-05-2026
#      Date Update                : 15-08-2026
#      Author                     : Saifuddin
#      Changes vs 1.0.0(5)        :
#        1) CHANGED: scan_with_zero_confirmation()'s "zero_recheck_attempts"
#           config default changed from 1 to 0. A recheck attempt calls
#           run_async_scan() AGAIN IN FULL - a second complete
#           scan_timeout-length listening window, plus power-cycle/baud-
#           reconnect overhead on top - not a short extra wait. With the
#           old default of 1, ANY cycle that read zero tags (correctly
#           or, worse, due to an over-aggressive filter - see the
#           min_native_read_count incident below) silently doubled the
#           wall-clock time end users see: scan_timeout=12s in
#           config.json became ~24s+ in practice. That's a silent
#           violation of what scan_timeout is documented/expected to
#           mean (a ceiling, not a per-attempt duration) and was the
#           direct cause of user-facing "why is it taking 24s when I set
#           12s" complaints. zero_recheck_attempts=0 is now the default -
#           exactly one scan, scan_timeout is a hard ceiling again. Set
#           "zero_recheck_attempts": 1+ in config.json ONLY if you
#           specifically want the old false-negative protection back and
#           are OK with the corresponding multiplied worst-case duration.
#
#      *** OPERATIONAL INCIDENT, config.json only, no code was wrong ***
#      MIN_NATIVE_READ_COUNT_DEFAULT (1.0.0(5)) is documented as
#      default=1 (disabled) specifically because native Read Count
#      behavior is deployment-dependent (see the constant's comment) -
#      it must be validated against real logs before enabling. On this
#      hardware (Session=1, Target=AB), EVERY tag's native Read Count is
#      always 1 - real tag or ghost, no exception - because Gen2
#      inventoried tags flip to state B and don't re-arbitrate again
#      within one dwell window, so the module never gets a second
#      same-round detection to count. With "min_native_read_count": 2
#      left enabled in config.json on this hardware, EVERY scan's
#      published result became empty regardless of actual tag count
#      (confirmed: a cabinet with 288 real tags was reported as
#      confirmed-empty). Combined with zero_recheck_attempts defaulting
#      to 1 (now fixed above), this made scans BOTH wrong AND slow on
#      every single cycle. Action: set "min_native_read_count": 1 (or
#      remove the key) in config.json on any deployment using
#      Session 1 + Target AB before relying on this field again.
#
#        1) CHANGED: RSSI ghost-tag filtering moved from software to the
#           module itself. Doc sec 10.8 "Set RSSI filter threshold
#           (0xAA5B)" ("Need to update the firmware 202404024 or later")
#           is a genuine RF/firmware-level filter: "when the signal
#           strength of the read tag is weaker than a certain value, the
#           tag data is not uploaded (ignored)" - i.e. the module itself
#           never sends a weak read over serial in the first place.
#           1.0.0(5)'s RSSI_THRESHOLD was software-only: the module sent
#           every read regardless of strength, and parse_fast_mode_correct()
#           dropped weak ones AFTER they'd already crossed the serial
#           link and been parsed. Same end result, but wasted serial
#           bandwidth/CPU on data that was going to be thrown away, and
#           wasn't the vendor-documented mechanism for this.
#           get_rssi_filter_command(config) builds the 0xAA5B command
#           (byte format reverse-engineered and verified EXACTLY against
#           all 4 of the doc's own worked examples - get/cancel/enable
#           -40dBm/receive - see build_rssi_filter_subdata() below) and
#           it's now sent once per scan cycle in run_async_scan(), same
#           position as SET REGION/GEN2/POWER etc. config.json's
#           "rssi_threshold" key is unchanged (None/absent = filtering
#           disabled, an integer dBm e.g. -55 = enabled) - only WHERE the
#           filtering happens has changed, not how it's configured.
#           parse_fast_mode_correct() no longer does its own RSSI
#           comparison/drop - RSSI is still parsed out of the metadata
#           and reported in the log line, but is no longer used to
#           accept/reject a read in software, since the module won't
#           send a sub-threshold read to filter in the first place.
#        2) REMOVED: remove_confirm_cycles / apply_remove_debounce() and
#           the "remove-debounce" (per-tag hysteresis) feature entirely,
#           including tag_debounce_state.json. This was purely an
#           application-layer smoothing heuristic (a tag missing on one
#           cycle was kept in the PUBLISHED result for extra cycles
#           before being allowed to disappear) with no basis anywhere in
#           the EX10 protocol doc - the module has no concept of
#           "remove-debounce" for its own inventory results. main() now
#           publishes result["epc_seen"] exactly as scan_with_zero_
#           confirmation() returned it, with no extra smoothing layer
#           on top. Any add/remove flicker this used to paper over
#           should now be addressed at the RF/protocol layer (0xAA5B
#           RSSI filter above, min_reads_per_scan, static Q, AA58 dense
#           mode, power/dwell tuning) - which is what those layers were
#           designed to be verified against the doc for in the first
#           place - rather than hidden behind an extra untracked-by-spec
#           publish delay.
#
#      Version                    : 1.0.0(5) 
#      Changes vs 1.0.0(4)        :
#        1) NEW: RSSI_THRESHOLD was a hardcoded constant (always None) -
#           never actually wired to config.json, a dead knob. Now reloaded
#           fresh every scan cycle from config.json's "rssi_threshold"
#           (dBm, e.g. -55, default None/disabled = old behavior). Weak
#           reads below this are dropped in parse_fast_mode_correct()
#           before ever reaching epc_seen.
#        2) NEW: "min_reads_per_scan" (default 1 = old behavior,
#           unchanged). epc_seen's values already tracked the real
#           per-tag read count for the scan window - they were just
#           discarded (reset to 1) when building the return dict. Now
#           used as a trust filter: a tag must be read at least
#           min_reads_per_scan times in this single scan_timeout window
#           to be included in the published result. A genuinely-present
#           tag in a dense cabinet is normally read many times over a
#           10-12s scan (dup ratios 3-5x, per log2.txt); a "ghost" - RF
#           bleed from a neighbouring cabinet/compartment, a stray tag
#           near the antenna, an occasional corrupted-but-CRC-valid read
#           - is far more likely to be seen only once or twice in the
#           whole window. This complements remove_confirm_cycles (which
#           only helps once a tag is already missing on a LATER cycle);
#           this one catches a ghost misread in the SAME cycle it
#           appears - the exact symptom reported: 128 physical tags
#           reading as 129-131, with the extra EPC(s) not matching the
#           installed batch's serial range.
#           Both new keys are logged every cycle in the "[CONFIG]" line
#           and any filtered-out ghost candidates are logged under
#           "[GHOST-FILTER]" for tuning visibility.
#
#      Version                    : 1.0.0(3) -select command
#      Changes vs 1.0.0(2)        :
#        1) NEW: RF-level Select filter for CMD_START_ASYNC_FULL (0xAA48).
#           Root cause of the earlier failed attempt ("all tags disappeared"):
#           this module's extended commands (CommandCode=0xAA) are NOT just
#           "Marker + Subcommand + Data + CRC-16" as the abstract frame
#           format in sec 3.1 implies. Per sec 3.2 and the worked example in
#           Appendix 6, every extended command's Data field is actually:
#               Subcommand Marker(10, "Moduletech") + Subcommand Code(2)
#               + Subcommand Data(N) + SubCRC(1) + Terminator(1, always 0xBB)
#           SubCRC = low byte of the sum of every byte from Subcommand Code
#           through the end of Subcommand Data (doc Appendix 6). This byte
#           was reverse-engineered and verified EXACTLY against this file's
#           own pre-existing CMD_START_ASYNC_FULL (SubCRC=B4) and
#           CMD_START_ASYNC (SubCRC=75) commands - both reproduce byte-for-
#           byte with the formula above. Any hand-built AA48 command that
#           skips SubCRC/Terminator (or gets SubCRC wrong) is a malformed
#           frame the module quietly ignores or mishandles - explaining why
#           tags stopped appearing entirely on the earlier attempt.
#        2) NEW: build_select_filter_realsubdata() embeds an EPC-bank Select
#           filter (doc sec 5.1.1/5.1.2, Select-Option Bits=0x04) directly
#           inside CMD_START_ASYNC_FULL's own Data field - this is a
#           genuine RF-level filter: the module itself only interrogates
#           tags whose EPC matches the configured prefix, so non-matching
#           tags never participate in the Gen2 inventory round at all and
#           can no longer cause Q/collision overhead for the tags actually
#           wanted. This is different from (and complements) the existing
#           config.json "rfid_filter", which only discards already-read
#           data in software AFTER the module has already spent RF airtime
#           inventorying every tag, filtered or not.
#        3) get_start_async_full_command(config): if config.json's
#           "rfid_filter" list has EXACTLY ONE hex prefix, that prefix is
#           used as the RF-level Select filter automatically - no new
#           config.json key required. If "rfid_filter" has zero or more
#           than one entry, RF-level Select is skipped (this module's
#           single-rule 0xAA48 Select can only match one prefix; multiple
#           prefixes would need the separate 0xAA4C multi-label filter,
#           not implemented here) and the command falls back to the old
#           unfiltered behavior - software-only rfid_filter still applies
#           as before in that case.
#        4) No other logic changed from 1.0.0(2).
#
#      Version                    : 1.0.0(4) - EX dense-mode inventory (0xAA58)
#      Changes vs 1.0.0(3)        :
#        1) NEW: alternative inventory command path, 0xAA58 "EX Asynchronous
#           Inventory" (doc sec 5.6.4), selectable via config.json's new
#           "inventory_mode" key ("AA48" [default, unchanged behavior] or
#           "AA58"). AA58 is a SEPARATE command from AA48 - it is not a
#           flag on AA48. Per the doc:
#             - "Suitable for scenarios with a large number of tags."
#             - Its own Data field carries an "ExConfigData" byte: 0 =
#               dense-tag mode ("mainly used for reading more and reading
#               all tags, a large number of tags or in complex
#               environments"), 1 = sparse mode (few/easy tags, faster).
#             - It does NOT accept Session/Target/Q/RF_mode - the module
#               handles Gen2 anti-collision parameters internally for this
#               command, so those 0x9B/RF-mode commands are skipped when
#               "inventory_mode": "AA58" is selected (sending them would
#               simply have no effect, per the doc).
#             - It does NOT support the AA48 Select/rfid_filter RF-level
#               filter (doc: "Filtering is not supported").
#             - Only certified for CHINA/CE/INDIA/RUSSIA/PHILIPPINES/
#               JAPAN(all)/ISRAEL certification regions (doc sec 2.2) - a
#               runtime warning is logged (not a hard stop, since this
#               script doesn't currently drive certification region from
#               config.json) if the module reports an unsupported region.
#           Stop command is the matching 0xAA59 (built the same way as
#           0xAA49 is for AA48).
#        2) No other logic changed from 1.0.0(3). Default behavior (no
#           "inventory_mode" key in config.json) is UNCHANGED - AA48 is
#           still used exactly as in 1.0.0(3).
#        3) FIX: scan_timeout was being measured from BEFORE the per-cycle
#           setup commands (SET REGION/GEN2/POWER/ANT/DWELL/RFMODE/SESSION/
#           TARGET/Q/START_ASYNC), which measurably take ~2.5s on the AA48
#           path (~1.4s on AA58) due to their own serial-response delays.
#           So a configured "scan_timeout": 12 was only leaving the module
#           ~9.5s (AA48) of ACTUAL listening time, not 12s - confirmed
#           against log2.txt, where the first tag is logged at elapsed
#           [3.0s], not [0.x s]. start_time/last_new_tag_time are now reset
#           right after START_ASYNC/START_ASYNC_EX is actually sent, so
#           scan_timeout now means N seconds of real listening time on
#           either inventory path. Net effect: each scan cycle's total wall
#           time is now (setup_time + scan_timeout) instead of exactly
#           scan_timeout - the module genuinely listens for the full
#           configured duration, it just now takes ~1.4-2.5s longer overall
#           to do so. The new "[SCAN] Setup took ...s" log line reports the
#           measured setup overhead every cycle for visibility.
#        4) NEW: remove-debounce (per-tag hysteresis), config key
#           "remove_confirm_cycles" (default 1 = old behavior, unchanged).
#           Since utilitys.py's add/remove is a plain diff of reader.py's
#           output vs its previous output, a single RF-layer miss on any
#           tag becomes a "remove" downstream no matter how well AA48/
#           AA58/static-Q/dwell/power are tuned - that can be reduced but
#           never fully eliminated at the RF layer with passive RFID in a
#           dense population. This layer keeps a missed tag inside the
#           PUBLISHED result for up to (remove_confirm_cycles - 1) extra
#           cycles before it's allowed to actually disappear, so a single
#           miss no longer flips straight to "remove". State persists in
#           tag_debounce_state.json next to config.json. "add" is not
#           debounced - a new tag is reported the moment it's seen.
# ======================================================================================================================================

# === log ===
from logger import get_logger
log = get_logger("reader")
# ===========


# =====================================================
#  DEFAULT VALUES - edit here to change the default. Still overridable
#  per-cabinet from config.json if that key is present there - if not
#  present, the value below is used. Same pattern as scan_timeout,
#  antenna_dwell_ms, etc. elsewhere in this file.
# =====================================================

# --- RSSI ghost-tag filter (0xAA48/AA58 tag reads) ---
# Reads below this RSSI (dBm) are dropped before ever reaching epc_seen.
# None = disabled (no RSSI filtering) - this is the safe default, since
# a wrong threshold can drop genuinely weak-but-real tags. Only set a
# number (e.g. -55) after checking real RSSI values in the logs for this
# cabinet's genuine tags vs the ghost tags, so the threshold sits between
# them. Override in config.json with "rssi_threshold": -55 (or omit to
# use this default).
RSSI_THRESHOLD_DEFAULT = None

# --- adaptive RSSI threshold: TIGHTER filter when cabinet is nearly
# empty, to reject ghost tags ---
# When the PREVIOUS published scan result had fewer than
# rssi_threshold_strict_count tags (cabinet nearly empty), THIS cycle
# uses rssi_threshold_strict (a HIGHER/less-negative dBm value than the
# normal rssi_threshold - i.e. a STRICTER cutoff) instead of the normal
# "rssi_threshold". Rationale: ghost tags (RF bleed from a neighbouring
# compartment, stray reflections, etc.) tend to show up as weak reads
# once the real tag population thins out - a full cabinet's own RF
# environment normally masks them, but an empty/near-empty cabinet lets
# them through. Tightening the module-level RSSI filter (e.g. -58dBm
# instead of -69dBm) makes the module ITSELF refuse to upload those weak
# ghost reads in the first place (they never even cross the serial
# link), at the cost of also dropping any genuinely weak-but-real tag in
# that same dBm range - an acceptable tradeoff specifically when few
# tags are expected anyway. Override in config.json with
# "rssi_threshold_strict" / "rssi_threshold_strict_count", or omit
# either/both to use these defaults.
RSSI_THRESHOLD_STRICT_DEFAULT       = -95
RSSI_THRESHOLD_STRICT_COUNT_DEFAULT = 10

# --- min_reads_per_scan ghost-tag filter ---
# A tag must be read at least this many times within a SINGLE scan
# window before it's trusted and included in the published result. A
# genuinely-present tag in a dense cabinet is normally read many times
# over a 10-12s scan (dup ratios 3-5x are typical). A "ghost" - RF bleed
# from a neighbouring cabinet, a stray tag near the antenna, an
# occasional corrupted-but-CRC-valid read - is far more likely to be
# seen only once or twice in the whole window. 1 = disabled (old
# behavior, any tag seen even once is trusted). Override in config.json
# with "min_reads_per_scan": 3 (or omit to use this default).
MIN_READS_PER_SCAN_DEFAULT = 3

# --- native "Read Count" ghost-tag filter (NEW in 1.0.0(7)) ---
# EX10's own per-report metadata "Read Count" (doc sec 5.1.3, BIT0 of
# Metadata Flags): "The number of times the inventory was taken during
# the inventory time" - i.e. how many times the MODULE ITSELF detected
# that tag inside a single inventory round, before compiling that one
# report. This is a stronger/finer-grained signal than
# min_reads_per_scan above (which only counts separate REPORT PACKETS
# across the whole scan_timeout window, a reader1-side heuristic) - a
# tag that's cleanly, strongly coupled to the antenna typically racks
# up a high native Read Count within a single round, while a weak/
# borderline ghost read usually only manages Read Count=1 even when it
# does get reported. This data was already being parsed out of every
# packet (METADATA_FLAGS has always had BIT0 set) but was previously
# discarded after being read - it's used starting 1.0.0(7).
# Default 1 = disabled (old behavior unchanged - every native Read
# Count value is accepted). Override in config.json with
# "min_native_read_count": 2 (or higher) once you've checked real
# NativeRC values in the logs for your genuine vs ghost tags.
MIN_NATIVE_READ_COUNT_DEFAULT = 1


# NOTE (1.0.0(6)): the remove-debounce / REMOVE_CONFIRM_CYCLES_DEFAULT
# feature has been removed entirely - see the 1.0.0(6) changelog at the
# top of this file. It was an application-layer publish-delay heuristic
# with no basis in the EX10 protocol doc. main() now publishes exactly
# what scan_with_zero_confirmation() returns, with no extra smoothing.

# --- auto-tuning (dwell time / quiet-stop timing, see compute_auto_timing()) ---
# AUTO_TUNE_PASSES_DEFAULT: target number of full round-robin passes
# across all antennas that should fit inside scan_timeout.
# AUTO_TUNE_QUIET_CYCLES_DEFAULT: how many full round-robin cycles of
# "no new tag seen" must elapse before the scan calls it done early.
# Override in config.json with "auto_tune_passes"/"auto_tune_quiet_cycles"
# (or omit either/both to use these defaults).
AUTO_TUNE_PASSES_DEFAULT       = 3
AUTO_TUNE_QUIET_CYCLES_DEFAULT = 2.5

# NOTE (1.0.0(6)): there is no more module-level RSSI_THRESHOLD global here.
# RSSI filtering is now done by the module itself via the 0xAA5B command,
# rebuilt fresh from config.json every scan cycle by get_rssi_filter_command()
# (see the RSSI FILTER section further below) - RSSI_THRESHOLD_DEFAULT above
# is still the code default used there, it's just no longer mirrored into a
# global that parse_fast_mode_correct() reads.


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
TIMEOUT       = 1.5
TRY_BAUDRATES = [921600]

GPIO.setmode(GPIO.BCM)
GPIO.setup(SHUTDOWN_GPIO, GPIO.OUT)
GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)


# =====================================================
#  COMMANDS - fixed (unchanged from 0.0.0.1)
# =====================================================
CMD_START_ASYNC      = bytes.fromhex("FF13AA4D6F64756C6574656368AA48000000800375BB4D30")  # epc only
CMD_STOP_ASYNC       = bytes.fromhex("FF0EAA4D6F64756C6574656368AA49F3BB0391")
CMD_SET_BAUD         = bytes.fromhex("FF14AA4D6F64756C6574656368AA400601000E10000FBB799F")
CMD_START_FIRMWARE   = bytes.fromhex("FF00041D0B")
CMD_SET_REGION       = bytes.fromhex("FF0197014BBC")
CMD_SET_PROTOCOL_GEN2 = bytes.fromhex("FF02930005517D")

# NOTE: CMD_START_ASYNC_FULL is NO LONGER a fixed constant as of 1.0.0(4).
# It is now built dynamically by get_start_async_full_command(config) further
# below (see the CONFIG section), so it can embed an RF-level Select filter
# when config.json's "rfid_filter" has exactly one prefix. The unfiltered
# byte pattern this used to be (Search Flags=0x8003, no Select fields) is
# still exactly what gets built when no RF-level filter applies.

CMD_GET_VERSION = bytes.fromhex("FF00031D0C")


def _calc_crc(msgbuf: bytes) -> int:
    """
    CRC-16/XMODEM per Appendix 4 of the protocol doc. Byte 0 (0xFF header)
    is skipped.

    Verified byte-for-byte against MULTIPLE worked examples straight from
    the protocol doc (not just one):
      - sec 7.1 example 2 (enable ant 1+4):        ... -> CRC 2BC6
      - sec 7.1 example 3 (set power ant 2+3):      ... -> CRC F2F5
      - sec 7.3 example   (set antenna dwell 5s):   ... -> CRC D5AB
      - sec 7.8 example 1 (set session 1):          ... -> CRC DCE9
      - sec 7.8 example 2 (set target B):           ... -> CRC A2FC
      - sec 7.8 example 3 (set RF mode 0x6F):       ... -> CRC DE87
      - sec 7.8 example 4 (set static Q=3):         ... -> CRC 80AC
      - Appendix 4 (receiving command example):     ... -> CRC 635C
      - sec 5.6.2.2 (heartbeat data packet):        ... -> CRC 1724
      - sec 5.6.2.3 (polling cycle data packet):    ... -> CRC F575
    All reproduce exactly, so this implementation itself is correct and is
    now ALSO used to validate incoming (received) frames in
    parse_fast_mode_correct() - the doc's own Appendix 4 worked example is
    explicitly a *receiving* command, confirming the same algorithm
    applies to frames sent BY the module, not just frames sent TO it.
    """
    calc_crc = 0xFFFF
    for i in range(1, len(msgbuf)):
        b = msgbuf[i]
        for bit in range(7, -1, -1):
            xor_flag = (calc_crc >> 15) & 1
            calc_crc = ((calc_crc << 1) | ((b >> bit) & 1)) & 0xFFFF
            if xor_flag:
                calc_crc ^= 0x1021
    return calc_crc


# =====================================================
#  EXTENDED-COMMAND FRAME BUILDER (CommandCode=0xAA) - NEW in 1.0.0(4)
#
#  Per doc sec 3.2 "Extended Command Communication Protocol Format" and the
#  worked example in Appendix 6, an extended command's Data field is:
#      Subcommand Marker(10, ASCII "Moduletech")
#      + Subcommand Code(2)
#      + Subcommand Data(N)
#      + SubCRC(1)
#      + Terminator(1, always 0xBB)
#  SubCRC = low byte of (sum of every byte from Subcommand Code through the
#  end of Subcommand Data) - doc Appendix 6, GetSubcrc() C reference.
#
#  Verified byte-for-byte against this file's OWN pre-existing commands:
#    - CMD_START_ASYNC_FULL: subcmd=AA48, subdata=003F008003 -> SubCRC=B4
#      (matches the hardcoded 1.0.0(3) byte string exactly)
#    - CMD_START_ASYNC:      subcmd=AA48, subdata=0000008003 -> SubCRC=75
#      (matches the hardcoded 1.0.0(3) byte string exactly)
#  Both reproduce their original hand-written hex exactly using this
#  formula, confirming it is correct for this module.
# =====================================================
MODULETECH_MARKER = bytes.fromhex("4D6F64756C6574656368")  # ASCII "Moduletech"
EXT_TERMINATOR     = 0xBB


def get_subcrc(data: bytes) -> int:
    """doc Appendix 6: sum of bytes (Subcommand Code..end of Subcommand Data), low byte only."""
    return sum(data) & 0xFF


def build_ext_command(subcmd: bytes, subdata: bytes) -> bytes:
    """
    Builds a full extended-command frame: Header + DataLength + 0xAA +
    [Marker(10) + SubcommandCode(2) + SubcommandData(N) + SubCRC(1) +
    Terminator(0xBB)] + CRC-16(2). See module docstring above for the
    verification this format was checked against.
    """
    if len(subcmd) != 2:
        raise ValueError("subcmd must be exactly 2 bytes")
    subcrc = get_subcrc(subcmd + subdata)
    data_field = MODULETECH_MARKER + subcmd + subdata + bytes([subcrc, EXT_TERMINATOR])
    frame_wo_crc = bytes([0xFF, len(data_field), 0xAA]) + data_field
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


# =====================================================
#  ANTENNA - config-driven ("antenna" in config.json)
#
#  Shared parser used by ENABLE ANT (0x91/Option=0x02) and SET POWER
#  (0x91/Option=0x04), so the two can never silently drift apart.
# =====================================================
class InvalidAntennaError(Exception):
    """Raised when config.json has a missing/invalid/out-of-range 'antenna' list."""
    pass


def parse_antenna_list(config):
    """Parses config["antenna"] into a sorted list of unique ints (1-32)."""
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
    """
    Builds the 0x91 command frame (Option=0x02) enabling exactly the given
    antenna numbers (1-32), per section 7.1 of the protocol doc. Works for
    ANY combination/count - cross-checked against the doc's own worked
    example (antennas 1+4 -> CRC 2BC6, reproduced exactly).
    """
    ant_list = sorted(set(ant_list))
    if not ant_list:
        raise InvalidAntennaError("antenna list is empty")
    for a in ant_list:
        if not (1 <= a <= 32):
            raise InvalidAntennaError("antenna id %d is out of valid range 1-32" % a)

    payload = bytes([0x02])  # Option = 0x02 (enable single or multiple antennas)
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


# =====================================================
#  TX POWER - config-driven ("power" in config.json, value in dBm)
#
#  Built dynamically from config.json's real "antenna" list via
#  parse_antenna_list(), for Option=0x04 ("set transmit/receive power +
#  antenna stabilization time"). Per antenna block = TX Ant(1) +
#  Read Power(2) + Write Power(2) + Setting Time(2) = 7 bytes (doc
#  section 7.1, "N*7"; the doc explicitly notes the Setting Time value
#  itself has no functional effect when Option=0x04, but the 7-byte block
#  layout is required and doc-correct).
# =====================================================
class InvalidPowerError(Exception):
    """Raised when config.json has a "power" value outside the supported range."""
    pass


POWER_MIN_DBM = 25
POWER_MAX_DBM = 33

# Setting Time (antenna stabilization time) field - constant across every
# dBm level and every antenna.
POWER_SETTING_TIME = 0x01F4


def build_power_command(ant_list, dbm):
    """
    Builds the 0x91 command frame (Option=0x04) setting TX/RX power for
    EXACTLY the given antenna numbers (1-32), at `dbm` dBm. Read Power and
    Write Power are both set to dbm*100 (0.01dBm units).
    """
    ant_list = sorted(set(ant_list))
    if not ant_list:
        raise InvalidAntennaError("antenna list is empty (cannot build power command)")
    for a in ant_list:
        if not (1 <= a <= 32):
            raise InvalidAntennaError("antenna id %d is out of valid range 1-32" % a)

    power_val = int(round(dbm * 100))
    payload = bytes([0x04])  # Option = 0x04 (set power + stabilization time)
    for a in ant_list:
        payload += (
            bytes([a])
            + power_val.to_bytes(2, "big")            # Read Power
            + power_val.to_bytes(2, "big")            # Write Power
            + POWER_SETTING_TIME.to_bytes(2, "big")   # Setting Time
        )

    data_length = len(payload)
    frame_wo_crc = bytes([0xFF, data_length, 0x91]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_power_command(config):
    """
    Reads config["power"] (integer dBm, POWER_MIN_DBM..POWER_MAX_DBM) and
    config["antenna"] (via parse_antenna_list), then builds SET POWER for
    EXACTLY the antennas configured - any count works automatically, no
    manual table entry required.
    """
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


# =====================================================
#  ANTENNA DWELL TIME - command builder for 0x95 (doc section 7.3),
#  Option=0x02 ("antenna dwell time").
#
#  The antenna switching logic: "If no more tags are inventoried or the
#  dwell time is reached, the module will switch to the next antenna for
#  reading." If this command is never sent, the module's own factory
#  default is 4000ms/antenna (doc, sec 7.3 and sec 8.1: "if it is 0, the
#  default is 4 seconds").
#
#  Frame/CRC cross-checked against the doc's own worked example:
#    "Set the antenna dwell time to 5 seconds": FF 05 95 02 00001388 D5AB
#    build_dwell_command(5000) reproduces this exactly, CRC included.
#
#  As of 1.0.0(3), the value actually SENT to the module every scan cycle
#  is normally computed by compute_auto_timing() (see below), not read
#  directly from config.json - see run_async_scan(). DWELL_DEFAULT_MS
#  below is only used as a manual fallback when "auto_tune_timing":
#  false, or if antenna_dwell_ms is present but invalid.
# =====================================================
class InvalidDwellError(Exception):
    """Raised when a dwell value is outside the doc's valid range."""
    pass


DWELL_MIN_MS     = 20      # doc: value range is 20-60000
DWELL_MAX_MS     = 60000   # doc: "which means the maximum value is one minute"
DWELL_DEFAULT_MS = 2000    # manual-mode fallback (deliberately shorter than
                           # the module's own 4000ms/antenna default)


def build_dwell_command(dwell_ms):
    """
    Builds the 0x95 command frame (Option=0x02) setting the antenna dwell
    time to `dwell_ms` milliseconds. Verified against the doc's own worked
    example for 5000ms (CRC D5AB, reproduced exactly).
    """
    dwell_ms = int(dwell_ms)
    if not (DWELL_MIN_MS <= dwell_ms <= DWELL_MAX_MS):
        raise InvalidDwellError(
            "antenna_dwell_ms=%d out of valid range %d-%d" % (dwell_ms, DWELL_MIN_MS, DWELL_MAX_MS)
        )

    payload = bytes([0x02]) + dwell_ms.to_bytes(4, "big")  # Option(1) + Timeout(4)
    data_length = len(payload)
    frame_wo_crc = bytes([0xFF, data_length, 0x95]) + payload
    crc = _calc_crc(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def get_dwell_command(config):
    """
    MANUAL mode only (used when config.json has "auto_tune_timing": false).
    Reads config["antenna_dwell_ms"] (optional, 20-60000ms). Defaults to
    DWELL_DEFAULT_MS (2000ms) if the key is absent from config.json - no
    config.json edit is required for this to work.
    """
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


# =====================================================
#  AUTO-TUNING - dwell time + quiet-stop ("new tag") timeout, computed
#  fresh every scan cycle from scan_timeout x the currently configured
#  antenna count. NEW in 1.0.0(3).
#
#  Why: with N antennas enabled, one full round-robin cycle takes up to
#  N * dwell_ms in the worst case (doc sec 7.1 note: "After polling all
#  antennas in turn, it will start again from the antenna with the lowest
#  serial number"). If dwell_ms is left at a fixed/manual value, editing
#  scan_timeout or the antenna list in config.json can silently push that
#  full-cycle time close to (or past) scan_timeout, so the hard
#  `elapsed >= scan_timeout` cutoff lands at a different point in the
#  round-robin cycle on every run - producing run-to-run variance in which
#  tags get read even with nothing physically changed. Auto-tuning removes
#  the need to hand-tune this every time scan_timeout or the antenna count
#  changes.
#
#  Config keys (all optional):
#    "auto_tune_timing"       bool,  default true.  false = old manual
#                              behavior (antenna_dwell_ms / scan_newtag
#                              read directly from config.json, as in
#                              1.0.0(2)).
#    "auto_tune_passes"       int,   default 3.      Target number of full
#                              round-robin passes across all antennas that
#                              should fit inside scan_timeout.
#    "auto_tune_quiet_cycles" float, default 1.     How many full
#                              round-robin cycles of "no new tag seen"
#                              must elapse before the scan calls it done
#                              early (quiet-stop), expressed as a multiple
#                              of one full cycle.
#  While auto-tuning is on, "scan_newtag" in config.json is ignored, as
#  requested - the quiet-stop timeout is derived instead.
# =====================================================
def compute_auto_timing(scan_timeout_s, antenna_count, passes_target=3, quiet_cycles=1):
    """
    Returns a dict with the auto-computed dwell_ms and quiet_stop_s for
    this scan cycle, given the current scan_timeout and antenna count.
    """
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
    """Builds the 0x95 SET ANTENNA DWELL command for an auto-tuning result dict."""
    return build_dwell_command(timing["dwell_ms"])


# =====================================================
#  RF MODE - config-driven ("rf_mode" in config.json)
# =====================================================
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
    """Raised when config.json has an rf_mode value that is not in RF_MODE_TABLE."""
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


# =====================================================
#  SESSION / TARGET / Q - Set Protocol Configuration (0x9B)
# =====================================================
CMD_SET_SESSION = bytes.fromhex("FF039B050001DCE9")   # Session 1

CMD_SET_TARGET_DYNAMIC_AB = bytes.fromhex("FF049B05010000A3FD")  # Option=00 dynamic, Value=00 -> A-B (in use)
CMD_SET_TARGET_DYNAMIC_BA = bytes.fromhex("FF049B05010001A3FC")
CMD_SET_TARGET_STATIC_A   = bytes.fromhex("FF049B05010100A2FD")
CMD_SET_TARGET_STATIC_B   = bytes.fromhex("FF049B05010101A2FC")

CMD_SET_Q_DYNAMIC  = bytes.fromhex("FF039B051200CEE8")      # Option=00, dynamic Q - module's own default
CMD_SET_Q_STATIC_8 = bytes.fromhex("FF049B0512010880A7")    # Q=8  ~256 tags
CMD_SET_Q_STATIC_9 = bytes.fromhex("FF049B0512010980A6")    # Q=9  ~512 tags
CMD_SET_Q_STATIC   = bytes.fromhex("FF049B0512010A80A5")    # Q=10 ~1024 tags


# =====================================================
#  Q - GENERAL BUILDER, config-driven ("q_mode"/"q_value" in config.json)
#  - NEW in 1.0.0(4)
#
#  The 4 fixed CMD_SET_Q_* constants above only cover Q=dynamic/8/9/10.
#  Decoding their Data field (0x9B command) shows the real structure:
#      05 (fixed sub-selector byte) + 12 (Parameter ID = Q) + Option(1) [+ Value(1), only if Option=static]
#  Option=0x00 -> dynamic Q (Data length 3, no Value byte)
#  Option=0x01 -> static Q  (Data length 4, Value = Q, 0-15 per Gen2 spec)
#  Verified byte-for-byte: build_q_command(False) and build_q_command(True, 8/9/10)
#  reproduce CMD_SET_Q_DYNAMIC / CMD_SET_Q_STATIC_8/9/10 above exactly.
#  This lets ANY Q value 0-15 be selected from config.json, not just 8/9/10.
# =====================================================
class InvalidQError(Exception):
    """Raised when config.json has an invalid q_mode/q_value combination."""
    pass


Q_MIN = 0
Q_MAX = 15  # Gen2 Q is a 4-bit slot-count parameter (2^Q slots)


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
    """
    Reads config["q_mode"] ("dynamic" [default] or "static") and, if
    static, config["q_value"] (0-15). Falls back to dynamic Q (module's
    own default/original 1.0.0(3) behavior) on any invalid/missing value,
    logging a warning rather than blocking the scan - Q is a tuning knob,
    not a correctness requirement like antenna/power/rf_mode.

    Only meaningful on the AA48 path - run_async_scan() skips sending any
    Q command at all when inventory_mode="AA58" (the module doesn't
    accept it there).
    """
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


# =====================================================
#  RF-LEVEL SELECT FILTER for CMD_START_ASYNC_FULL (0xAA48) - NEW in 1.0.0(4)
#
#  Doc sec 5.1.1 "Option" + sec 5.1.2 "Tag Singulation": when the AA48
#  command's Option byte has Select-Option Bits = 0x04 ("filter and select
#  the contents of the EPC bank"), the following extra fields become
#  present in the AA48 Data field, IN THIS ORDER:
#      Access Password (4 bytes)   - 0x00000000 for unlocked tags
#      Select Address   (4 bytes)  - bit offset within the bank to compare
#      Select Data Length (1 byte) - length of the compare data, in bits
#      Select Data       (N bytes) - the bytes to match against
#  Per the doc's own worked example (sec 5.1.2): "EPCID address is start
#  from 0x20 bits" - i.e. the actual EPC ID data in the EPC bank begins at
#  bit offset 0x20 (32 decimal); bits 0-31 are the bank's stored CRC/PC
#  words, not part of the EPC itself. So to match the first N bytes of a
#  tag's EPC, Select Address is always 0x00000020 and Select Data Length =
#  N*8.
#  This is a genuine RF-level filter (unlike config.json's software-only
#  "rfid_filter"): the module itself only inventories tags whose EPC bank
#  matches, so non-matching tags never enter the Gen2 slot/collision
#  process at all.
# =====================================================
SELECT_EPC_BANK_ADDRESS = 0x00000020  # doc sec 5.1.2: EPC data starts at bit 0x20


def build_select_filter_subdata(metadata_flags, search_flags, prefix_bytes):
    """
    Builds the AA48 Subcommand Data (the part between Subcommand Code and
    SubCRC) with an EPC-bank Select filter enabled for `prefix_bytes`.
    """
    if not (1 <= len(prefix_bytes) <= 31):
        raise ValueError("Select filter prefix must be 1-31 bytes (8-248 bits)")

    option = 0x04  # Select-Option Bits = 0x04: filter & select EPC bank contents
    access_password = bytes(4)  # 0x00000000 - no password, standard for unlocked tags
    select_address = SELECT_EPC_BANK_ADDRESS.to_bytes(4, "big")
    select_bitlen = len(prefix_bytes) * 8
    select_data_length = bytes([select_bitlen])  # BIT5 of Option not set -> 1-byte length field

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
    """AA48 Subcommand Data with Select disabled (Option=0x00) - the original 1.0.0(3) behavior."""
    return (
        metadata_flags.to_bytes(2, "big")
        + bytes([0x00])
        + search_flags.to_bytes(2, "big")
    )


def get_start_async_full_command(config):
    """
    Builds CMD_START_ASYNC_FULL (0xAA48). If config.json's "rfid_filter"
    list contains EXACTLY ONE hex prefix, that prefix is embedded as a
    genuine RF-level Select filter (see block above) so the module itself
    only interrogates tags matching that prefix - other tags never
    participate in the inventory round, so they can no longer cause
    collisions for the tags actually wanted.

    If "rfid_filter" has zero or more than one entry, RF-level Select is
    NOT used (this module's single-rule AA48 Select can only match one
    prefix at a time; multiple prefixes would need the separate 0xAA4C
    multi-label filter, which is a different command and not implemented
    here) - falls back to the unfiltered command (software-only
    rfid_filter post-filtering still applies exactly as in 1.0.0(3)).
    """
    METADATA_FLAGS = 0x003F   # unchanged from 1.0.0(3): read count, RSSI, ant, etc.
    SEARCH_FLAGS   = 0x8003   # unchanged from 1.0.0(3): heartbeat on, no duty cycle

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


# =====================================================
#  EX ASYNCHRONOUS INVENTORY - 0xAA58 / stop 0xAA59 (doc sec 5.6.4) - NEW
#  in 1.0.0(4). This is a DIFFERENT command from AA48/AA49, not a mode
#  flag on it - it is the module's own alternative inventory algorithm,
#  purpose-built by the vendor for "a large number of tags environment"
#  and "complex/difficult to read" scenes (doc's own examples: a handheld
#  device counting a large number of tags, a smart cabinet/filing cabinet,
#  or tags that are stacked and hard to read - i.e. exactly the kind of
#  dense, mutually-shadowing tag population this reader's config.json
#  "antenna"/"power" comments describe).
#
#  Per doc sec 5.6.4 the Send-command SubData layout (after Marker+AA58)
#  is, IN THIS ORDER:
#      ExConfigData   (20 bytes) - only byte[0] is meaningful, rest MUST
#                                    be 0x00. byte[0]=0x00 -> dense-tag
#                                    mode (read more/read all, large
#                                    numbers of tags or complex
#                                    environments). byte[0]=0x01 -> sparse
#                                    mode (few/easy tags, faster).
#      Metadata Flags (2 bytes)  - same meaning as AA48/0x22.
#      Option         (1 byte)  - same meaning as 0x22's Option byte, but
#                                    "not support Match Filter" (i.e. the
#                                    AA48-style Select/rfid_filter RF-level
#                                    filter used above CANNOT be combined
#                                    with AA58 - the doc explicitly says
#                                    "Filtering is not supported").
#      Search Flags   (2 bytes) - high byte same meaning as AA48 (heartbeat
#                                    etc.) except BIT6 (single-antenna, no
#                                    auto-stop-on-new-tag) is not supported;
#                                    low byte same as 0x22, no embedded
#                                    data / TagFocus support.
#  SubCRC formula verified against the doc's own worked example (sec
#  5.6.4): ExConfigData=20x00, MetadataFlags=0x003F, Option=0x00,
#  SearchFlags=0x0000 -> SubCRC=0x41. get_subcrc(AA58-subcmd + that
#  subdata) reproduces 0x41 exactly, confirming the field order/formula
#  above.
#
#  Doc's explicit constraints (sec 5.6.4 notes), all honored below:
#    - "does not require/cannot specify Session, Target, Q, RF_mode. The
#      module handles it internally, and the configuration does not
#      work." -> these 0x9B/RF-mode sends are SKIPPED when AA58 is
#      selected (see run_async_scan()).
#    - "Filtering is not supported, and additional data is not
#      supported." -> no Select filter is ever embedded in AA58, unlike
#      AA48 above; config.json's software-only "rfid_filter" still runs
#      as a post-filter exactly as before, since that logic lives in
#      is_allowed_epc() and doesn't care which inventory command produced
#      the tag.
#    - "For inventory polling rules, refer to ... Asynchronous Inventory
#      (0xAA48)" -> antenna enable/power/dwell are still sent as normal;
#      only Session/Target/Q/RF_mode are AA48-specific and skipped.
#    - Region-limited: "only supported in China, CE, INDIA, RUSSIA,
#      PHILIPPINES, JAPAN (all), and ISRAEL." This script does not
#      currently select certification region from config.json (CMD_SET_
#      REGION is a fixed constant), so this cannot be hard-enforced here;
#      instead log_module_version()'s region readback is checked at
#      startup and a warning is logged if AA58 is selected on hardware
#      reporting an unsupported region (see AA58_SUPPORTED_REGIONS below
#      and the check in main()).
# =====================================================
AA58_SUPPORTED_REGIONS = {
    "CHINA", "CE_LOW", "CE_HIGH", "CE_LOW_AND_HIGH",
    "INDIA", "RUSSIA", "PHILIPPINES", "ISRAEL",
    "JAPAN", "JAPAN2", "JAPAN3",   # doc: "JAPAN (all)"
}


def build_ex_dense_subdata(dense_mode: bool, metadata_flags: int, search_flags: int, option: int = 0x00) -> bytes:
    """
    Builds the AA58 Subcommand Data: ExConfigData(20) + MetadataFlags(2) +
    Option(1) + SearchFlags(2). dense_mode=True -> ExConfigData[0]=0x00
    (dense-tag mode); dense_mode=False -> ExConfigData[0]=0x01 (sparse/
    few-tag fast mode). Option is left at 0x00 (Match Filter is not
    supported by this command per the doc, so it is never anything else).
    """
    exconfig = bytearray(20)
    exconfig[0] = 0x00 if dense_mode else 0x01
    return (
        bytes(exconfig)
        + metadata_flags.to_bytes(2, "big")
        + bytes([option])
        + search_flags.to_bytes(2, "big")
    )


def get_start_async_ex_command(config):
    """
    Builds CMD_START_ASYNC_EX (0xAA58). config.json's "dense_mode" key
    (bool, default True) selects ExConfigData byte[0]: True -> dense-tag
    mode (module's own recommendation for large/complex tag populations,
    i.e. the exact symptom this reader keeps seeing - tags flickering in
    and out between scans because the population is too dense for a
    single AA48 round to settle on). False -> sparse/fast mode, only
    appropriate when very few tags are expected.
    """
    METADATA_FLAGS = 0x003F   # same fields as AA48: read count, RSSI, ant, etc.
    SEARCH_FLAGS   = 0x8003   # same heartbeat/status bits as AA48 - BIT6 (the
                               # one AA58 doesn't support) is not part of this value.

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
    """Builds the 0xAA59 stop command for the EX (AA58) inventory - empty SubData, same as AA49 is for AA48."""
    return build_ext_command(bytes.fromhex("AA59"), b"")


def warn_if_region_unsupported_for_ex(region_name):
    """Logs a warning (does not block the scan) if AA58 is selected but the module's reported certification region doesn't support it."""
    if region_name and region_name.upper() not in AA58_SUPPORTED_REGIONS:
        log.warning(
            "[CONFIG] inventory_mode=AA58 selected, but module reports "
            "certification region=%s, which the protocol doc does NOT list "
            "as supporting 0xAA58 (supported: %s). The module may reject "
            "AA58 or fall back to unexpected behavior - this is "
            "informational only, the scan is not blocked.",
            region_name, ", ".join(sorted(AA58_SUPPORTED_REGIONS))
        )


# =====================================================
#  RSSI FILTER - native module command, 0xAA5B (doc sec 10.8) - NEW in
#  1.0.0(6), replacing the 1.0.0(5) software-only RSSI_THRESHOLD drop in
#  parse_fast_mode_correct().
#
#  Doc sec 10.8: "This command is used to configure the RSSI filtering
#  function, the purpose of which is to ignore some tag data with too
#  weak signal strength. In the firmware, when the signal strength of
#  the read tag is weaker than a certain value, the tag data is not
#  uploaded (ignored)." Requires firmware 202404024 or later (doc's own
#  note) - if the module is on older firmware it will simply not
#  recognize/act on this command; that isn't hard-enforced here (no
#  in-band way to block sending it), but log_module_version()'s firmware
#  date is checked and a warning logged if a threshold is configured on
#  older firmware - see warn_if_firmware_predates_rssi_filter() below.
#
#  SubData format (Send command), verified byte-for-byte against ALL 4
#  of the doc's own worked examples in sec 10.8:
#      Get current status/value:       1 byte:  00
#      Cancel RSSI filtering:          4 bytes: 01 00 00 00
#      Enable, threshold -50dBm:       4 bytes: 01 AA CE 00
#      Enable, threshold -40dBm:       4 bytes: 01 AA D8 00
#  From the two "enable" examples: byte[2] is the threshold in dBm
#  encoded as a single two's-complement byte (-50 & 0xFF = 0xCE, -40 &
#  0xFF = 0xD8 - both reproduce exactly), byte[1] is a fixed 0xAA,
#  byte[3] is a fixed 0x00. build_rssi_filter_subdata() below reproduces
#  all 4 examples exactly (get -> SubCRC 05/CRC 0B49, cancel -> SubCRC
#  06/CRC 1DAB, enable -40 -> SubCRC 88/CRC 7578).
# =====================================================
RSSI_FILTER_MIN_DBM = -128  # single signed byte, doc examples only show -40/-50
RSSI_FILTER_MAX_DBM = -1    # 0 or positive isn't a meaningful "ignore weak reads" threshold

# Doc: "Need to update the firmware 202404024 or later" for 0xAA5B to be
# recognized. Compared against query_module_version()'s firmware_date_raw
# (YY MM DD rev, BCD-ish per that function) - informational only, since
# there's no in-band way to confirm the module actually understood the
# command.
RSSI_FILTER_MIN_FIRMWARE_DATE_RAW = "20240424"  # YYYYMMDD from doc's "202404024" (10-digit typo in doc; treated as 2024-04-24)


def build_rssi_filter_subdata(enabled: bool, threshold_dbm: int = None) -> bytes:
    """
    Builds the 0xAA5B Subcommand Data. enabled=False -> cancel filtering
    (the 4-byte 01 00 00 00 pattern from the doc's own "cancel" example).
    enabled=True -> enable at threshold_dbm dBm (01 AA <byte> 00 pattern,
    verified against both the -50 and -40 worked examples).
    """
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
    """SubData for the 'Get current status and setting value' form (doc: 1 byte, 00)."""
    return bytes([0x00])


# =====================================================
#  RSSI FILTER COMMAND (0xAA5B) - module-level, normal threshold only.
#
#  NOTE: the RSSI-based ghost-reject step is NOT done here anymore. It
#  used to be attempted at the module level (0xAA5B), adaptively
#  switching to a stricter threshold - but that command is sent BEFORE
#  this cycle's tags are known, so it could only ever react to the
#  PREVIOUS cycle's tag count, one full cycle late. That's been replaced
#  by a same-cycle SOFTWARE post-filter (see "COUNT-TRIGGERED RSSI
#  GHOST-REJECT" further below, applied in run_async_scan() right before
#  publish) which checks THIS cycle's own count before deciding whether
#  to drop weak-RSSI tags - no lag, no guessing based on history. This
#  function now just sends the plain config["rssi_threshold"] every
#  cycle, unchanged from before the adaptive experiment.
# =====================================================
def get_rssi_filter_command(config):
    """
    Reads config["rssi_threshold"] (int dBm, e.g. -69, or None/absent =
    disabled) and builds the matching 0xAA5B command - enable at that
    threshold, or cancel filtering if the key is None/absent. This is
    sent once per scan cycle in run_async_scan(), same as SET REGION/
    GEN2/POWER/ANT - the module-side equivalent of what 1.0.0(5)'s
    RSSI_THRESHOLD used to do purely in software.
    """
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
    """
    Informational only (doc sec 10.8: '0xAA5B ... Need to update the
    firmware 202404024 or later'). If rssi_threshold is configured but
    the module's firmware date looks older than that, 0xAA5B may be
    silently unrecognized - logs a warning so this isn't a silent
    no-op, but does not block the scan (there's no in-band way to
    confirm the module actually understood the command either way).

    firmware_date_raw comes from query_module_version()'s info dict -
    4 raw bytes as an 8-char hex string, byte[0] is always 0x20 and
    byte[1:4] are the YY/MM/DD digits (see _decode via "20%02X.%02X.%02X"
    in log_module_version()), so the raw hex string already reads
    directly as a YYYYMMDD-shaped value for a simple string comparison.
    """
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
        pass  # best-effort only, never blocks the scan on a parsing issue


# =====================================================
#  CONFIG
# =====================================================
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


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

# Static/manual dwell command + value, kept for check_version_only() display
# and as the fallback used when "auto_tune_timing": false. The REAL scan
# path (run_async_scan) builds a fresh dwell command every cycle instead -
# see the AUTO-TUNING section above and the top of run_async_scan().
CMD_SET_ANTENNA_DWELL, ANTENNA_DWELL_MS = get_dwell_command(cfg)

# NEW in 1.0.0(4): CMD_START_ASYNC_FULL is now built here, from config, so
# it can embed the RF-level Select filter described above. Rebuilt fresh
# each time reader.py is (re)started, same as CMD_SET_RFMODE/ENABLE_ANT/
# SET_POWER_ALL above.
CMD_START_ASYNC_FULL = get_start_async_full_command(cfg)

# NEW in 1.0.0(4): alternative EX dense-mode inventory path (0xAA58/0xAA59).
# Built unconditionally (cheap - a handful of bytes) so it's ready whichever
# way "inventory_mode" is set; which one actually gets sent is decided per
# scan cycle in run_async_scan() from a freshly-reloaded config, same as
# every other per-cycle setting (scan_timeout, dwell, etc.).
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


# =====================================================
#  EPC PREFIX FILTER - loaded from config.json
# =====================================================
_raw_prefixes        = cfg.get("rfid_filter", ["86"])
EPC_ALLOWED_PREFIXES = tuple(p.upper() for p in _raw_prefixes)
log.info("[READER] EPC prefix filter: %s", EPC_ALLOWED_PREFIXES)


def is_allowed_epc(epc: str) -> bool:
    return any(epc.startswith(p) for p in EPC_ALLOWED_PREFIXES)


# =====================================================
#  ANTENNA ALLOW-LIST - software-level guarantee
# =====================================================
try:
    ALLOWED_ANTENNAS = frozenset(int(str(a).strip()) for a in cfg.get("antenna", []))
except (ValueError, TypeError):
    ALLOWED_ANTENNAS = frozenset()
log.info("[READER] Antenna allow-list (tags outside this are dropped): %s", sorted(ALLOWED_ANTENNAS))


def is_allowed_antenna(ant) -> bool:
    if ant is None:
        return False
    return ant in ALLOWED_ANTENNAS


# =====================================================
#  MODULE VERSION - Get Version (0x03)
# =====================================================
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


# =====================================================
#  SERIAL HELPERS
# =====================================================
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


# =====================================================
#  SERIAL BOOT - baud rate detection & negotiation
# =====================================================
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


# =====================================================
#  EPC VALIDATION
# =====================================================
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


# =====================================================
#  EPC FRAME PARSER
# =====================================================
HEARTBEAT_MARKER = b"XTSJ"  # doc sec 5.6.2.2 - fixed ASCII marker, Data starts at idx+5


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
                # NOTE (1.0.0(6)): no software RSSI drop here anymore - RSSI
                # filtering is now done by the module itself via the 0xAA5B
                # command (see get_rssi_filter_command()/RSSI FILTER section
                # above and run_async_scan() below). If rssi_threshold is
                # configured, the module simply never uploads a sub-threshold
                # read in the first place, so there is nothing left to filter
                # here. `rssi` is still parsed above and reported in the
                # per-tag log line for visibility, just no longer used to
                # accept/reject a read in software.
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


# =====================================================
#  MODULE POWER-CYCLE HELPERS
# =====================================================
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
#  ASYNC SCAN
# =====================================================
def run_async_scan(baud, stop_event=None):
    cfg = load_config()

    MAX_SCAN_TIME    = float(cfg.get("scan_timeout", 20))
    STABLE_THRESHOLD = int(cfg.get("stable_stop_threshold", 0))
    AUTO_TUNE        = bool(cfg.get("auto_tune_timing", True))

    # CHANGED in 1.0.0(6): RSSI filtering is now built as a module command
    # (0xAA5B) rather than a software global re-read here - see the RSSI
    # FILTER section above. Rebuilt fresh every cycle (same pattern as
    # every other per-cycle setting in this file) so changing
    # config.json's "rssi_threshold" takes effect on the very next scan.
    rssi_filter_cmd, rssi_threshold_used = get_rssi_filter_command(cfg)

    # NEW: same-cycle RSSI ghost-reject config, read fresh each cycle -
    # see the "COUNT-TRIGGERED RSSI GHOST-REJECT" block further below
    # (applied right before publish) for how these are used. Unlike the
    # module-level rssi_threshold above, this is a SOFTWARE post-filter
    # evaluated against THIS cycle's own tag count, no one-cycle lag.
    RSSI_STRICT_COUNT_BOUNDARY = int(cfg.get("rssi_threshold_strict_count", RSSI_THRESHOLD_STRICT_COUNT_DEFAULT))
    RSSI_STRICT_DBM            = int(cfg.get("rssi_threshold_strict", RSSI_THRESHOLD_STRICT_DEFAULT))

    MIN_READS_PER_SCAN = max(1, int(cfg.get("min_reads_per_scan", MIN_READS_PER_SCAN_DEFAULT)))

    # NEW in 1.0.0(7): native Read Count gate - see the constant comment
    # above for what this measures vs MIN_READS_PER_SCAN.
    MIN_NATIVE_READ_COUNT = max(1, int(cfg.get("min_native_read_count", MIN_NATIVE_READ_COUNT_DEFAULT)))

    # NEW in 1.0.0(4): re-read inventory_mode fresh each cycle (same pattern
    # as every other per-cycle setting above) so switching AA48 <-> AA58 in
    # config.json takes effect on the very next scan, no restart needed.
    scan_inventory_mode = str(cfg.get("inventory_mode", "AA48")).strip().upper()
    if scan_inventory_mode not in ("AA48", "AA58"):
        scan_inventory_mode = "AA48"
    use_ex_inventory = (scan_inventory_mode == "AA58")

    if use_ex_inventory:
        start_cmd = get_start_async_ex_command(cfg)   # rebuilt so "dense_mode" toggles live too
        stop_cmd  = CMD_STOP_ASYNC_EX
        q_cmd, q_mode_used, q_value_used = None, None, None  # AA58 doesn't accept Q at all
    else:
        start_cmd = CMD_START_ASYNC_FULL
        stop_cmd  = CMD_STOP_ASYNC
        # NEW in 1.0.0(4): re-read q_mode/q_value fresh each cycle, same
        # pattern as inventory_mode above - only sent on the AA48 path.
        q_cmd, q_mode_used, q_value_used = get_q_command(cfg)

    try:
        antenna_count_now = len(parse_antenna_list(cfg))
    except InvalidAntennaError:
        antenna_count_now = len(ALLOWED_ANTENNAS) or 1
        log.warning(
            "[CONFIG] Could not re-parse 'antenna' list this cycle - "
            "falling back to the antenna count used at startup (%d) for "
            "auto-tuning.", antenna_count_now
        )

    if AUTO_TUNE:
        # Default lives in code (AUTO_TUNE_PASSES_DEFAULT /
        # AUTO_TUNE_QUIET_CYCLES_DEFAULT above), still overridable via
        # config.json if the key is present.
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
    epc_native_max_count = {}  # NEW in 1.0.0(7): highest native Read Count per EPC this scan
    epc_best_rssi = {}         # NEW: strongest (max/least-negative) RSSI ever seen per EPC this scan
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

            # NEW in 1.0.0(6): module-level RSSI filter (0xAA5B, doc sec
            # 10.8) - replaces 1.0.0(5)'s software-only RSSI_THRESHOLD
            # drop. Not fatal if unconfirmed (older firmware may not
            # recognize 0xAA5B - see warn_if_firmware_predates_rssi_filter()
            # in main()) - the scan still proceeds either way.
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
                # doc sec 5.6.4: AA58 "does not require/cannot specify Session,
                # Target, Q, RF_mode. The module handles it internally, and the
                # configuration does not work." -> skip sending them; the
                # module ignores/ handles this itself for this command.
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

            # AA48 (default): CMD_START_ASYNC_FULL embeds the RF-level Select
            # filter when config.json's rfid_filter has exactly one prefix -
            # see get_start_async_full_command() above.
            # AA58 (inventory_mode="AA58"): CMD_START_ASYNC_EX, the vendor's
            # purpose-built "large number of tags / complex environment"
            # inventory algorithm - see the EX ASYNCHRONOUS INVENTORY block
            # above for what it does differently and why it's worth trying
            # against the intermittent add/remove flicker seen in dense
            # multi-hundred-tag scans.
            send_command(ser, start_cmd)
            time.sleep(0.3)

            # FIX (identified from log2.txt): start_time was previously set
            # BEFORE all the setup commands above (SET REGION/GEN2/POWER/
            # ANT/DWELL/RFMODE/SESSION/TARGET/Q/START_ASYNC), and those add
            # up to real, measured serial delay - about 2.5s on this AA48
            # path (0.15+0.15+0.15+0.3+0.15+0.5+0.3+0.15+0.15+0.2+0.3), or
            # about 1.4s on the AA58 path (RFMODE/SESSION/TARGET/Q skipped).
            # Since the scan-time cutoff below is `elapsed >= MAX_SCAN_TIME`,
            # a configured "scan_timeout": 12 was only leaving ~9.5s (AA48)
            # or ~10.6s (AA58) of the module actually listening for tags -
            # matching what log2.txt shows (first tag logged at [3.0s], not
            # [0.x s], right after the "[SCAN] Started" line).
            # Resetting the clock HERE, right after START_ASYNC/START_ASYNC_EX
            # has actually been sent, makes scan_timeout mean what it says:
            # N seconds of real inventory listening time, regardless of which
            # inventory command or setup path was used.
            setup_elapsed = time.time() - start_time
            start_time        = time.time()
            last_new_tag_time = start_time

            log.info(
                "[SCAN] Setup took %.2fs (region/gen2/power/antenna/dwell%s/"
                "start-async) - scan clock reset here, scan_timeout=%.0fs now "
                "means %.0fs of actual listening time.",
                setup_elapsed, "" if use_ex_inventory else "/rfmode/session/target/q",
                MAX_SCAN_TIME, MAX_SCAN_TIME
            )

            log.info(
                "[SCAN] Started (%s) - max=%.0fs quiet=%.1fs dwell_ms=%d antenna_gate_open=%s",
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
                    log.info("[SCAN] Max scan time %.0fs reached - stopping", MAX_SCAN_TIME)
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

                    # NEW in 1.0.0(7): track the highest native "Read
                    # Count" (module's own per-round detection count,
                    # doc sec 5.1.3 BIT0) ever reported for this EPC
                    # during the scan - a separate, finer-grained signal
                    # from epc_seen's cross-report count below.
                    native_rc = epc_native_max_count.get(epc, 0)
                    if tag["COUNT"] > native_rc:
                        epc_native_max_count[epc] = tag["COUNT"]

                    # NEW: track the STRONGEST (max/least-negative) RSSI
                    # ever seen for this EPC during the scan - used by the
                    # same-cycle "COUNT-TRIGGERED RSSI GHOST-REJECT" step
                    # right before publish (see below). Using the best
                    # single reading (not the last, not the average) gives
                    # a real tag every possible chance to clear the strict
                    # threshold even if most of its reads were weak.
                    if tag["RSSI"] is not None:
                        best_rssi = epc_best_rssi.get(epc)
                        if best_rssi is None or tag["RSSI"] > best_rssi:
                            epc_best_rssi[epc] = tag["RSSI"]

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
                # stop_cmd matches whichever inventory command was actually
                # started above (AA59 for AA58/EX, AA49 for AA48) - sending
                # the wrong stop command is harmless (module just won't be
                # "in" that inventory state) but sending the matching one is
                # what the doc specifies for a clean/normal end.
                send_command(ser, stop_cmd)
        except Exception:
            pass

        GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
        log.info("[SCAN] Async scan stopped")

    duration = int(time.time() - start_time)
    unique   = len(epc_seen)
    log.info(
        "[SCAN] Done unique=%d duration=%ds aborted=%s antenna_gate_open=%s",
        unique, duration, aborted, antenna_gate_open
    )

    if aborted:
        return {
            "epc_seen": {}, "duration": duration, "aborted": True,
            "antenna_gate_open": antenna_gate_open,
        }

    # =====================================================
    #  GHOST-TAG DEFENSE: min_reads_per_scan - NEW in 1.0.0(6)
    #
    #  epc_seen's VALUES already are the real per-tag read counts for this
    #  scan (incremented every time that EPC was seen again in the loop
    #  above) - they just used to get discarded (reset to 1) when building
    #  the return dict. A genuinely-present tag in a dense cabinet
    #  typically gets read many times over a 10-12s scan (dup ratios of
    #  3-5x are normal, per log2.txt). A "ghost" tag - RF bleed from a
    #  neighbouring cabinet/compartment, a stray tag near the antenna, an
    #  occasional corrupted-but-CRC-valid read - is far more likely to be
    #  seen only once or twice in the whole window. Requiring
    #  min_reads_per_scan reads before a tag is trusted filters those out
    #  without needing multiple scan CYCLES (unlike remove_confirm_cycles,
    #  which only helps once a tag is already missing - this catches a
    #  ghost that's misread the SAME cycle it appears).
    #  Default min_reads_per_scan=1 (config key absent) = old behavior,
    #  completely unchanged - every tag seen even once is trusted.
    #
    #  GHOST-TAG DEFENSE: min_native_read_count - NEW in 1.0.0(7)
    #
    #  Independent second gate using EX10's own per-report "Read Count"
    #  metadata (doc sec 5.1.3 BIT0) - see MIN_NATIVE_READ_COUNT_DEFAULT's
    #  comment above for what this measures and why it's a different,
    #  finer-grained signal than min_reads_per_scan. A tag must clear
    #  BOTH gates to be trusted: cross-report count (min_reads_per_scan)
    #  AND its single best native in-round Read Count
    #  (min_native_read_count). Default min_native_read_count=1 (config
    #  key absent) = old behavior, completely unchanged.
    # =====================================================
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

    # =====================================================
    #  GHOST-TAG DEFENSE: COUNT-TRIGGERED RSSI GHOST-REJECT - NEW
    #
    #  Runs LAST, after min_reads_per_scan/min_native_read_count have
    #  already been applied above, and evaluated against THIS cycle's
    #  own result - no waiting for a future cycle, no guessing from
    #  history. If the cabinet is (per this cycle's own count) nearly
    #  empty - i.e. len(published_epc_seen) < RSSI_STRICT_COUNT_BOUNDARY
    #  (default 10) - any tag whose STRONGEST reading this scan
    #  (epc_best_rssi, tracked in the read loop above) never reached
    #  RSSI_STRICT_DBM (default -58) is dropped before publish. Ghost
    #  tags (RF bleed from a neighbouring compartment, stray
    #  reflections, etc.) tend to surface as weak reads specifically
    #  once the real tag population thins out - a full cabinet's own RF
    #  environment normally masks them, but an empty/near-empty cabinet
    #  lets them through at the normal (looser) rssi_threshold used at
    #  the module level. This step only ever REMOVES tags that already
    #  survived every earlier gate - it never adds anything back.
    #  A count >= RSSI_STRICT_COUNT_BOUNDARY means the cabinet still has
    #  a real, sizeable tag population, so this step is skipped entirely
    #  and published_epc_seen passes through unchanged.
    # =====================================================
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

    return {
        "epc_seen": published_epc_seen, "duration": duration, "aborted": False,
        "antenna_gate_open": antenna_gate_open,
    }


# =====================================================
#  ZERO-TAG DOUBLE-CHECK
# =====================================================
def scan_with_zero_confirmation(baud, stop_event=None):
    cfg_now           = load_config()
    # CHANGED in 1.0.0(8): default recheck_attempts=0 (was 1). A recheck
    # calls run_async_scan() AGAIN IN FULL - it is not a short extra wait,
    # it is a second complete scan_timeout-length listening window (plus
    # power-cycle/baud-reconnect overhead on top). With the old default
    # of 1, ANY cycle that legitimately or falsely reads zero tags
    # silently doubled the wall-clock time users see, from
    # scan_timeout=12s to ~24s+ - a hard SLA violation from the caller's
    # point of view, since config.json's scan_timeout is documented/
    # expected as a cutoff, not a per-attempt duration. This is now
    # OFF by default - scan_timeout is a hard ceiling again, exactly
    # once, no silent doubling. Set "zero_recheck_attempts": 1 (or more)
    # in config.json ONLY if you specifically want the old
    # false-negative protection back and are OK with the corresponding
    # multiplied worst-case duration (attempts * (scan_timeout + setup
    # + power-cycle overhead)).
    recheck_attempts  = int(cfg_now.get("zero_recheck_attempts", 0))
    recheck_delay     = float(cfg_now.get("zero_recheck_delay", 1.5))

    result   = run_async_scan(baud, stop_event=stop_event)
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

        result    = run_async_scan(recheck_baud, stop_event=stop_event)
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


# =====================================================
#  MAIN
# =====================================================
def main(stop_event=None):
    acquire_reader_mutex()
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

        result = scan_with_zero_confirmation(baud, stop_event)

        if not result["publish_safe"]:
            log.warning(
                "[READER] Result NOT safe to publish (unconfirmed zero or "
                "aborted scan) - discarding this cycle. attempts=%d",
                result.get("scan_attempts", 1)
            )
        else:
            # CHANGED in 1.0.0(6): no remove-debounce step anymore - result
            # is published exactly as scan_with_zero_confirmation() returned
            # it. See the 1.0.0(6) changelog at the top of this file for why
            # that feature was removed.
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
