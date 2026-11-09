#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ledstatus.py
__version__ = "1.0.0(15) - 8-LED local status display (mirror of the React dashboard)"

# =====================================================================================================================================
#      LED STATUS
#      Name                       : LED STATUS - local fallback display for the React dashboard
#      Version                    : 1.0.0(15)
#      Date Created               : 03-11-2026
#      Purpose                    : If the React monitor breaks, the Raspberry Pi still shows the
#                                   cabinet state on 8 LEDs, using the SAME data the dashboard uses.
#
#      HOW IT WORKS (nothing existing is modified):
#        This is a standalone, read-only process. It does not import run.py / mqtts.py / reader.py
#        and does not touch the lock pin (24), light pins (16/18) or reader pin (23).
#
#        Data sources (all read-only):
#          1. MQTT topic "out" on the local mosquitto  -> exactly what React receives:
#                 {"cmd":"Open"} {"cmd":"Close"} {"cmd":"Scanning"},
#                 inventory result (has key "macaddress")  -> READY,
#                 status payload (Door1 / Lock1 / Light)
#          2. MQTT topic "in"  -> only to detect Reboot / Stopscan (same as React)
#          3. status.json      -> polled every 0.5 s for Lock1 / Light (works even if MQTT drops)
#          4. GPIO17 (door)    -> INPUT only, read-only, same pin and logic as library/door.py
#
#      LED MAP (8 LEDs, BCM numbering):
#          LED1  GPIO 4   DOOR OPEN     on  = door open
#          LED2  GPIO 12  DOOR CLOSE    on  = door closed
#          LED3  GPIO 13  LOCK          on  = locked
#          LED4  GPIO 25  UNLOCK        on  = unlocked
#          LED5  GPIO 19  LIGHT ON      on  = light on
#          LED6  GPIO 20  LIGHT OFF     on  = light off
#          LED7  GPIO 21  SCANNING      blink = scanning (React "Scanning")
#          LED8  GPIO 7   READY         on  = ready (React "Ready")
#          All LEDs blink together while the cabinet is rebooting.
#
#      These pins are NOT used by any existing module (used: 2,3,5,6,14,15,16,17,18,22,23,24,26,27).
#      Override them in config.json (optional, read-only here):
#          "led_pins": [4,12,13,25,19,20,21,7], "led_active_low": false
#
#      USAGE:
#          python3 ledstatus.py              # run normally (use the systemd service)
#          python3 ledstatus.py --test       # sweep the 8 LEDs once and exit (wiring check)
#          python3 ledstatus.py --simulate   # no GPIO, prints LED state in the console
#      Author                     : Saifuddin
# ======================================================================================================================================

import os
import sys
import json
import time
import signal
import socket
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR  = os.path.join(BASE_DIR, "library")
sys.path.insert(0, LIB_DIR)

# ---------------------------------------------------------------
# Logger: reuse the project logger when available, otherwise fall
# back to plain logging (so this file can also be tested off-Pi).
# ---------------------------------------------------------------
try:
    from logger import get_logger
    log = get_logger("ledstatus")
except Exception:
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)-8s] [ledstatus] %(message)s")
    log = logging.getLogger("ledstatus")

try:
    from network_utils import get_local_ip
except Exception:
    def get_local_ip():
        return "127.0.0.1"

