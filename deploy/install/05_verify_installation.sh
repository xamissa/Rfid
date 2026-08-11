#!/bin/bash

# Read-only verification of an RFID Bridge installation.
#
# Run as root after installation:
#
#   sudo bash deploy/install/05_verify_installation.sh
#
# This script does not:
# - start, stop, restart or enable services
# - modify PostgreSQL
# - contact RFID hardware
# - contact Odoo

APP_DIR="/opt/rfid_bridge/app"
PYTHON="/opt/rfid_bridge/venv/bin/python"
MANAGE="$APP_DIR/manage.py"

FAIL=0
HOLD=0

echo "===== RFID BRIDGE INSTALLATION VERIFICATION ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

echo
echo "===== 1. REQUIRED PATHS ====="

for PATH_ITEM in \
    /opt/rfid_bridge \
    /opt/rfid_bridge/app \
    /opt/rfid_bridge/venv \
    /etc/rfid_bridge \
    /etc/rfid_bridge/app.env \
    /etc/rfid_bridge/secrets.env \
    /var/lib/rfid_bridge \
    /var/lib/rfid_bridge/staticfiles \
    /var/log/rfid_bridge \
    /etc/systemd/system/rfid-bridge-web.service \
    /etc/systemd/system/rfid-bridge-worker.service \
    /etc/nginx/sites-available/rfid-bridge \
    /etc/nginx/sites-enabled/rfid-bridge
do
    if [ -e "$PATH_ITEM" ]; then
        stat -c \
            'PATH=%n OWNER=%U GROUP=%G MODE=%a TYPE=%F' \
            "$PATH_ITEM"
    else
        echo "FAIL: Missing required path: $PATH_ITEM"
        FAIL=1
    fi
done

echo
echo "===== 2. SERVICE ACCOUNT ====="

getent passwd rfidbridge
PASSWD_RC=$?

getent group rfidbridge
GROUP_RC=$?

id rfidbridge
ID_RC=$?

echo "PASSWD_RC=$PASSWD_RC"
echo "GROUP_RC=$GROUP_RC"
echo "ID_RC=$ID_RC"

if [ "$PASSWD_RC" -ne 0 ] || \
   [ "$GROUP_RC" -ne 0 ] || \
   [ "$ID_RC" -ne 0 ]; then
    FAIL=1
fi

echo
echo "===== 3. POSTGRESQL CONTRACT ====="

runuser -u postgres -- psql \
    --no-psqlrc \
    --tuples-only \
    --no-align \
    --command="
        SELECT
            datname,
            pg_catalog.pg_get_userbyid(datdba),
            datallowconn
        FROM pg_database
        WHERE datname = 'rfid_bridge';

        SELECT
            rolname,
            rolcanlogin,
            rolcreatedb,
            rolcreaterole,
            rolsuper
        FROM pg_roles
        WHERE rolname = 'rfid_bridge_app';
    "
POSTGRES_RC=$?

echo "POSTGRES_RC=$POSTGRES_RC"

if [ "$POSTGRES_RC" -ne 0 ]; then
    FAIL=1
fi

echo
echo "===== 4. DJANGO CHECKS ====="

runuser -u rfidbridge -- \
    "$PYTHON" "$MANAGE" check
DJANGO_CHECK_RC=$?

runuser -u rfidbridge -- \
    "$PYTHON" "$MANAGE" makemigrations \
    --check \
    --dry-run
MIGRATION_DRIFT_RC=$?

runuser -u rfidbridge -- \
    "$PYTHON" "$MANAGE" showmigrations \
    --plan
SHOW_MIGRATIONS_RC=$?

echo "DJANGO_CHECK_RC=$DJANGO_CHECK_RC"
echo "MIGRATION_DRIFT_RC=$MIGRATION_DRIFT_RC"
echo "SHOW_MIGRATIONS_RC=$SHOW_MIGRATIONS_RC"

if [ "$DJANGO_CHECK_RC" -ne 0 ] || \
   [ "$MIGRATION_DRIFT_RC" -ne 0 ] || \
   [ "$SHOW_MIGRATIONS_RC" -ne 0 ]; then
    FAIL=1
fi

echo
echo "===== 5. FAIL-CLOSED APPLICATION BASELINE ====="

runuser -u rfidbridge -- \
    "$PYTHON" "$MANAGE" shell <<'PY'
from cryptography.fernet import Fernet
from django.conf import settings

from bridge_core.models import OperationalConfiguration

print(f"READER_BACKEND={settings.READER_BACKEND}")
print(f"SENDER_BACKEND={settings.SENDER_BACKEND}")
print(
    "ALLOW_PHYSICAL_READER_CONTACT="
    f"{settings.ALLOW_PHYSICAL_READER_CONTACT}"
)
print(f"ALLOW_ODOO_CONTACT={settings.ALLOW_ODOO_CONTACT}")

