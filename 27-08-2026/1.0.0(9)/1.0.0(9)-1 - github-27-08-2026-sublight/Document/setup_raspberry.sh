#!/bin/bash
# ================================================================
#  setup_raspberry.sh
#  Full Auto Setup on Raspberry Pi - Mobile Aspects
#  Based on: "Guide to Run Service on Raspberry v1.0"
#
#  Automated steps:
#    1. Setup systemd service for send.py
#    2. Install & configure Mosquitto (mosquitto.conf)
#    3. Install nbtscan (for cabinet hostname detection)
#    4. Setup passwordless sudo for send.service control (sudoers)
#    5. Setup systemd service for dip.py (DIP Switch IP Manager)
#
#  Usage:
#    chmod +x setup_raspberry.sh
#    sudo ./setup_raspberry.sh
# ================================================================
set -e

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ================================================================
#  CONFIGURATION — Adjust before running!
# ================================================================
HOME_BASE="alice"
BROKER_DIR="/home/${HOME_BASE}/broker"
VENV_PYTHON="${BROKER_DIR}/venv/bin/python"
SERVICE_FILE="/etc/systemd/system/send.service"
MOSQUITTO_CONF="/etc/mosquitto/mosquitto.conf"
HOSTNAME_PREFIX="MASCAB01"   # final hostname = PREFIX + letter (A-J) based on last IP octet
BROKER_ADDRESS="192.168.137.1:1883"   # remote MQTT broker (Windows machine) for the bridge
BRIDGE_USERNAME="guest"
BRIDGE_PASSWORD="guest"
SYSTEMCTL_BIN="/usr/bin/systemctl"   # ganti ke /bin/systemctl jika path berbeda di sistem Anda
SUDOERS_FILE="/etc/sudoers.d/${HOME_BASE}-send-service"
DIP_SCRIPT="${BROKER_DIR}/dip.py"
DIP_SERVICE_FILE="/etc/systemd/system/dip.service"
PYTHON3_BIN="/usr/bin/python3"
IPSERVICE_SCRIPT="${BROKER_DIR}/ipservice.py"
IPSERVICE_SERVICE_FILE="/etc/systemd/system/ipservice.service"

# ================================================================
header() {
  echo ""
  echo -e "${BLUE}================================================================${NC}"
  echo -e "${BLUE}  $1${NC}"
  echo -e "${BLUE}================================================================${NC}"
}
step() { echo -e "\n${YELLOW}[STEP $1] $2${NC}"; }
ok()   { echo -e "${GREEN}  ? $1${NC}"; }
info() { echo -e "${CYAN}  ? $1${NC}"; }
err()  { echo -e "${RED}  ? $1${NC}"; }

# Check root
if [ "$EUID" -ne 0 ]; then
  err "Run this script with sudo!"
  echo "  Example: sudo ./setup_raspberry.sh"
  exit 1
fi

header "Mobile Aspects — Auto Setup Raspberry Pi v1.0"
info "HOME_BASE  = /home/${HOME_BASE}"
info "BROKER_DIR = ${BROKER_DIR}"

# ================================================================
# PART 0: AUTO HOSTNAME BASED ON IP ADDRESS
# ================================================================
header "PART 0: Auto-set Hostname based on IP (cabinet ID)"

step "0" "Detecting IP address on 192.168.137.x subnet..."
PI_IP=$(hostname -I | tr ' ' '\n' | grep '^192\.168\.137\.' | head -n1 || true)

if [ -z "${PI_IP}" ]; then
  err "No IP found in 192.168.137.x range. Skipping auto-hostname."
  info "Hostname remains: $(hostname)"
