================================================================================
VERSION - 1.0.0.(9)
================================================================================
Date 27 August 2026
-Module 1.0.0(8)
 Change Select Cabinet with A(101)-O(116) List
 Add Shutdown command

-LightLockControl 1.0.0(9)
#   lightLockControl.py runs as its own service, separate from
#   run.py/mqtts.py. To keep the light status readable by the other
#   process WITHOUT creating a new file (status.json is already sent
#   to the web dashboard - we don't want two status sources that need
#   to be manually kept in sync), this module reads/writes the
#   "Light" field directly in the EXISTING status.json - the same
#   field status.py has always used. Other fields in status.json
#   (Door1, Lock1, sessionId, etc.) are left untouched - this is a
#   read-modify-write, only the "Light" key is changed.

================================================================================
VERSION - 1.0.0.(7)
================================================================================


Date 14 August 2026
- Reader 1.0.0(7)

#        1) NEW: check_antenna_connections(), sent once at startup right
#           after Get Version, before the scan begins. Uses 0xAA48/0x91's
#           sibling read-only command 0x61 with Option=0x05 (doc sec
#           8.1) to ask the module which physical antenna ports have a
#           closed-loop (i.e. cable/antenna actually plugged in) right
#           now. Purely informational, does not block the scan - logs
#           connected/disconnected port lists, and specifically WARNS if
#           any antenna ID in config.json's "antenna" list (i.e. one
#           you're actually asking the module to inventory on) comes
#           back disconnected, since that antenna will silently
#           contribute zero tags every cycle otherwise with no other
#           indication why.
#           This is a different failure mode from the antenna_ports
#           over-limit check already in log_module_version() (1.0.0(?)):
#           that one catches "you configured antenna 5 but this module
#           variant only has 4 ports at all" (answered from Get Version
#           data, no new command); this one catches "port 5 exists on
#           this module, but nothing is physically plugged into it".
#           Byte format verified against the doc's own worked example
#           (sec 8.1 "Example 5") - CRC-checked (send: FF 01 61 05 BD B8;
#           receive CRC 9CE2 reproduced exactly). Parsing does not trust
#           the example's printed DataLength byte, which doesn't
#           arithmetically match the rest of that worked example (same
#           kind of PDF-extraction artifact seen elsewhere in this doc)
#           - it walks (ant_id, connected) pairs directly up to the
#           trailing CRC instead.


================================================================================
VERSION - 1.0.0.(6)
================================================================================


Date 13 August 2026
- Reader 1.0.0(6)


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

================================================================================
VERSION - 1.0.0.(5)
================================================================================


Date 08 August 2026
- Reader 1.0.0(5)
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

- Starstop.py 1.0.0(2)

#        - scan_worker() now calls reader.apply_remove_debounce() on the
#          raw epc_seen result before saving to rfid2.json, using
#          config.json's "remove_confirm_cycles". Previously this only
#          happened inside reader.main() (via scan_with_zero_confirmation()),
#          which none of the production trigger paths (MQTT Startscan via
#          mqtts.py, door-close via radarScanControl.py, periodic startup
#          scan via run.py) actually call - they all go through
#          startstops.main() -> start_scan() -> scan_worker(), which called
#          reader.run_async_scan() directly and skipped debounce entirely.
#          This is why tag_debounce_state.json was never being created even
#          with "remove_confirm_cycles": 2 set in config.json.
#        - This is the SINGLE choke point all three trigger paths share, so
#          patching it here covers MQTT / door-sensor / periodic scans alike
#          without touching mqtts.py, radarScanControl.py, or run.py.
#        - zero-tag double-check (scan_with_zero_confirmation) is
#          intentionally NOT introduced here - only the debounce step was
#          missing/requested; adding zero-recheck here would change scan
#          timing/behavior beyond the scope of this fix.

- Module 1.0.0(5):
  Set Inventory Mode AA48 or AA58