STATUS_FILE = os.path.join(BASE_DIR, "status.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# =====================================================
#  LED CONFIG
# =====================================================
LED_NAMES = ["DOOR_OPEN", "DOOR_CLOSE", "LOCK", "UNLOCK",
             "LIGHT_ON", "LIGHT_OFF", "SCANNING", "READY"]
DEFAULT_LED_PINS = [4, 12, 13, 25, 19, 20, 21, 7]

DOOR1_GPIO       = 17      # same as library/door.py (read-only here)
DOOR_GPIO_ENABLE = True    # False -> door is taken from MQTT/status.json only
CLOSE_TO_READY_S = 30      # same as React: after "Close", back to Ready after 30 s
MQTT_PORT        = 1883
LOOP_DT          = 0.05    # LED refresh period (50 ms)
STATUS_POLL_S    = 0.5

# Cabinet states (identical to React "doorStatus")
READY, OPEN, CLOSE, SCANNING, REBOOTING = "Ready", "Open", "Close", "Scanning", "Rebooting"


def _read_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


# =====================================================
#  LED DRIVER (real GPIO or simulation)
# =====================================================
class LedDriver:
    def __init__(self, pins, active_low=False, simulate=False):
        self.pins       = pins
        self.active_low = active_low
        self.simulate   = simulate
        self.GPIO       = None
        self._last      = [None] * len(pins)

        if not simulate:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            for p in pins:
                GPIO.setup(p, GPIO.OUT, initial=self._level(False))
            if DOOR_GPIO_ENABLE:
                GPIO.setup(DOOR1_GPIO, GPIO.IN)   # input only - same as door.py

    def _level(self, on):
        on = bool(on)
        return (not on) if self.active_low else on

    def write(self, states):
        for i, on in enumerate(states):
            on = bool(on)
            if self._last[i] == on:
                continue
            self._last[i] = on
            if self.GPIO:
                self.GPIO.output(self.pins[i], self._level(on))
        if self.simulate:
            line = " ".join(("[%s]" if s else " %s ") % n[:5] for n, s in zip(LED_NAMES, states))
            print("\r" + line, end="", flush=True)

    def read_door_open(self):
        """True=open, False=closed, None=unknown. Same logic as door.py (1 = Open)."""
        if not self.GPIO or not DOOR_GPIO_ENABLE:
            return None
        try:
            return self.GPIO.input(DOOR1_GPIO) == 1
        except Exception:
            return None

    def close(self):
        try:
            self.write([False] * len(self.pins))
            if self.GPIO:
                # clean ONLY our own LED pins - never GPIO.cleanup() everything,
                # and never the door/lock/light pins.
                self.GPIO.cleanup(self.pins)
        except Exception:
            pass


# =====================================================
#  STATE (mirror of the React logic)
# =====================================================
class CabinetState:
    def __init__(self):
        self.lock        = threading.Lock()
        self.state       = READY      # Ready / Open / Close / Scanning / Rebooting
        self.door_open   = None       # True / False / None (unknown)
        self.locked      = None
        self.light_on    = None
        self.mqtt_ok     = False
        self.last_msg    = 0.0
        self._close_at   = None       # when to fall back to Ready after "Close"
        self._reboot_at  = None

    # ---------- helpers ----------
    def _set_door(self, is_open):
        if is_open is None:
            return
        self.door_open = is_open

    # ---------- MQTT "out" (same branches as React handleMqttMessage) ----------
    def on_out(self, payload):
        with self.lock:
            self.last_msg = time.time()

            if not isinstance(payload, dict):
                payload = {"cmd": str(payload).strip()}

            # 1) INVENTORY RESULT -> Ready
            if "macaddress" in payload:
                self._to_ready()
                self._apply_lock_light(payload)
                return

            # 2) STATUS OBJECT (Door1 / Lock1 / Light)
            if "Light" in payload or "Lock1" in payload or "Door1" in payload:
                self._apply_lock_light(payload)
                d = payload.get("Door1")
                if d == "Open":
                    self._set_door(True)
                    self.state, self._close_at = OPEN, None
                elif d == "Close":
                    self._set_door(False)
                return

            # 3) EVENTS ("cmd")
            cmd = str(payload.get("cmd", "")).strip().title()
            if cmd == "Open":
                self._set_door(True)
                self.state, self._close_at = OPEN, None
            elif cmd == "Close":
                self._set_door(False)
                self.state = CLOSE
                self._close_at = time.time() + CLOSE_TO_READY_S
            elif cmd == "Scanning":
                self.state, self._close_at = SCANNING, None
            elif cmd == "Ready":
                self._to_ready()
            elif str(payload.get("event", "")).title() == "Reboot":
                self._to_reboot()

    # ---------- MQTT "in" ----------
    def on_in(self, payload):
        if not isinstance(payload, dict):
            return
        with self.lock:
            if str(payload.get("cmd", "")).strip().title() == "Reboot":
                self._to_reboot()
            elif str(payload.get("rfid", "")).strip().title() == "Stopscan":
                if self.state == SCANNING:       # React: Stopscan -> Ready
                    self._to_ready()

    # ---------- status.json / GPIO ----------
    def on_status_file(self, data):
        with self.lock:
            self._apply_lock_light(data)

    def on_door_gpio(self, is_open):
        """Called only when the debounced GPIO door state CHANGES."""
        with self.lock:
            self._set_door(is_open)
            if is_open:
                self.state, self._close_at = OPEN, None
            elif self.state == OPEN:
                self.state = CLOSE
                self._close_at = time.time() + CLOSE_TO_READY_S

    # ---------- internals ----------
    def _apply_lock_light(self, d):
        if "Lock1" in d:
            self.locked = (d["Lock1"] == "Lock")
        if "Light" in d:
            self.light_on = (d["Light"] == "On")

    def _to_ready(self):
        self.state, self._close_at, self._reboot_at = READY, None, None

    def _to_reboot(self):
        self.state, self._close_at = REBOOTING, None
        self._reboot_at = time.time()

    def tick(self):
        """Timers (30 s after Close -> Ready, like React)."""
        with self.lock:
            now = time.time()
            if self.state == CLOSE and self._close_at and now >= self._close_at:
                self._to_ready()

    # ---------- LED frame ----------
    def frame(self, now):
        with self.lock:
            blink_slow = int(now * 2) % 2 == 0          # 1 Hz
            blink_fast = int(now * 6) % 2 == 0          # 3 Hz

            if self.state == REBOOTING:
                return [blink_fast] * 8

            door_open  = bool(self.door_open)
            door_known = self.door_open is not None

            return [
                door_known and door_open,                # DOOR OPEN
                door_known and not door_open,            # DOOR CLOSE
                self.locked is True,                     # LOCK
                self.locked is False,                    # UNLOCK
                self.light_on is True,                   # LIGHT ON
                self.light_on is False,                  # LIGHT OFF
                blink_slow if self.state == SCANNING else False,   # SCANNING
                self.state == READY,                     # READY
            ]


# =====================================================
#  MQTT (read-only subscriber)
# =====================================================
def _own_names():
    names = {socket.gethostname().lower()}
    cfg = _read_json(CONFIG_FILE)
    if cfg.get("hostname"):
        names.add(str(cfg["hostname"]).lower())
    st = _read_json(STATUS_FILE)
    for k in ("mac", "hostname"):
        if st.get(k):
            names.add(str(st[k]).lower())
    return names


class MqttReader:
    def __init__(self, cab):
        self.cab    = cab
        self.client = None
        self._names = _own_names()
        self._names_t = time.time()

    def _is_mine(self, payload):
        """Accept the message if it has no mac/hostname or it is this cabinet's."""
        if not isinstance(payload, dict):
            return True
        tag = payload.get("mac") or payload.get("hostname")
        if not tag or str(tag).lower() == "all":
            return True
        if time.time() - self._names_t > 10:
            self._names, self._names_t = _own_names(), time.time()
        return str(tag).lower() in self._names

    def start(self):
        import paho.mqtt.client as mqtt
        cid = f"{socket.gethostname()}-ledstatus"
        try:   # paho-mqtt 2.x needs the callback API version; 1.x does not have it
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, clean_session=True)
        except AttributeError:
            c = mqtt.Client(client_id=cid, clean_session=True)

        def on_connect(client, userdata, flags, rc):
            ok = (rc == 0)
            self.cab.mqtt_ok = ok
            log.info("[LED] MQTT connected rc=%s", rc)
            if ok:
                client.subscribe([("out", 0), ("in", 0)])

        def on_disconnect(client, userdata, rc, *a):
            self.cab.mqtt_ok = False
            log.warning("[LED] MQTT disconnected rc=%s", rc)

        def on_message(client, userdata, msg):
            raw = msg.payload.decode(errors="ignore").strip()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw
            try:
                if msg.topic == "out" and self._is_mine(payload):
                    self.cab.on_out(payload)
                elif msg.topic == "in":
                    if isinstance(payload, dict):
                        mt = str(payload.get("mac", "")).lower()
                        if mt in ("", "all") or mt in self._names:
                            self.cab.on_in(payload)
            except Exception as e:
                log.error("[LED] handler error: %s", e)

        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        c.reconnect_delay_set(min_delay=1, max_delay=10)

        # Try the same address the other modules use first, then loopback.
        hosts = []
        for h in (get_local_ip(), "127.0.0.1"):
            if h not in hosts:
                hosts.append(h)
        while True:
            for h in hosts:
                try:
                    c.connect(h, MQTT_PORT, 60)
                    c.loop_start()
                    self.client = c
                    log.info("[LED] MQTT client started broker=%s id=%s", h, cid)
                    return
                except Exception as e:
                    log.warning("[LED] MQTT connect %s failed: %s", h, e)
            time.sleep(3)          # keep trying; LEDs still work from status.json + GPIO

    def stop(self):
        try:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:
            pass


