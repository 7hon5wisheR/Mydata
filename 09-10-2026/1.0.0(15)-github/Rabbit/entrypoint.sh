#!/bin/bash
set -e

SSL_DIR=/etc/rabbitmq/ssl
CONF_FILE=/etc/rabbitmq/rabbitmq.conf

echo "[BOOT] Preparing local certs..."

if [[ ! -f "$SSL_DIR/cacert.pem" || ! -f "$SSL_DIR/cert.pem" || ! -f "$SSL_DIR/key.pem" || ! -f "$CONF_FILE" ]]; then
  echo "[FATAL] Missing required cert or config file in $SSL_DIR"
  ls -l "$SSL_DIR" || true
  ls -l /etc/rabbitmq || true
  exit 1
fi

# Set permissions
chown -R rabbitmq:rabbitmq "$SSL_DIR"
chmod 640 "$SSL_DIR"/*.pem
chmod 600 "$SSL_DIR/key.pem"
chmod 644 "$CONF_FILE"


LAST_CERT_HASH=$(sha256sum "$SSL_DIR/cert.pem" | awk '{print $1}')
LAST_KEY_HASH=$(sha256sum "$SSL_DIR/key.pem" | awk '{print $1}')
LAST_CACERT_HASH=$(sha256sum "$SSL_DIR/cacert.pem" | awk '{print $1}')


watch_cert_update() {
  echo "[WATCH] Watching for cert updates..."
  while true; do
    sleep 60
    NEW_CERT_HASH=$(sha256sum "$SSL_DIR/cert.pem" | awk '{print $1}')
    NEW_KEY_HASH=$(sha256sum "$SSL_DIR/key.pem" | awk '{print $1}')
    NEW_CACERT_HASH=$(sha256sum "$SSL_DIR/cacert.pem" | awk '{print $1}')

    if [[ "$NEW_CERT_HASH" != "$LAST_CERT_HASH" ]] || \
       [[ "$NEW_KEY_HASH" != "$LAST_KEY_HASH" ]] || \
       [[ "$NEW_CACERT_HASH" != "$LAST_CACERT_HASH" ]]; then
       
      echo "[INFO] Detected local cert change — reloading RabbitMQ SSL cache..."

      chown rabbitmq:rabbitmq "$SSL_DIR"/*.pem
      chmod 640 "$SSL_DIR"/*.pem
      chmod 600 "$SSL_DIR/key.pem"

 
      LAST_CERT_HASH=$NEW_CERT_HASH
      LAST_KEY_HASH=$NEW_KEY_HASH
      LAST_CACERT_HASH=$NEW_CACERT_HASH


      rabbitmqctl eval 'ssl:clear_pem_cache().' || echo "[WARN] Failed to clear SSL cache"
      echo "[INFO] SSL cache reloaded successfully."
    fi
  done
}


watch_cert_update &

echo "[BOOT] Starting RabbitMQ now..."
exec docker-entrypoint.sh "$@"