else
  LAST_OCTET=$(echo "${PI_IP}" | awk -F. '{print $4}')

  if [ "${LAST_OCTET}" -ge 101 ] && [ "${LAST_OCTET}" -le 110 ]; then
    OFFSET=$((LAST_OCTET - 100))
    LETTER=$(printf "\\$(printf '%03o' $((64 + OFFSET)))")
    NEW_HOSTNAME="${HOSTNAME_PREFIX}${LETTER}"

    info "Detected IP    : ${PI_IP}"
    info "Last octet     : ${LAST_OCTET}  ->  Letter: ${LETTER}"
    info "Target hostname: ${NEW_HOSTNAME}"

    CURRENT_HOSTNAME=$(hostname)
    if [ "${CURRENT_HOSTNAME}" != "${NEW_HOSTNAME}" ]; then
      hostnamectl set-hostname "${NEW_HOSTNAME}"

      if grep -q "^127\.0\.1\.1" /etc/hosts; then
        sed -i -E "s/^(127\.0\.1\.1[[:space:]]+).*/\1${NEW_HOSTNAME}/" /etc/hosts
      else
        echo -e "127.0.1.1\t${NEW_HOSTNAME}" >> /etc/hosts
      fi

      ok "Hostname updated: ${CURRENT_HOSTNAME} -> ${NEW_HOSTNAME}"
    else
      ok "Hostname already correct (${CURRENT_HOSTNAME})"
    fi
  else
    err "Last octet ${LAST_OCTET} is outside the mapped range (101-110 -> A-J)."
    info "Skipping auto-hostname, current hostname kept: $(hostname)"
  fi
fi

# Always capture the current hostname (changed or not) for later use (e.g. bridge.conf)
FINAL_HOSTNAME="$(hostname)"

# ================================================================
# PART 1: SYSTEMD SERVICE for send.py
# ================================================================
header "PART 1: Setup systemd Service (send.service)"

step "1" "Creating ${SERVICE_FILE}..."
cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=Send Flask App
After=network-online.target mosquitto.service
Wants=network-online.target mosquitto.service

[Service]
Type=simple
User=${HOME_BASE}
WorkingDirectory=${BROKER_DIR}
ExecStart=/bin/bash -c "sleep 10 && ${VENV_PYTHON} ${BROKER_DIR}/send.py"
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
StartLimitIntervalSec=0
TimeoutStartSec=0
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "send.service created successfully"

step "2" "Verifying service file..."
ls -l "${SERVICE_FILE}"
ok "Service file detected"

step "3" "Enable Python Access in Your Virtual Environment..."
echo "  ? ls -l ${VENV_PYTHON}"
ls -l "${VENV_PYTHON}" || true
if [ -f "${VENV_PYTHON}" ]; then
  chmod +x "${VENV_PYTHON}"
  ok "chmod +x ${VENV_PYTHON}"
  echo "  ? Verify (check valid):"
  ls -l "${BROKER_DIR}/venv/bin/python3" || true
  echo "  ? Python version:"
  "${VENV_PYTHON}" --version 2>&1 || true
else
  info "venv not found at ${VENV_PYTHON}"
  info "Create it first with: python3 -m venv ${BROKER_DIR}/venv"
fi

step "4" "Reloading systemd daemon..."
systemctl daemon-reload
ok "daemon-reload complete"

step "5" "Enable send.service (auto-start on boot)..."
systemctl enable send.service
ok "send.service enabled"

step "6" "Starting send.service..."
if [ -f "${BROKER_DIR}/send.py" ] && [ -f "${VENV_PYTHON}" ]; then
  systemctl start send.service
  sleep 3
  if systemctl is-active --quiet send.service; then
    ok "send.service is ACTIVE and running!"
  else
    err "send.service failed to start. Check: journalctl -u send.service -n 30"
  fi
else
  info "send.py or venv not available — service registered but not started"
  info "Start manually later: sudo systemctl start send.service"
fi

# ================================================================
# PART 2: MOSQUITTO CONFIG
# ================================================================
header "PART 2: Mosquitto Configuration"

step "7" "Checking current Mosquitto status..."
systemctl status mosquitto --no-pager || true

step "8" "Running apt update..."
apt update -y
ok "apt update complete"

step "9" "Installing mosquitto & mosquitto-clients..."
apt install -y mosquitto mosquitto-clients
ok "Mosquitto installed"

step "10" "Checking Mosquitto status after install..."
systemctl status mosquitto --no-pager || true

step "11" "Creating conf.d directory and bridge config..."
mkdir -p "${BROKER_DIR}/conf.d"
ok "Directory ${BROKER_DIR}/conf.d ready"