# =====================================================
#  WORKERS
# =====================================================
def status_file_worker(cab, stop):
    """Poll status.json (mtime-gated) for Lock1 / Light - independent of MQTT."""
    last_mtime = 0
    while not stop.is_set():
        try:
            m = os.path.getmtime(STATUS_FILE)
            if m != last_mtime:
                last_mtime = m
                cab.on_status_file(_read_json(STATUS_FILE))
        except Exception:
            pass
        stop.wait(STATUS_POLL_S)


def door_gpio_worker(cab, drv, stop):
    """Read-only, debounced (3 identical samples @ 50 ms) door watcher on GPIO17."""
    stable, cnt, cand = None, 0, None
    while not stop.is_set():
        cur = drv.read_door_open()
        if cur is not None:
            if cur == cand:
                cnt += 1
            else:
                cand, cnt = cur, 1
            if cnt >= 3 and cand != stable:
                first = stable is None
                stable = cand
                if first:
                    with cab.lock:
                        cab._set_door(stable)
                        if stable:
                            cab.state = OPEN
                else:
                    cab.on_door_gpio(stable)
        stop.wait(0.05)


# =====================================================
#  LED TEST / MAIN
# =====================================================
def load_pins():
    cfg   = _read_json(CONFIG_FILE)
    pins  = cfg.get("led_pins", DEFAULT_LED_PINS)
    if not (isinstance(pins, list) and len(pins) == 8 and all(isinstance(p, int) for p in pins)):
        log.warning("[LED] invalid led_pins in config.json, using defaults")
        pins = DEFAULT_LED_PINS
    return pins, bool(cfg.get("led_active_low", False))


