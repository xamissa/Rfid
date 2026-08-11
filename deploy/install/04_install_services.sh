#!/bin/bash

# Install RFID Bridge systemd and Nginx configuration.
#
# Run as root after 03_install_application.sh.
#
# The web service will be enabled and started.
# The worker will remain disabled and stopped.

APP_DIR="/opt/rfid_bridge/app"
WEB_UNIT="rfid-bridge-web.service"
WORKER_UNIT="rfid-bridge-worker.service"
NGINX_SITE="rfid-bridge"
SERVER_NAMES="${RFID_BRIDGE_SERVER_NAMES:-$(hostname) 127.0.0.1}"

echo "===== RFID BRIDGE SERVICE INSTALLATION ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

for FILE in \
    "$APP_DIR/deploy/systemd/$WEB_UNIT" \
    "$APP_DIR/deploy/systemd/$WORKER_UNIT" \
    "$APP_DIR/deploy/nginx/$NGINX_SITE"
do
    if [ ! -f "$FILE" ]; then
        echo "FAIL: Missing required file: $FILE"
        exit 1
    fi
done

echo
echo "===== 1. INSTALL SYSTEMD UNITS ====="

install \
    -o root \
    -g root \
    -m 0644 \
    "$APP_DIR/deploy/systemd/$WEB_UNIT" \
    "/etc/systemd/system/$WEB_UNIT"

install \
    -o root \
    -g root \
    -m 0644 \
    "$APP_DIR/deploy/systemd/$WORKER_UNIT" \
    "/etc/systemd/system/$WORKER_UNIT"

systemctl daemon-reload

systemd-analyze verify \
    "/etc/systemd/system/$WEB_UNIT" \
    "/etc/systemd/system/$WORKER_UNIT"

echo
echo "===== 2. INSTALL NGINX SITE ====="

sed \
    "s/^[[:space:]]*server_name .*/    server_name $SERVER_NAMES;/" \
    "$APP_DIR/deploy/nginx/$NGINX_SITE" \
    > "/etc/nginx/sites-available/$NGINX_SITE"

chown root:root "/etc/nginx/sites-available/$NGINX_SITE"
chmod 0644 "/etc/nginx/sites-available/$NGINX_SITE"

echo "NGINX_SERVER_NAMES=$SERVER_NAMES"

ln -sfn \
    "/etc/nginx/sites-available/$NGINX_SITE" \
    "/etc/nginx/sites-enabled/$NGINX_SITE"

rm -f /etc/nginx/sites-enabled/default

nginx -t

echo
echo "===== 3. SERVICE ACTIVATION ====="

systemctl disable --now "$WORKER_UNIT" 2>/dev/null || true

systemctl enable "$WEB_UNIT"
systemctl restart "$WEB_UNIT"

systemctl enable nginx
systemctl reload nginx

echo
echo "===== WAIT FOR WEB SOCKET ====="

SOCKET_READY=false

for ATTEMPT in 1 2 3 4 5 6 7 8 9 10
do
    if [ -S /run/rfid_bridge/web.sock ]; then
        SOCKET_READY=true
        echo "WEB_SOCKET_READY_ATTEMPT=$ATTEMPT"
        break
    fi

    sleep 1
done

if [ "$SOCKET_READY" != "true" ]; then
    echo "FAIL: Gunicorn socket was not ready after 10 seconds."
    exit 1
fi

echo
echo "===== 4. VERIFICATION ====="

echo "WEB_ACTIVE=$(systemctl is-active "$WEB_UNIT")"
echo "WEB_ENABLED=$(systemctl is-enabled "$WEB_UNIT")"
echo "WORKER_ACTIVE=$(systemctl is-active "$WORKER_UNIT")"
echo "WORKER_ENABLED=$(systemctl is-enabled "$WORKER_UNIT")"
echo "NGINX_ACTIVE=$(systemctl is-active nginx)"
echo "NGINX_ENABLED=$(systemctl is-enabled nginx)"

curl \
    --silent \
    --show-error \
    --output /dev/null \
    --write-out='HTTP_CODE=%{http_code}\n' \
    http://127.0.0.1/

echo
echo "PASS: Web and Nginx services are installed."
echo "PASS: Worker remains disabled and stopped."