================================================================================
VERSION - 1.0.0.(4)
================================================================================
Date 07 August 2026
- Reader 1.0.0(4)

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
#        4) FIX: scan_timeout was measured from BEFORE the module setup
#           sequence (SET REGION/GEN2/POWER/ANT/DWELL/RFMODE/SESSION/
#           TARGET/Q + CMD_START_ASYNC_FULL + 0.3s settle), so every scan
#           silently lost ~2-2.5s of its configured scan_timeout to setup
#           overhead before the module had even started inventorying tags
#           (e.g. scan_timeout=12 in config.json only yielded ~9.5s of real
#           inventory time). run_async_scan() now resets start_time/
#           last_new_tag_time to the moment right after CMD_START_ASYNC_FULL
#           is sent and settled - scan_timeout is now purely inventory time,
#           not setup+inventory time. A fallback start_time is still set
#           before the serial connection is opened, so duration/logging
#           stay well-defined even if the connection fails outright.


================================================================================
VERSION - 1.0.0.(3)
================================================================================
Date 06 August 2026
- Reader 1.0.0(3):

#        1) ROOT-CAUSE FIX (garbled/impossible EPCs like
#           "86000000001B4D0F91B1A4FF" mixed in with real reads like
#           "860015332900000000002E84"):
#           parse_fast_mode_correct() computed the end of every received
#           frame as `idx + 2 + datalen + 2`. Per the doc's own general
#           frame format (sec 3.1, "Reader-to-Host Communication Frame"):
#               Header(1) + DataLength(1) + CommandCode(1) + StatusCode(2)
#               + Data(datalen) + CRC-16(2)
#           the correct frame length is `7 + datalen`, i.e. the old formula
#           was undercounting every single frame by exactly 3 bytes (the
#           2-byte Status Code was never folded into the boundary math,
#           even though it WAS being read out manually a few lines below).
#           Effect: after every tag/heartbeat frame, `idx` was advanced 3
#           bytes short of the real next-frame start, so the last 3 bytes
#           of the frame (part of its own CRC) were re-scanned as
#           candidate frame starts. Whenever those leftover bytes lined up
#           with the *next* real frame's FF..AA header by coincidence, the
#           parser spliced two frames together and produced a corrupted
#           "tag" - explaining the intermittent garbage reads that always
#           carried a real-looking prefix (e.g. "8600") followed by junk.
#           Fixed: frame_end is now computed correctly (`idx + 7 +
#           datalen`), AND every candidate frame is now CRC-validated
#           (same CRC-16/XMODEM algorithm as _calc_crc/Appendix 4, applied
#           to received frames exactly as verified against the doc's own
#           receiving-command worked example in Appendix 4, CRC 635C,
#           reproduced exactly) before any of its bytes are trusted. Any
#           frame that fails CRC (e.g. a splice, a torn read, electrical
#           noise) is now rejected and the parser resyncs byte-by-byte
#           instead of being parsed as a bogus tag.
#        2) NEW: explicit heartbeat-packet detection. Heartbeat packets
#           (doc sec 5.6.2.2, sent automatically every ~15s because
#           CMD_START_ASYNC_FULL's Search Flags has BIT7=1) share Command
#           Code 0xAA with real tag packets, but their Data field starts
#           with the 4-byte ASCII marker "XTSJ" instead of Metadata Flags.
#           These are now recognized and skipped explicitly (verified
#           against the doc's own heartbeat worked example, CRC 1724,
#           reproduced exactly) instead of being fed into the tag-field
#           parser, which used to interpret "XTSJ" bytes as a bogus
#           Metadata Flags value.
#        3) NEW: auto-tuning of antenna dwell time and quiet-stop
#           ("new tag" timeout), computed fresh every scan cycle from
#           scan_timeout x the currently configured antenna count
#           (see compute_auto_timing()). Enabled by default
#           ("auto_tune_timing": true, or key absent). config.json's
#           "scan_newtag" is ignored while auto-tuning is on, as
#           requested. Set "auto_tune_timing": false to fall back to the
#           1.0.0(2) manual behavior (antenna_dwell_ms / scan_newtag read
#           directly from config.json).
#        4) No other logic changed from 1.0.0(2): Target stays Dynamic
#           A-B, Q stays dynamic, SET POWER/ENABLE ANT are still built
#           dynamically from config.json's "antenna" list. All command
#           tables (RF MODE, SESSION, TARGET, Q, ENABLE ANT, SET POWER,
#           SET DWELL) were re-verified byte-for-byte against the
#           protocol doc's own worked examples for this release and are
#           unchanged - they were already correct.

