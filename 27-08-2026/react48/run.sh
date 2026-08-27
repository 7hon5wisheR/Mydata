#!/bin/bash
IMAGE_NAME=clientstatus
CONTAINER_NAME=clientstatus
HOST_IP=192.168.137.1
NUMBER_OF_CABINETS=2

# --- Data folder on the HOST (survives docker rm / rebuild) ---
# broker-config.json and registry-data.json live here, on the host,
# NOT inside the container's own filesystem. Editing broker-config.json
# here (even while the container is running) takes effect immediately,
# because it's bind-mounted into the container below -- it's the SAME
# file, not a copy.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"

# Install Docker if not installed
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker not found. Installing Docker..."
    sudo apt update
    sudo apt install -y docker.io
    sudo systemctl enable docker
    sudo systemctl start docker
fi

# Verify Docker is available
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker installation failed."
    exit 1
fi

# --- Make sure the host data folder + seed files exist ---
# (only created the FIRST time you run this; won't overwrite an
# existing broker-config.json / registry-data.json)
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/broker-config.json" ]; then
  echo '{"ip": "20.81.43.213"}' > "$DATA_DIR/broker-config.json"
  echo "Created default $DATA_DIR/broker-config.json"
fi
if [ ! -f "$DATA_DIR/registry-data.json" ]; then
  echo '{}' > "$DATA_DIR/registry-data.json"
  echo "Created empty $DATA_DIR/registry-data.json"
fi

docker rm -f "$CONTAINER_NAME" 2>/dev/null
docker build -t "$IMAGE_NAME" . || exit 1
docker run -d \
  --name "$CONTAINER_NAME" \
  -p 3000:3000 \
  -p 3001:3001 \
  -e HOST_IP="$HOST_IP" \
  -e NUMBER_OF_CABINETS="$NUMBER_OF_CABINETS" \
  -v "$DATA_DIR/broker-config.json:/app/broker-config.json" \
  -v "$DATA_DIR/registry-data.json:/app/registry-data.json" \
  "$IMAGE_NAME"
docker update --restart unless-stopped "$CONTAINER_NAME"

echo "Application started successfully."
echo ""
echo "Broker config file: $DATA_DIR/broker-config.json"
echo "To change the MQTT broker IP without restarting/rebuilding anything,"
echo "just edit and save that file, or use the (gear) button in the UI."