def sweep_test(drv):
    n = len(drv.pins)
    for i in range(n):
        f = [False] * n; f[i] = True
        drv.write(f); time.sleep(0.25)
    for i in reversed(range(n)):
        f = [False] * n; f[i] = True
        drv.write(f); time.sleep(0.15)
    drv.write([True] * n);  time.sleep(0.6)
    drv.write([False] * n)


def main():
    simulate  = "--simulate" in sys.argv
    test_only = "--test" in sys.argv

    pins, active_low = load_pins()
    if simulate:
        drv = LedDriver(pins, active_low, simulate=True)
    else:
        try:
            drv = LedDriver(pins, active_low)
        except Exception as e:
            log.error("[LED] GPIO unavailable (%s) - running in simulation", e)
            drv = LedDriver(pins, active_low, simulate=True)

    log.info("[LED] pins=%s active_low=%s version=%s", pins, active_low, __version__)

    # Power-on LED test (also proves the wiring every time the service starts)
    sweep_test(drv)
    if test_only:
        drv.close()
        return

    cab  = CabinetState()
    stop = threading.Event()

    def _bye(*_):
        stop.set()
    signal.signal(signal.SIGINT,  _bye)
    signal.signal(signal.SIGTERM, _bye)

    # Seed from status.json so the LEDs are right immediately, before any MQTT message.
    cab.on_status_file(_read_json(STATUS_FILE))
    first = drv.read_door_open()
    if first is None:
        d = _read_json(STATUS_FILE).get("Door1")
        first = True if d == "Open" else (False if d == "Close" else None)
    cab._set_door(first)
    if first:
        cab.state = OPEN

    threading.Thread(target=status_file_worker, args=(cab, stop),      daemon=True).start()
    threading.Thread(target=door_gpio_worker,   args=(cab, drv, stop), daemon=True).start()

    reader = MqttReader(cab)
    threading.Thread(target=reader.start, daemon=True).start()   # never blocks the LEDs

    log.info("[LED] running")
    try:
        while not stop.is_set():
            cab.tick()
            drv.write(cab.frame(time.time()))
            stop.wait(LOOP_DT)
    finally:
        reader.stop()
        drv.close()
        log.info("[LED] stopped")


if __name__ == "__main__":
    main()
