#!/bin/bash

# Install the RFID Bridge Python application.
#
# Run as root from the checked-out repository:
#
#   sudo bash deploy/install/03_install_application.sh
#
# Required before running:
# - 01_prepare_system.sh completed
# - 02_configure_postgresql.sh completed
# - deploy/examples/app.env.example reviewed for the target Pi
# - RFID_BRIDGE_DB_PASSWORD supplied
#
# This script does not start or enable the web or worker services.

SOURCE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_ROOT="/opt/rfid_bridge"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
CONFIG_DIR="/etc/rfid_bridge"
DATA_DIR="/var/lib/rfid_bridge"
SERVICE_USER="rfidbridge"
SERVICE_GROUP="rfidbridge"

DB_PASSWORD="${RFID_BRIDGE_DB_PASSWORD:-}"
DJANGO_SECRET="${RFID_BRIDGE_DJANGO_SECRET:-}"
ALLOWED_HOSTS="${RFID_BRIDGE_ALLOWED_HOSTS:-127.0.0.1,localhost}"
TIME_ZONE="${RFID_BRIDGE_TIME_ZONE:-Africa/Johannesburg}"

echo "===== RFID BRIDGE APPLICATION INSTALLATION ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

if [ -z "$DB_PASSWORD" ]; then
    echo "FAIL: RFID_BRIDGE_DB_PASSWORD is required."
    exit 1
fi

if [ -z "$DJANGO_SECRET" ]; then
    echo "FAIL: RFID_BRIDGE_DJANGO_SECRET is required."
    exit 1
fi

if [ ! -f "$SOURCE_DIR/manage.py" ]; then
    echo "FAIL: manage.py was not found in $SOURCE_DIR."
    exit 1
fi

echo
echo "===== 1. COPY APPLICATION SOURCE ====="

mkdir -p "$APP_DIR"

SOURCE_REAL="$(readlink -f "$SOURCE_DIR")"
APP_REAL="$(readlink -f "$APP_DIR")"

if [ "$SOURCE_REAL" = "$APP_REAL" ]; then
    echo "SOURCE_COPY=skipped_already_in_canonical_path"
else
    find "$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

    tar \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='deploy/evidence' \
        -C "$SOURCE_DIR" \
        -cf - . \
        | tar -C "$APP_DIR" -xf -

    echo "SOURCE_COPY=completed"
fi

chown -R root:"$SERVICE_GROUP" "$APP_ROOT"
chmod 2750 "$APP_ROOT" "$APP_DIR"

find "$APP_DIR" -type d -exec chmod 2750 {} \;
find "$APP_DIR" -type f -exec chmod 0640 {} \;
find "$APP_DIR/deploy/install" -type f -name '*.sh' -exec chmod 0750 {} \;

echo
echo "===== 2. PYTHON VIRTUAL ENVIRONMENT ====="

rm -rf "$VENV_DIR"
python3 -m venv "$VENV_DIR"

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install \
    --requirement "$APP_DIR/requirements.txt"

ODOO_ENCRYPTION_KEY="$(
    "$VENV_DIR/bin/python" - <<'PY_KEY'
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode("ascii"))
PY_KEY
)"

chown -R root:"$SERVICE_GROUP" "$VENV_DIR"
chmod 2750 "$VENV_DIR"

echo
echo "===== 3. CONFIGURATION FILES ====="

install -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$CONFIG_DIR"

cat > "$CONFIG_DIR/app.env" <<EOF_APP
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$ALLOWED_HOSTS
DJANGO_TIME_ZONE=$TIME_ZONE
POSTGRES_DB=rfid_bridge
POSTGRES_USER=rfid_bridge_app
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false

# Final RFID runtime starts with non-live placeholder values.
# Replace these deliberately during commissioning or restore them
# from a protected disaster-recovery configuration set.
RFID_ODOO_BASE_URL=https://example.invalid
RFID_GATEWAY_CODE=RFID-GW-01
RFID_READER_CODE=receiving-door-01
RFID_CONTROL_POLL_SECONDS=1.0
RFID_ODOO_REQUEST_TIMEOUT_SECONDS=10
RFID_ODOO_VERIFY_TLS=true
EOF_APP

cat > "$CONFIG_DIR/secrets.env" <<EOF_SECRETS
DJANGO_SECRET_KEY=$DJANGO_SECRET
POSTGRES_PASSWORD=$DB_PASSWORD
ODOO_CREDENTIAL_ENCRYPTION_KEY=$ODOO_ENCRYPTION_KEY

# Non-live placeholder. Replace only during commissioning or
# restore the matching protected value from a DR backup set.
RFID_ODOO_BEARER_TOKEN=REPLACE_WITH_ODOO_RFID_API_BEARER_TOKEN
EOF_SECRETS

chown root:"$SERVICE_GROUP" \
    "$CONFIG_DIR/app.env" \
    "$CONFIG_DIR/secrets.env"

chmod 0640 \
    "$CONFIG_DIR/app.env" \
    "$CONFIG_DIR/secrets.env"

echo
echo "===== 4. DJANGO PRE-DEPLOYMENT CHECKS ====="

runuser -u "$SERVICE_USER" -- \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" check

runuser -u "$SERVICE_USER" -- \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" migrate \
    --noinput

runuser -u "$SERVICE_USER" -- \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" create_initial_admin

runuser -u "$SERVICE_USER" -- \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" collectstatic \
    --noinput

echo
echo "===== 5. FINAL VERIFICATION ====="

runuser -u "$SERVICE_USER" -- \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" \
    makemigrations --check --dry-run

stat -c \
    'PATH=%n OWNER=%U GROUP=%G MODE=%a TYPE=%F' \
    "$APP_DIR" \
    "$VENV_DIR" \
    "$CONFIG_DIR" \
    "$CONFIG_DIR/app.env" \
    "$CONFIG_DIR/secrets.env" \
    "$DATA_DIR/staticfiles"

echo
echo "PASS: Application installation completed."
echo "PASS: Fail-closed configuration is installed."
echo "HOLD: Services are not installed, started or enabled yet."