BRIDGE_CONF="${BROKER_DIR}/conf.d/bridge.conf"
if [ -f "${BRIDGE_CONF}" ]; then
  cp "${BRIDGE_CONF}" "${BRIDGE_CONF}.bak_$(date +%Y%m%d_%H%M%S)"
  info "Old bridge.conf backed up"
fi

cat > "${BRIDGE_CONF}" << BRIDGEEOF
connection ${FINAL_HOSTNAME}
address ${BROKER_ADDRESS}
clientid ${FINAL_HOSTNAME}
topic ${FINAL_HOSTNAME}/# both 0
topic in in 0
topic out out 0
topic config both 0
topic session both 0
topic reboot both 0
remote_username ${BRIDGE_USERNAME}
remote_password ${BRIDGE_PASSWORD}
try_private false
cleansession false
bridge_protocol_version mqttv311
log_type error
BRIDGEEOF

ok "bridge.conf written -> connection/clientid/topic = ${FINAL_HOSTNAME}"

step "12" "Updating /etc/mosquitto/mosquitto.conf..."
if [ -f "${MOSQUITTO_CONF}" ]; then
  BACKUP="${MOSQUITTO_CONF}.bak_$(date +%Y%m%d_%H%M%S)"
  cp "${MOSQUITTO_CONF}" "${BACKUP}"
  info "Old config backed up ? ${BACKUP}"
fi

cat > "${MOSQUITTO_CONF}" << MQTTEOF
# ======================================================
# MOSQUITTO CONFIG
# ======================================================
listener 1883 0.0.0.0
protocol mqtt
allow_anonymous true

# WebSocket listener (optional)
listener 9001 0.0.0.0
protocol websockets
allow_anonymous true

pid_file /run/mosquitto/mosquitto.pid

persistence true
persistence_location /var/lib/mosquitto/

log_dest file /var/log/mosquitto/mosquitto.log

include_dir /home/${HOME_BASE}/broker/conf.d
MQTTEOF
ok "mosquitto.conf written successfully"

step "13" "Restarting Mosquitto with new config..."
systemctl restart mosquitto
sleep 2
if systemctl is-active --quiet mosquitto; then
  ok "Mosquitto ACTIVE — port 1883 (MQTT) and 9001 (WebSocket)"
else
  err "Mosquitto failed to start! Check: journalctl -xe | grep mosquitto"
  exit 1
fi

# ================================================================
# PART 3: CRONJOB — SSH Auto-Recovery
# ================================================================
header "PART 3: Setup Cronjob SSH Auto-Recovery"

step "14" "Creating script /usr/local/bin/cron_check_ssh.sh..."
cat > /usr/local/bin/cron_check_ssh.sh << 'CRONEOF'
#!/bin/bash
if systemctl is-active --quiet ssh; then
    echo "SSH OK"
else
    echo "SSH DOWN - restarting"
    systemctl restart ssh
fi
CRONEOF
ok "cron_check_ssh.sh created successfully"

step "15" "Setting executable permission..."
chmod +x /usr/local/bin/cron_check_ssh.sh
ok "chmod +x /usr/local/bin/cron_check_ssh.sh"

step "16" "Adding cronjob (check SSH every 60 minutes)..."
CRON_ENTRY="*/60 * * * * /usr/local/bin/cron_check_ssh.sh"
(crontab -l 2>/dev/null | grep -v "cron_check_ssh"; echo "# Check SSH every 60 minutes"; echo "${CRON_ENTRY}") | crontab -
ok "Cronjob added successfully"
info "Current root crontab:"
crontab -l

# ================================================================
# PART 4: WATCHDOG — Auto-Recovery Raspberry Pi
# ================================================================
header "PART 4: Setup Watchdog Auto-Recovery"

step "17" "Running apt update..."
apt update -y
ok "apt update complete"

step "18" "Installing watchdog..."
apt install -y watchdog
ok "watchdog installed"

step "19" "Enabling kernel watchdog driver (bcm2835_wdt)..."
if ! grep -q "bcm2835_wdt" /etc/modules; then
  echo "bcm2835_wdt" | tee -a /etc/modules
  ok "bcm2835_wdt added to /etc/modules"