cipher = Fernet(
    settings.ODOO_CREDENTIAL_ENCRYPTION_KEY.encode("ascii")
)
plaintext = b"rfid-bridge-install-verifier"

if cipher.decrypt(cipher.encrypt(plaintext)) != plaintext:
    raise RuntimeError(
        "Odoo credential encryption key is invalid."
    )

print("ODOO_CREDENTIAL_ENCRYPTION_KEY=valid")
print(
    "OPERATIONAL_CONFIGURATION_COUNT="
    f"{OperationalConfiguration.objects.count()}"
)

if settings.READER_BACKEND != "fake":
    raise RuntimeError("Reader backend is not fail-closed.")

if settings.SENDER_BACKEND != "disabled":
    raise RuntimeError("Sender backend is not fail-closed.")

if settings.ALLOW_PHYSICAL_READER_CONTACT:
    raise RuntimeError("Physical reader contact is enabled.")

if settings.ALLOW_ODOO_CONTACT:
    raise RuntimeError("Odoo contact is enabled.")

if OperationalConfiguration.objects.count() != 1:
    raise RuntimeError(
        "Expected exactly one operational configuration record."
    )
PY
SAFETY_RC=$?

echo "SAFETY_RC=$SAFETY_RC"

if [ "$SAFETY_RC" -ne 0 ]; then
    FAIL=1
fi

echo
echo "===== 6. SYSTEMD AND NGINX ====="

systemd-analyze verify \
    /etc/systemd/system/rfid-bridge-web.service \
    /etc/systemd/system/rfid-bridge-worker.service
SYSTEMD_VERIFY_RC=$?

nginx -t
NGINX_RC=$?

echo "SYSTEMD_VERIFY_RC=$SYSTEMD_VERIFY_RC"
echo "NGINX_RC=$NGINX_RC"

if [ "$SYSTEMD_VERIFY_RC" -ne 0 ] || \
   [ "$NGINX_RC" -ne 0 ]; then
    FAIL=1
fi

echo
echo "===== 7. SERVICE STATE ====="

WEB_ACTIVE="$(systemctl is-active rfid-bridge-web.service)"
WEB_ENABLED="$(systemctl is-enabled rfid-bridge-web.service)"
WORKER_ACTIVE="$(systemctl is-active rfid-bridge-worker.service)"
WORKER_ENABLED="$(systemctl is-enabled rfid-bridge-worker.service)"
NGINX_ACTIVE="$(systemctl is-active nginx)"
NGINX_ENABLED="$(systemctl is-enabled nginx)"

echo "WEB_ACTIVE=$WEB_ACTIVE"
echo "WEB_ENABLED=$WEB_ENABLED"
echo "WORKER_ACTIVE=$WORKER_ACTIVE"
echo "WORKER_ENABLED=$WORKER_ENABLED"
echo "NGINX_ACTIVE=$NGINX_ACTIVE"
echo "NGINX_ENABLED=$NGINX_ENABLED"

if [ "$WEB_ACTIVE" != "active" ] || \
   [ "$WEB_ENABLED" != "enabled" ] || \
   [ "$NGINX_ACTIVE" != "active" ] || \
   [ "$NGINX_ENABLED" != "enabled" ]; then
    FAIL=1
fi

if [ "$WORKER_ACTIVE" != "inactive" ] || \
   [ "$WORKER_ENABLED" != "disabled" ]; then
    FAIL=1
fi

echo
echo "===== 8. LOCAL HTTP CHECK ====="

HTTP_CODE="$(
    curl \
        --silent \
        --show-error \
        --output /dev/null \
        --write-out='%{http_code}' \
        http://127.0.0.1/
)"
CURL_RC=$?

echo "HTTP_CODE=$HTTP_CODE"
echo "CURL_RC=$CURL_RC"

if [ "$CURL_RC" -ne 0 ]; then
    FAIL=1
elif [ "$HTTP_CODE" != "200" ] && \
     [ "$HTTP_CODE" != "302" ]; then
    FAIL=1
fi

echo
echo "===== CONCLUSION ====="

if [ "$FAIL" -eq 0 ]; then
    echo "PASS: RFID Bridge installation is healthy."
    echo "PASS: Web and Nginx are operational."
    echo "PASS: Worker remains disabled and inactive."
    echo "PASS: Reader and Odoo contact remain blocked."
    echo "HOLD: Real integration testing still requires approval."
else
    echo "FAIL: RFID Bridge installation verification failed."
    exit 1
fi