- React 1.0.0(3):
  - CabinetStatusComponent.js

#        1) ROOT-CAUSE FIX (MQTT broker IP could only be changed by
#           editing source code):
#           CabinetStatusComponent.js previously defined the broker as a
#           hardcoded module-level constant:
#               const BROKER = { id: "broker1", ws: "ws://20.81.43.213:15675/ws",
#                                 user: "guest", pass: "guest" };
#           Changing the broker IP meant editing this line in the React
#           source and rebuilding - a full `npm run dev` restart when run
#           locally, or a full `docker build` + `docker rm` + `docker run`
#           cycle when run via run.bat/run.sh, since the value was baked
#           into the compiled JS bundle. There was no way to point the
#           dashboard at a different broker without a rebuild.
#           Fixed: the BROKER constant is removed entirely. The broker
#           WebSocket URL/user/pass are now fetched at runtime from the
#           backend (GET /api/broker-config, served by server.js - see
#           Server 1.0.0(3) below) into a new `brokerConfig` state value,
#           and the MQTT-connect useEffect now depends on `brokerConfig`
#           instead of a constant. Only the IP is operator-configurable;
#           port (15675) and credentials (guest/guest) are unchanged and
#           still fixed on the server side.
#        2) NEW: live broker switching with no restart/rebuild/reload.
#           A Socket.IO listener for "broker-config-update" was added
#           alongside the existing socket.on("cabinet-update"/
#           "cabinet-removed") handlers. When the server broadcasts this
#           event (because the broker IP was changed - either via the UI
#           below, via the HTTP API, or by editing broker-config.json on
#           disk), `brokerConfig` state updates, which - because it is a
#           dependency of the MQTT-connect effect - causes React to run
#           that effect's cleanup (client.end(true), disconnecting from
#           the OLD broker) and then reconnect fresh to the NEW broker.
#           This happens in every open browser tab simultaneously, with
#           no page reload and no interruption to cabinet polling/config
#           loading, which are unaffected (separate effects/state).
#        3) NEW: minimal, intentionally inconspicuous broker-IP control.
#           A tiny fixed-position dot (8px, ~35% opacity, no border, no
#           label, no tooltip text) is rendered in the bottom-right
#           corner of the page at all times. Clicking it opens a small
#           (150px) popup - not a full modal - with a single IP text
#           input and Save/Cancel. Save POSTs the new IP to
#           /api/broker-config, applies the returned config to local
#           state immediately (so this tab doesn't wait on its own
#           broadcast round-trip), and shows the existing custom-alert
#           toast. This was deliberately kept small/unlabeled per
#           request, so only an operator who already knows it exists
#           would notice or use it - regular cabinet-floor users see no
#           visible "settings" affordance.
#        4) All new user-facing strings (this control only - the rest of
#           the file was already English) are in English: "Broker IP",
#           "Save", "Cancel", "IP is required", "Broker switched to
#           <ip>", "Failed to save broker IP".
#        5) No other logic changed from 1.0.0(2): cabinet registry sync
#           (cabinet-update/cabinet-removed), MQTT message handling
#           (handleMqttMessage), scan/diagnosis/session/lock/light
#           commands, and the Startscan/Stopscan signature logic
#           (buildScanSignature - see the earlier "Startscan/Stopscan
#           Signature Update" section below) are all unchanged and were
#           re-checked against 1.0.0(2) behavior for this release.

   - Server.js:

#        1) NEW: runtime-editable broker configuration, backing the
#           React 1.0.0(3) changes above. A small file, broker-config.json
#           (containing only {"ip": "..."}; port/user/pass remain fixed
#           server-side constants of 15675/guest/guest, unchanged from
#           what was previously hardcoded in React), is read on startup
#           from the same folder as server.js - mirroring how
#           registry-data.json is already loaded/persisted.
#        2) NEW: GET /api/broker-config - returns the current broker as
#           { ws, user, pass } (ws pre-built as "ws://<ip>:15675/ws") for
#           the React dashboard to fetch on mount, replacing the old
#           hardcoded BROKER constant described above.
#        3) NEW: POST /api/broker-config - accepts { ip }, updates the
#           in-memory config, persists it to broker-config.json, and
#           broadcasts the new config to every connected client via
#           Socket.IO event "broker-config-update". This is what the
#           React hidden-dot control (React 1.0.0(3), item 3) calls, and
#           is also usable directly (e.g. curl) without the UI.
#        4) NEW: fs.watchFile on broker-config.json, so a manual edit of
#           the file on disk (no HTTP call at all) is also detected
#           (~1s poll interval) and triggers the same in-memory reload +
#           "broker-config-update" broadcast as the POST endpoint above.
#           A short (1.5s) guard window after the server's own POST-
#           triggered write prevents that write from being redundantly
#           re-broadcast as if it were an external edit.
#        5) DOCKER NOTE (documented in-code, not a code change per se):
#           broker-config.json lives inside whatever filesystem server.js
#           sees. Inside a container, that is the container's own
#           filesystem by default - editing the file on the Docker HOST
#           has no effect unless that file is bind-mounted into the
#           container. run.bat and run.sh were updated accordingly (see
#           below) to mount a host-side data/broker-config.json (and
#           data/registry-data.json, for the same reason) into the
#           container, so the same fs.watchFile mechanism above also
#           reacts to edits made from the host, and both files now
#           survive `docker rm` + re-run instead of resetting to the
#           image's baked-in defaults.
#        6) No other server logic changed from 1.0.0(2)/prior: cabinet
#           auto-registration (autoRegisterCabinet), registry
#           persistence (registry-data.json), the heartbeat "kept, not
#           removed" behavior, and all existing /api/* endpoints are
#           unchanged.

  - run.bat / run.sh (Docker launch scripts):
#        1) NEW: both scripts now create a `data/` folder next to
#           themselves (auto-created on first run, never overwrites an
#           existing file) containing broker-config.json (seeded with
#           the previous hardcoded default, "20.81.43.213") and
#           registry-data.json (seeded empty, {}).
#        2) NEW: both scripts now bind-mount those two files into the
#           container (`-v .../broker-config.json:/app/broker-config.json`,
#           same for registry-data.json), so they are read/written on
#           the HOST filesystem, not lost on every `docker rm` +
#           `docker run` cycle the scripts already perform.
#        3) run.bat is the native-Windows entry point (Command Prompt /
#           double-click); run.sh is for WSL/Git Bash/Linux shells. Both
#           now target the same image/container name (clientstatus) and
#           NUMBER_OF_CABINETS (2), and are otherwise unchanged from
#           their prior versions (Docker install check, image build,
#           container run, auto-restart policy).

================================================================================
VERSION - 1.0.0.(2)
================================================================================
Date 04 August 2026

- Module 1.0.0(2):
  Set version
  Set Cut off range 7 - 20
  Remove New Tag

- Dip.py 1.0.0

- Reader 1.0.0(2)
  NEW: antenna dwell time is now explicitly configured via command
#           0x95 ("Set the frequency hopping table, antenna dwell time, and
#           duty cycle", doc section 7.3) instead of being left at the
#           module's factory default of 4 seconds/antenna.
#           CRC/frame format verified against the doc's own worked example
#           (Set antenna dwell time to 5 seconds -> FF 05 95 02 00001388
#           D5AB, reproduced exactly by build_dwell_command(5000)).

  Root cause fixed: with scan_timeout capped low and multiple
#           antennas enabled, the module's own 4s/antenna default made a
#           full round-robin cycle (N_antennas * 4s) eat a large/unstable
#           fraction of the scan budget, so the hard `elapsed >=
#           MAX_SCAN_TIME` cutoff would land at a different point in the
#           round-robin cycle each run - producing run-to-run variance in
#           which tags get read even with nothing physically changed.
#           Setting an explicit, shorter dwell time (config.json key
#           "antenna_dwell_ms", default 2000ms if not set - no config.json
#           edit required) gives a full round-robin cycle comfortable
#           headroom inside scan_timeout, so the scan reliably completes
#           at least one full pass every run.

No other logic changed from 1.0.0(2): Target stays Dynamic A-B
#           (this was already the fix applied vs the earlier Static-A
#           regression), Q stays dynamic, SET POWER/ENABLE ANT are still
#           built dynamically from config.json's "antenna" list.