else
  info "bcm2835_wdt already exists in /etc/modules"
fi
modprobe bcm2835_wdt
ok "modprobe bcm2835_wdt complete"

step "20" "Verifying kernel module..."
echo "  ? lsmod | grep wdt:"
lsmod | grep wdt || info "Module not detected yet, normal if just loaded"

step "21" "Updating /etc/watchdog.conf..."
if [ -f /etc/watchdog.conf ]; then
  cp /etc/watchdog.conf /etc/watchdog.conf.bak_$(date +%Y%m%d_%H%M%S)
  info "Old watchdog.conf backup saved"
fi

cat > /etc/watchdog.conf << WDOGEOF
# ======================================================
# WATCHDOG CONFIG
# ======================================================

# Hardware watchdog device
watchdog-device    = /dev/watchdog

# CPU load limits
# Reboot if 1-min  load > 24
# Reboot if 5-min  load > 18
# Reboot if 15-min load > 12
max-load-1         = 24
max-load-5         = 18
max-load-15        = 12

# Temperature sensor (Raspberry Pi BCM2835)
# Reboot if temperature exceeds 85°C
max-temperature    = 85
temperature-sensor = /sys/devices/virtual/thermal/thermal_zone0/hwmon0/temp1_input

# Ping check DISABLED — prevents reboot on internet loss
# ping = 8.8.8.8
# ping = 1.1.1.1

# Wait 120 seconds before forcing reboot
retry-timeout      = 120
WDOGEOF

ok "watchdog.conf written successfully"
info "Applied parameters:"
echo "  watchdog-device    = /dev/watchdog"
echo "  max-load-1         = 24"
echo "  max-load-5         = 18"
echo "  max-load-15        = 12"
echo "  max-temperature    = 85"
echo "  temperature-sensor = /sys/devices/virtual/thermal/thermal_zone0/hwmon0/temp1_input"
echo "  ping               = DISABLED (will not reboot on internet loss)"
echo "  retry-timeout      = 120"

step "22" "Enabling and starting watchdog service..."
systemctl enable watchdog
systemctl start watchdog
sleep 2
if systemctl is-active --quiet watchdog; then
  ok "Watchdog ACTIVE and running!"
else
  err "Watchdog failed to start. Check: journalctl -u watchdog -n 20"
fi

# ================================================================
# PART 5: INSTALL nbtscan
# ================================================================
header "PART 5: Install nbtscan (for cabinet hostname detection)"

step "23" "Running apt update before installing nbtscan..."
apt update -y
ok "apt update complete"

step "24" "Installing nbtscan..."
apt install -y nbtscan
ok "nbtscan installed"
info "Usage: sudo nbtscan 192.168.x.0/24"

# ================================================================
# PART 6: JOURNALD LOG CONFIG
# ================================================================
header "PART 6: Log Configuration (systemd-journald)"

JOURNALD_CONF="/etc/systemd/journald.conf"

step "25" "Backing up old journald.conf..."
if [ -f "${JOURNALD_CONF}" ]; then
  cp "${JOURNALD_CONF}" "${JOURNALD_CONF}.bak_$(date +%Y%m%d_%H%M%S)"
  info "Backup saved ? ${JOURNALD_CONF}.bak_*"
fi

step "26" "Setting log parameters in ${JOURNALD_CONF}..."
set_journal_param() {
  local KEY="$1"
  local VAL="$2"
  sed -i "/^#*\s*${KEY}\s*=/d" "${JOURNALD_CONF}"
  grep -q "^\[Journal\]" "${JOURNALD_CONF}" || echo "[Journal]" >> "${JOURNALD_CONF}"
  sed -i "/^\[Journal\]/a ${KEY}=${VAL}" "${JOURNALD_CONF}"
}

set_journal_param "SystemMaxUse"      "100M"
set_journal_param "SystemMaxFileSize" "10M"
set_journal_param "MaxRetentionSec"   "7day"
set_journal_param "Compress"          "yes"

ok "journald.conf updated successfully"
info "Applied parameters:"
echo "  SystemMaxUse      = 100M"
echo "  SystemMaxFileSize = 10M"
echo "  MaxRetentionSec   = 7 days"
echo "  Compress          = yes"

