"""
windows_agent.py
"""

import json
import os
import socket
import subprocess
import sys
import time
import winreg
import paho.mqtt.client as mqtt

# ── Config ─────────────────────────────────────────────────
CONFIG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "module.json")
BROKER_PORT     = 1883
TOPIC_CMD       = "win/cmd"
TOPIC_ACK       = "win/ack"
CONTAINER_NAME  = "module2policys"

# ── Load broker IP ─────────────────────────────────────────
def load_broker():
    try:
        with open(CONFIG_FILE) as f:
            ip = json.load(f).get("ip_broker", "localhost")
            print(f"[AGENT] Broker from config: {ip}")
            return ip
    except Exception as e:
        print(f"[AGENT] Cannot read {CONFIG_FILE}: {e} → using localhost")
        return "localhost"

# ── Change hostname via Windows Registry ────────────────────
def change_windows_hostname(new_hostname: str):
    paths = [
        r"SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName",
        r"SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName",
    ]
    for path in paths:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ComputerName", 0, winreg.REG_SZ, new_hostname)
        winreg.CloseKey(key)

    key3 = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\services\Tcpip\Parameters",
        0, winreg.KEY_SET_VALUE
    )
    winreg.SetValueEx(key3, "Hostname",    0, winreg.REG_SZ, new_hostname)
    winreg.SetValueEx(key3, "NV Hostname", 0, winreg.REG_SZ, new_hostname)
    winreg.CloseKey(key3)

# ── Restart Docker container dengan WINDOWS_HOSTNAME baru ──
def restart_docker_with_new_hostname(new_hostname: str):
    """
    Update env var WINDOWS_HOSTNAME di container yang sedang jalan,
    lalu restart supaya module.py membaca hostname baru.
    Caranya: stop → rm → run ulang dengan env baru.
    """
    try:
        # Ambil docker run command yang sedang dipakai (image name, ports, volumes)
        # Cara paling aman: pakai 'docker inspect' untuk dapat config lama
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}",
             CONTAINER_NAME],
            capture_output=True, text=True
        )
        mounts_raw = result.stdout.strip()

        # Buat volume args dari inspect
        volume_args = []
        for pair in mounts_raw.split():
            if ":" in pair:
                volume_args += ["-v", pair]

        # Ambil port mapping
        port_result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{range $p, $c := .NetworkSettings.Ports}}{{$p}}={{(index $c 0).HostPort}} {{end}}",
             CONTAINER_NAME],
            capture_output=True, text=True
        )
        ports_raw = port_result.stdout.strip()
        port_args = []
        for pair in ports_raw.split():
            if "=" in pair:
                container_port, host_port = pair.split("=")
                # container_port format: "5100/tcp" → ambil angkanya saja
                cp = container_port.split("/")[0]
                port_args += ["-p", f"{host_port}:{cp}"]

        # Ambil image name
        image_result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", CONTAINER_NAME],
            capture_output=True, text=True
        )
        image_name = image_result.stdout.strip() or "module2policys"

        print(f"[AGENT] Restarting Docker container '{CONTAINER_NAME}' "
              f"with WINDOWS_HOSTNAME={new_hostname} ...")

        # Stop & remove lama
        subprocess.run(["docker", "stop", CONTAINER_NAME],
                       capture_output=True)
        subprocess.run(["docker", "rm",   CONTAINER_NAME],
                       capture_output=True)

        # Run baru dengan env hostname baru
        run_cmd = (
            ["docker", "run", "-d",
             "--name", CONTAINER_NAME,
             "-e", f"WINDOWS_HOSTNAME={new_hostname}"]
            + port_args
            + volume_args
            + [image_name]
        )
        print(f"[AGENT] docker run cmd: {' '.join(run_cmd)}")
        subprocess.run(run_cmd, check=True)
        subprocess.run(["docker", "update", "--restart", "unless-stopped", CONTAINER_NAME],
                       capture_output=True)

        print(f"[AGENT] Docker container restarted OK with WINDOWS_HOSTNAME={new_hostname}")
        return True

    except Exception as e:
        print(f"[AGENT] Docker restart failed: {e}")
        return False

# ── MQTT callbacks ─────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(TOPIC_CMD)
        print(f"[AGENT] Connected ✓  subscribed → '{TOPIC_CMD}'")
        print(f"[AGENT] Hostname Now: {socket.gethostname()}")
    else:
        print(f"[AGENT] Connect failed rc={rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        print(f"[AGENT] ← {msg.topic}  {payload}")

        cmd = payload.get("cmd", "")

        # ── change_hostname ──────────────────────────────────
        if cmd == "change_hostname":
            new_hostname = payload.get("hostname", "").strip()
            if not new_hostname:
                _ack(client, "error", "hostname empty")
                return

            print(f"[AGENT] Changing hostname: {socket.gethostname()} → {new_hostname}")
            change_windows_hostname(new_hostname)
            print(f"[AGENT] Registry updated ✓")

            # Restart Docker container dengan hostname baru
            restart_docker_with_new_hostname(new_hostname)

            # Jadwalkan Windows restart
            subprocess.Popen("shutdown /r /t 5", shell=True)
            _ack(client, "ok",
                 f"Hostname changed to '{new_hostname}'. Restart in 5 seconds.")

        else:
            print(f"[AGENT] Unknown cmd: '{cmd}'")

    except PermissionError:
        msg_err = "PermissionError: run windows_agent.py as Administrator!"
        print(f"[AGENT] {msg_err}")
        _ack(client, "error", msg_err)
    except Exception as e:
        print(f"[AGENT] Error: {e}")
        _ack(client, "error", str(e))

def _ack(client, status, message):
    p = json.dumps({"status": status, "message": message})
    client.publish(TOPIC_ACK, p)
    print(f"[AGENT] → {TOPIC_ACK}  {p}")

# ── Main loop ──────────────────────────────────────────────
def main():
    broker = load_broker()
    print("=" * 45)
    print(f"[AGENT] Broker  : {broker}:{BROKER_PORT}")
    print(f"[AGENT] Listen  : topic='{TOPIC_CMD}'")
    print(f"[AGENT] Respond : topic='{TOPIC_ACK}'")
    print(f"[AGENT] Hostname: {socket.gethostname()}")
    print("=" * 45)

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(broker, BROKER_PORT, 60)
            client.loop_forever()
        except Exception as e:
            print(f"[AGENT] Reconnect in 5s: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