================================================================================
VERSION - 1.0.0.(1)
================================================================================
Date: 31 July 2026

- Module 1.0.0(1):
  Set dbm, dim brightness
  Set Cut off range 7 - 20
  Set New Tag range 10 - 20
  

 Hardware: Reader runs on Raspberry Pi 3.

 What's new in this revision:
    
   - module.py / apps.py: update for show reader version

================================================================================
================================================================================
 Hardware: Reader runs on Raspberry Pi 4.

 What's new in this revision:
   - reader.py: added adaptive timeout logic for scans. When a scan is
     performed and the tag obtained differs from the previous tag, the
     timeout is extended based on the newtag value (see section 7 below).
   - module.py / apps.py: the value range used for scanning a new tag was
     changed from 7-10 to 10-15 (see section 8 below).

  - Light control: added light_pwm.py, driving the cabinet light via
     PWM. Brightness is controlled by a "dim" value (0-100, inverted
     scale - see section 5 below).
   - Sensor: added sht20.py, reading temperature and humidity from the
     SHT20 sensor.
   - The module that handles cabinet status/commands now also sends the
     brightness value out (as part of its status broadcast / command
     handling), so the dashboard can display and set the current
     brightness alongside the existing scan-signature fields.

 Everything below this point is unchanged from the previous revision
 (Startscan/Stopscan signature tracing) unless noted in the new sections.
================================================================================

================================================================================
 README - Startscan/Stopscan Signature Update
================================================================================

This update adds a traceable "signature" (time + sig) to Startscan and
Stopscan commands, so it becomes possible to manually trace which source
sent a given command later (via RabbitMQ/broker logs), without ever
blocking or rejecting a scan based on that signature.

The device NEVER verifies, matches, or rejects anything based on the
signature. It only reads it (if present) and passes/logs it as-is. Manual
tracing (recomputing md5(source + eth0 + time) by hand against candidate
sources) is done entirely outside these files - see trace_sig.html /
trace_sig.py from this same conversation.

--------------------------------------------------------------------------------
 1. CabinetStatusComponent.js (React dashboard)
--------------------------------------------------------------------------------
This is the ONLY file that actually COMPUTES a signature. Everything else
just reads and passes it through.

What was added:
  - A pure-JavaScript MD5 implementation (no external library needed).
  - buildScanSignature(targetMacAddress): computes
        time = current local time, formatted "YYYY-MM-DD HH:MM:SS"
        sig  = md5(source + eth0_target_no_colons_lowercase + time)
    where:
      * source = window.location.hostname (auto-detected - the same code
                 works no matter where this dashboard is deployed, no
                 per-install configuration needed).
      * eth0   = the TARGET cabinet's own eth0 MAC address (NOT this
                 browser's), taken from that cabinet's own periodic
                 status broadcast.
  - The cabinet's own eth0 MAC ("macAddress" field) is now captured into
    React state whenever a status broadcast is received - previously this
    field was never stored anywhere even though the device always sent it.
  - sendCommand()'s "scan" (Startscan) and "close" (Stopscan) cases now
    spread { time, sig } from buildScanSignature() into the outgoing MQTT
    payload.
  - If the target cabinet's eth0 MAC isn't known yet (e.g. no status
    received for it since page load), buildScanSignature() simply returns
    {} - time/sig are omitted, and the command is sent exactly as before
    (unsigned). The scan is never blocked by this.

--------------------------------------------------------------------------------
 2. mqtts.py (main MQTT command handler, runs on the Raspberry Pi)