step "27" "Restarting systemd-journald..."
systemctl restart systemd-journald
sleep 1
if systemctl is-active --quiet systemd-journald; then
  ok "systemd-journald ACTIVE and running!"
else
  err "systemd-journald failed to restart. Check: journalctl -xe"
fi

# ================================================================
# PART 7: PASSWORDLESS SUDO for send.service control
# ================================================================
header "PART 7: Setup Passwordless Sudo (${HOME_BASE})"

step "28" "Re-confirming send.service is enabled and started..."
systemctl daemon-reload
systemctl enable send.service
systemctl start send.service || info "send.service start skipped/failed — check manually if needed"
ok "send.service daemon-reload / enable / start re-applied"

step "29" "Locating systemctl binary..."
info "Configured path: ${SYSTEMCTL_BIN}"
if [ ! -x "${SYSTEMCTL_BIN}" ]; then
  err "${SYSTEMCTL_BIN} not found or not executable."
  info "Detected 'which systemctl':"
  which systemctl || true
  info "Update SYSTEMCTL_BIN at the top of this script to match, then re-run this part."
else
  ok "systemctl binary confirmed at ${SYSTEMCTL_BIN}"
fi

step "30" "Writing sudoers drop-in ${SUDOERS_FILE}..."
cat > "${SUDOERS_FILE}" << SUDOEOF
${HOME_BASE} ALL=(ALL) NOPASSWD: ${SYSTEMCTL_BIN} start send.service, \\
    ${SYSTEMCTL_BIN} stop send.service, \\
    ${SYSTEMCTL_BIN} restart send.service, \\
    ${SYSTEMCTL_BIN} enable send.service, \\
    ${SYSTEMCTL_BIN} disable send.service, \\
    ${SYSTEMCTL_BIN} status send.service
${HOME_BASE} ALL=(ALL) NOPASSWD: /usr/sbin/reboot -f, \\
    /usr/sbin/reboot, \\
    ${SYSTEMCTL_BIN} restart mosquitto
${HOME_BASE} ALL=(ALL) NOPASSWD: ${SYSTEMCTL_BIN} start ipservice.service, \\
    ${SYSTEMCTL_BIN} stop ipservice.service, \\
    ${SYSTEMCTL_BIN} restart ipservice.service, \\
    ${SYSTEMCTL_BIN} enable ipservice.service, \\
    ${SYSTEMCTL_BIN} disable ipservice.service, \\
    ${SYSTEMCTL_BIN} status ipservice.service, \\
    /usr/bin/journalctl -u ipservice.service *
SUDOEOF
ok "Sudoers drop-in written"

step "31" "Setting permissions (0440) on ${SUDOERS_FILE}..."
chmod 440 "${SUDOERS_FILE}"
ok "Permissions set to 440"

step "32" "Validating sudoers syntax..."
if visudo -c -f "${SUDOERS_FILE}"; then
  ok "Sudoers file syntax is valid"
else
  err "Sudoers file has a SYNTAX ERROR — removing it to avoid locking out sudo!"
  rm -f "${SUDOERS_FILE}"
  exit 1
fi

step "33" "Restarting send.service to apply everything..."
systemctl restart send.service
sleep 2
if systemctl is-active --quiet send.service; then
  ok "send.service ACTIVE after restart"
else
  err "send.service failed to restart. Check: journalctl -u send.service -n 30"
fi

# ================================================================
# PART 8: SYSTEMD SERVICE for dip.py (DIP Switch IP Manager)
# ================================================================
header "PART 8: Setup systemd Service (dip.service)"

step "34" "Verifying ${DIP_SCRIPT} exists..."
if [ -f "${DIP_SCRIPT}" ]; then
  ls -l "${DIP_SCRIPT}"
  ok "dip.py found at ${DIP_SCRIPT}"
  info "Owned by root is fine — service runs as root and is launched via python3, no execute bit needed"
else
  err "dip.py not found at ${DIP_SCRIPT}"
  info "Copy dip.py to ${BROKER_DIR} first, then re-run this part manually"
fi