--------------------------------------------------------------------------------
Signature-related changes:
  - Startscan and Stopscan now parse two extra optional fields from the
    incoming payload: "time" and "sig". Neither is verified or checked
    against anything - they are read as plain strings.
  - One log line was added for each command:
        [SCAN] Startscan sessionid=<sid>, mac=<mac> time='<time>' sig='<sig>'
        [SCAN] Stopscan  sessionid=<sid>, mac=<mac> time='<time>' sig='<sig>'
    If a command arrives without time/sig (old client code, or any sender
    that doesn't implement the signature), these fields simply log as
    empty strings - there is no rejection, warning escalation, or lookup
    table of any kind on this side.
  - Before starting the actual scan worker, mqtts.py now also prepares a
    "trigger_time" / "trigger_sig" pair that will be attached to the
    "Scanning" broadcast (published later by startstops.py once the door
    is confirmed closed):
      * If the incoming Startscan already had time+sig -> those exact
        same values are passed straight through unchanged.
      * If the incoming Startscan had no time/sig (old code) -> a fresh
        one is computed on the spot: md5(eth0 + time_now) - note this has
        NO source component at all, which is exactly how you distinguish
        "old code / no source" when tracing later (try an empty-string
        candidate).
  - These trigger_time/trigger_sig values are passed into
    startstops.main() as new keyword arguments.

Brightness-related change (Revision 3):
  - mqtts.py now also handles a brightness/"dim" command, updating
    config.json's "dim" field so that light_pwm.py (already running in
    its no-arg, config-watching mode - see section 5) picks the new
    value up live.
  - The current brightness value is included in the module's outgoing
    status broadcast, alongside the existing mac/hostname/session fields,
    so the dashboard can show and set it without a separate poll.

Non-signature change also included in this same update:
  - SCAN_MUTEX (a cross-process file lock, /tmp/scan.lock) is acquired
    before starting a scan and released only after the ENTIRE scan
    finishes. This is what actually prevents two scans (e.g. a manual
    Startscan and a door-triggered scan) from running at the same time
    and corrupting each other's serial-port access to the RFID reader.
    This mechanism is completely independent of the signature - it is
    the actual collision-prevention fix, whereas the signature is purely
    for later human tracing/auditing.

--------------------------------------------------------------------------------
 3. radarScanControl.py (door sensor -> automatic scan trigger)
--------------------------------------------------------------------------------
Signature-related change:
  - When the door closes and a scan is triggered automatically (not via
    any MQTT Startscan command), this file now computes its OWN
    trigger_time / trigger_sig pair, using the literal source string
    "GPIO":
        trigger_sig = md5("GPIO" + eth0 + trigger_time)
    This makes automatic door-triggered scans traceable the same way as
    any other source - when tracing a "Scanning" broadcast's sig later,
    trying the candidate "GPIO" reveals it was the door sensor, not any
    MQTT client.
  - These values are passed into startstops.main() as trigger_time /
    trigger_sig, exactly like mqtts.py does for the MQTT-triggered path.

Non-signature change also included in this same update:
  - Before starting a door-triggered scan, this file now also acquires
    mqtts.SCAN_MUTEX (the same cross-process lock mqtts.py uses). If a
    manual MQTT Startscan is already running, the door-triggered scan is
    skipped this cycle (and the radar hardware is turned back off) rather
    than colliding with the in-progress scan. This is the fix that makes
    door-triggered and MQTT-triggered scans mutually exclusive.

--------------------------------------------------------------------------------
 4. startstops.py (actual scan door-wait + hardware scan logic)
--------------------------------------------------------------------------------
Signature-related change:
  - main() now accepts two new optional keyword arguments:
        trigger_time="", trigger_sig=""
    These are simply inserted into the "Scanning" broadcast payload once
    the door-closed timer confirms and scanning is about to begin:
        {"mac": ..., "hostname": ..., "cmd": "Scanning",
         "sessionId": ..., "time": trigger_time, "sig": trigger_sig}
    startstops.py does not compute, validate, or interpret these values
    in any way - it only receives them from its caller (mqtts.py or
    radarScanControl.py) and republishes them as-is.

Non-signature change also included in this same update:
  - main() now calls scan_thread.join() after start_scan() spawns the
    real hardware-scanning thread, instead of returning immediately.
    Previously, main() returned the instant the thread was spawned (before
    any actual RFID reading happened), which caused SCAN_MUTEX to be
    released far too early - letting a second scan slip in and collide
    with the first one mid-read (causing "No valid baud rate found" /
    false zero-tag results). Now main() only returns once the entire
    hardware scan (baud detection, RFID read, reader shutdown) has
    genuinely finished, so SCAN_MUTEX stays held for its whole real
    duration.

--------------------------------------------------------------------------------
 5. light_pwm.py (Revision 3 - light / brightness control)
--------------------------------------------------------------------------------
  - Drives the cabinet light via software PWM (RPi.GPIO), on the
    Raspberry Pi 4.
  - Brightness is stored in config.json as "dim" (0-100, step 10),
    inverted relative to raw PWM duty cycle: dim=0 is brightest
    (PWM=100%), dim=100 is dimmest (PWM=0%).
  - Run with no argument, it watches config.json continuously and
    applies any new "dim" value live, with no restart needed - this is
    what lets the brightness command described in section 2 (mqtts.py)
    take effect immediately after the dashboard sends it.
  - Can also be run with an explicit raw PWM percentage (manual testing)
    or with --fade for a continuous breathing-effect loop.

--------------------------------------------------------------------------------
 6. sht20.py (Revision 3 - temperature / humidity sensor)
--------------------------------------------------------------------------------
  - Reads temperature and humidity from the SHT20 sensor attached to the
    Raspberry Pi 4.
  - Readings are reported alongside the module's other status fields
    (mac/hostname/session/brightness), so the dashboard can display
    current temperature and humidity together with the rest of the
    cabinet's live status.

--------------------------------------------------------------------------------
 7. reader.py (NEW in Revision 5 - adaptive scan timeout on tag mismatch)
--------------------------------------------------------------------------------
  - Purpose: reduce false "missing tag" results caused by a tag simply
    not being read yet, by giving the scan extra time to re-detect a tag
    it previously saw, before concluding the tag is genuinely gone.
  - Behavior:
      1. During a scan, if the tag currently read does NOT match the
         previously read tag (i.e. the tag set changed / a previously
         seen tag is now missing), the scan timeout is extended. The
         amount of the extension is based on the "newtag" value (the
         tag that was newly read in place of the expected one).
      2. After this first extension, the reader attempts another read.
         If, on this first re-read, the previously-missing tag is STILL
         not read back, a SECOND timeout extension is applied - again
         based on the newtag value.
      3. If, after this second extension, the originally expected tag is
         still not read, the reader treats this as a strong indication
         that the tag currently being read (newtag) is in fact the real,
         current tag - i.e. the expected tag was most likely physically
         removed/taken, rather than just missed by a weak read.
  - Net effect: a tag mismatch no longer immediately triggers a
    "missing" result on the first read - the reader now gives up to two
    extra timeout extensions (sized according to newtag) to rule out a
    transient read miss before concluding the original tag was actually
    removed.

--------------------------------------------------------------------------------
 8. module.py / apps.py (UPDATED in Revision 5 - new-tag scan value range)
--------------------------------------------------------------------------------
  - The value range used when scanning for a new tag was changed:
        Old range: 7-10
        New range: 10-15
  - This change applies identically in both module.py and apps.py.

--------------------------------------------------------------------------------
 Summary: what "signature" means across the whole system
--------------------------------------------------------------------------------
  - It is for TRACING ONLY. No file in this update rejects or blocks a
    scan because of a missing/invalid/mismatched signature.
  - Formula everywhere: sig = md5(source + eth0_no_colons_lowercase + time)
  - Three possible "source" values you'll see when tracing later:
      1. Whatever window.location.hostname resolves to on the dashboard
         that sent a signed Startscan (e.g. "iriscabinet.com").
      2. "" (empty) - Startscan/Stopscan arrived with no signature at all
         (old client code, or the "Scanning" broadcast for such a case).
      3. "GPIO" - the scan was triggered automatically by the door sensor,
         not by any MQTT command.
  - To trace a captured (time, sig, eth0) triple back to its source, use
    trace_sig.html (interactive) or trace_sig.py (script) from this same
    conversation - manually try candidate source strings until one
    produces a matching md5 hash. There is no way to reverse a hash
    directly; matching against known candidates is the only method.

  - Brightness (Revision 3) is unrelated to the signature system above -
    it is a separate "dim" value read/written via config.json and
    applied live by light_pwm.py (section 5), with the current value
    included in the module's status broadcast (section 2).

  - The adaptive timeout logic in reader.py (Revision 5, section 7) and
    the new-tag value range change in module.py/apps.py (Revision 5,
    section 8) are both unrelated to the signature system - they only
    affect how long/how the reader waits before deciding a tag is truly
    missing versus just not-yet-read.
================================================================================