step "35" "Creating ${DIP_SERVICE_FILE}..."
cat > "${DIP_SERVICE_FILE}" << EOF
[Unit]
Description=DIP Switch IP Manager
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${BROKER_DIR}
ExecStart=${PYTHON3_BIN} -u ${DIP_SCRIPT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
ok "dip.service created successfully"

step "36" "Verifying service file..."
ls -l "${DIP_SERVICE_FILE}"
ok "Service file detected"

step "37" "Reloading systemd daemon..."
systemctl daemon-reload
ok "daemon-reload complete"

step "38" "Enable dip.service (auto-start on boot)..."
systemctl enable dip.service
ok "dip.service enabled"

step "39" "Starting dip.service..."
if [ -f "${DIP_SCRIPT}" ]; then
  systemctl start dip.service
  sleep 3
  if systemctl is-active --quiet dip.service; then
    ok "dip.service is ACTIVE and running!"
  else
    err "dip.service failed to start. Check: journalctl -u dip.service -n 50"
  fi
else
  info "dip.py not available — service registered but not started"
  info "Start manually later: sudo systemctl start dip.service"
fi

info "Watch live output any time with: journalctl -u dip.service -f"
info "NOTE: dip.py currently does not wait for NetworkManager to be fully"
info "connected before checking eth0. Consider adding a short wait loop"
info "at startup (before the main while True) for more reliable boot behavior."
info "Recommended reboot test after setup: sudo reboot, then check:"
info "  sudo systemctl status dip.service"

# ================================================================
# PART 9: SYSTEMD SERVICE for ipservice.py (Auto Hostname & Bridge
#         Update on IP Change)
# ================================================================
header "PART 9: Setup systemd Service (ipservice.service)"

step "40" "Verifying ${IPSERVICE_SCRIPT} exists..."
if [ -f "${IPSERVICE_SCRIPT}" ]; then
  chmod +x "${IPSERVICE_SCRIPT}"
  ok "ipservice.py found and made executable at ${IPSERVICE_SCRIPT}"
else
  err "ipservice.py not found at ${IPSERVICE_SCRIPT}"
  info "Copy ipservice.py to ${BROKER_DIR} first, then re-run this part manually"
fi

step "41" "Creating ${IPSERVICE_SERVICE_FILE}..."
cat > "${IPSERVICE_SERVICE_FILE}" << EOF
[Unit]
Description=IP Change Monitor - Auto Update Hostname & MQTT Bridge Config
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PYTHON3_BIN} ${IPSERVICE_SCRIPT}
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "ipservice.service created successfully (no manual nano needed)"

step "42" "Verifying service file..."
ls -l "${IPSERVICE_SERVICE_FILE}"
ok "Service file detected"

step "43" "Reloading systemd daemon..."
systemctl daemon-reload
ok "daemon-reload complete"

step "44" "Enable ipservice.service (auto-start on boot)..."
systemctl enable ipservice.service
ok "ipservice.service enabled"

step "45" "Starting ipservice.service..."
if [ -f "${IPSERVICE_SCRIPT}" ]; then
  systemctl start ipservice.service
  sleep 3
  if systemctl is-active --quiet ipservice.service; then
    ok "ipservice.service is ACTIVE and running!"
  else
    err "ipservice.service failed to start. Check: journalctl -u ipservice.service -n 50"
  fi
else
  info "ipservice.py not available — service registered but not started"
  info "Start manually later: sudo systemctl start ipservice.service"
fi

info "Watch live output any time with: journalctl -u ipservice.service -f"
info "ipservice.service reboots the Pi automatically after hostname & bridge.conf"
info "are updated, so all other services (send.service, mosquitto) pick up the"
info "new hostname cleanly on the next boot."

# ================================================================
# FINAL SUMMARY
# ================================================================
header "ALL SETUP COMPLETE!"

echo -e "${GREEN}"
echo "  [0] HOSTNAME"
echo "      Prefix   : ${HOSTNAME_PREFIX}"
echo "      Current  : $(hostname)"
echo ""
echo "  [1] SEND.SERVICE"
echo "      File       : /etc/systemd/system/send.service"
echo "      Script     : ${BROKER_DIR}/send.py"
echo "      User       : ${HOME_BASE}"
echo "      Auto-start : Yes | Delay: 10 seconds"
echo ""
echo "  [2] MOSQUITTO"
echo "      MQTT Port      : 1883"
echo "      WebSocket Port : 9001"
echo "      Config : /etc/mosquitto/mosquitto.conf"
echo "      Log    : /var/log/mosquitto/mosquitto.log"
echo "      Bridge : ${BROKER_DIR}/conf.d/bridge.conf (connection=${FINAL_HOSTNAME} -> ${BROKER_ADDRESS})"
echo ""
echo "  [3] SSH CRONJOB"
echo "      Script   : /usr/local/bin/cron_check_ssh.sh"
echo "      Schedule : every 60 minutes"
echo ""
echo "  [4] WATCHDOG"
echo "      Device         : /dev/watchdog"
echo "      Ping           : DISABLED"
echo "      Max Temp       : 85°C"
echo "      Temp Sensor    : /sys/devices/virtual/thermal/thermal_zone0/hwmon0/temp1_input"
echo "      Max Load       : 24 (1m) / 18 (5m) / 12 (15m)"
echo "      Retry Timeout  : 120 seconds"
echo ""
echo "  [5] NBTSCAN  : Installed"
echo ""
echo "  [6] JOURNALD LOG"
echo "      Config   : /etc/systemd/journald.conf"
echo "      Max Use  : 100M | Max File: 10M"
echo "      Retention: 7 days | Compress: yes"
echo ""
echo "  [7] PASSWORDLESS SUDO"
echo "      File   : ${SUDOERS_FILE}"
echo "      User   : ${HOME_BASE}"
echo "      Allows : start/stop/restart/enable/disable/status send.service,"
echo "               reboot (with/without -f), restart mosquitto — no password"
echo ""
echo "  [8] DIP.SERVICE"
echo "      File       : /etc/systemd/system/dip.service"
echo "      Script     : ${DIP_SCRIPT}"
echo "      User       : root"
echo "      Auto-start : Yes | Restart=always | RestartSec=5"
echo ""
echo "  [9] IPSERVICE.SERVICE"
echo "      File       : ${IPSERVICE_SERVICE_FILE}"
echo "      Script     : ${IPSERVICE_SCRIPT}"
echo "      User       : root"
echo "      Auto-start : Yes | Restart=always | RestartSec=5"
echo "      Behavior   : detects IP change -> updates hostname & bridge.conf -> reboots Pi"
echo "      Passwordless sudo: start/stop/restart/enable/disable/status + journalctl -u ipservice.service"
echo -e "${NC}"

echo -e "${CYAN}  NEXT STEPS (manual):${NC}"
echo ""
echo "  1. Open browser and access the web config:"
echo "     http://ip.raspberry:8082"
echo ""
echo "  2. Enter Windows IP in the 'Broker Address' field"
echo "     Example: 192.168.200.139"
echo "     Default TTL: 60000 ms (1 minute)"
echo ""
echo "  3. Consider adding a wait-for-NetworkManager loop in dip.py before"
echo "     its main while True, so it doesn't check eth0 before the network"
echo "     is fully up at boot."
echo ""
echo -e "${CYAN}  USEFUL COMMANDS:${NC}"
echo ""
echo "  sudo systemctl status send.service"
echo "  sudo systemctl status mosquitto"
echo "  sudo systemctl status watchdog"
echo "  sudo systemctl status dip.service"
echo "  sudo systemctl restart send.service"
echo "  sudo systemctl stop send.service"
echo "  sudo systemctl disable send.service"
echo "  journalctl -u send.service -f        # realtime send.py log"
echo "  journalctl -u dip.service -f         # realtime dip.py log"
echo "  sudo journalctl -u ipservice.service -f   # realtime ipservice.py log (no password prompt)"
echo "  sudo systemctl status ipservice.service   # check ipservice.py status (no password prompt)"
echo "  crontab -l                           # view active cronjobs"
echo "  sudo -l -U ${HOME_BASE}              # verify passwordless sudo rules"
echo ""
echo -e "${BLUE}================================================================${NC}"
