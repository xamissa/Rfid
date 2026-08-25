#!/bin/bash

set -Eeuo pipefail

DB_NAME="rfid_bridge"
DB_USER="rfid_bridge_app"
CONFIG_DIR="/etc/rfid_bridge"

WEB_SERVICE="rfid-bridge-web.service"
LEGACY_WORKER="rfid-bridge-worker.service"
FINAL_WORKER="rfid-final-worker.service"
FINAL_DELIVERY="rfid-final-delivery.service"
DISPATCH_WORKER="rfid-final-dispatch-worker.service"
DISPATCH_DELIVERY="rfid-final-dispatch-delivery.service"

BACKUP_DIR="${1:-}"
CONFIRM="${CONFIRM_EXACT_RESTORE:-NO}"

echo "===== RFID BRIDGE EXACT DISASTER RECOVERY RESTORE ====="
echo "DATE=$(date -Is)"
echo "DATABASE=$DB_NAME"
echo "BACKUP_DIR=${BACKUP_DIR:-NOT_SUPPLIED}"
echo

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

if [ -z "$BACKUP_DIR" ]; then
    echo "FAIL: Supply the exact backup directory as argument 1."
    echo
    echo "Example:"
    echo "sudo CONFIRM_EXACT_RESTORE=YES \\"
    echo "  bash deploy/recovery/restore_exact_backup.sh \\"
    echo "  /var/lib/rfid_bridge/backups/exact_HOST_TIMESTAMP"
    exit 1
fi

BACKUP_DIR="$(readlink -f "$BACKUP_DIR")"

if [ "$CONFIRM" != "YES" ]; then
    echo "FAIL: Exact restore is destructive."
    echo "Set CONFIRM_EXACT_RESTORE=YES explicitly."
    exit 1
fi

echo "===== REQUIRED BACKUP CONTENT ====="

for FILE in \
    "$BACKUP_DIR/rfid_bridge.dump" \
    "$BACKUP_DIR/config/app.env" \
    "$BACKUP_DIR/config/secrets.env" \
    "$BACKUP_DIR/MANIFEST.txt" \
    "$BACKUP_DIR/SHA256SUMS"
do
    if [ ! -f "$FILE" ]; then
        echo "FAIL: Missing required recovery file: $FILE"
        exit 1
    fi

    echo "FOUND=$FILE"
done

echo
echo "===== MANIFEST VALIDATION ====="

grep -qx 'RFID_DR_FORMAT=1' \
    "$BACKUP_DIR/MANIFEST.txt" || {
        echo "FAIL: Unsupported or missing RFID_DR_FORMAT."
        exit 1
    }

grep -qx 'DATABASE=rfid_bridge' \
    "$BACKUP_DIR/MANIFEST.txt" || {
        echo "FAIL: Backup database is not rfid_bridge."
        exit 1
    }

echo "MANIFEST=PASS"

echo
echo "===== CHECKSUM VALIDATION ====="

(
    cd "$BACKUP_DIR"
    sha256sum -c SHA256SUMS
)

echo "CHECKSUMS=PASS"

echo
echo "===== CONFIG PASSWORD CONSISTENCY ====="

DB_PASSWORD="$(
    awk -F= '
        $1 == "POSTGRES_PASSWORD" {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$BACKUP_DIR/config/secrets.env"
)"

if [ -z "$DB_PASSWORD" ]; then
    echo "FAIL: POSTGRES_PASSWORD missing from backed-up secrets.env."
    exit 1
fi

echo "POSTGRES_PASSWORD_PRESENT=YES"

echo
echo "===== STOP APPLICATION SERVICES ====="

systemctl stop "$FINAL_WORKER" 2>/dev/null || true
systemctl stop "$FINAL_DELIVERY" 2>/dev/null || true
systemctl stop "$DISPATCH_WORKER" 2>/dev/null || true
systemctl stop "$DISPATCH_DELIVERY" 2>/dev/null || true
systemctl stop "$LEGACY_WORKER" 2>/dev/null || true
systemctl stop "$WEB_SERVICE" 2>/dev/null || true

echo "APPLICATION_SERVICES_STOPPED=YES"

echo
echo "===== RECREATE APPLICATION DATABASE ====="

runuser -u postgres -- \
    dropdb \
    --if-exists \
    "$DB_NAME"

runuser -u postgres -- \
    createdb \
    --owner="$DB_USER" \
    --encoding=UTF8 \
    --template=template0 \
    "$DB_NAME"

echo "DATABASE_RECREATED=YES"

echo
echo "===== RESTORE DATABASE ====="

runuser -u postgres -- \
    pg_restore \
    --exit-on-error \
    --no-owner \
    --no-privileges \
    --dbname="$DB_NAME" \
    "$BACKUP_DIR/rfid_bridge.dump"

echo "DATABASE_RESTORE=PASS"

echo
echo "===== RESTORE DATABASE ROLE PASSWORD ====="

DB_PASSWORD_SQL="$(
    printf '%s' "$DB_PASSWORD" | sed "s/'/''/g"
)"

printf '%s\n' "
ALTER ROLE \"$DB_USER\"
WITH PASSWORD '$DB_PASSWORD_SQL';
" | runuser -u postgres -- \
    psql \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1

unset DB_PASSWORD
unset DB_PASSWORD_SQL

echo "DATABASE_ROLE_PASSWORD_RESTORED=YES"

echo
echo "===== RESTORE MATCHING PROTECTED CONFIG ====="

install -d \
    -o root \
    -g rfidbridge \
    -m 0750 \
    "$CONFIG_DIR"

install \
    -o root \
    -g rfidbridge \
    -m 0640 \
    "$BACKUP_DIR/config/app.env" \
    "$CONFIG_DIR/app.env"

install \
    -o root \
    -g rfidbridge \
    -m 0640 \
    "$BACKUP_DIR/config/secrets.env" \
    "$CONFIG_DIR/secrets.env"

echo "PROTECTED_CONFIG_RESTORE=PASS"

echo
echo "===== DJANGO VALIDATION ====="

runuser -u rfidbridge -- \
    /opt/rfid_bridge/venv/bin/python \
    /opt/rfid_bridge/app/manage.py \
    check

runuser -u rfidbridge -- \
    /opt/rfid_bridge/venv/bin/python \
    /opt/rfid_bridge/app/manage.py \
    showmigrations bridge_core

echo "DJANGO_VALIDATION=PASS"

echo
echo "===== SAFE POST-RESTORE SERVICE STATE ====="

systemctl disable "$LEGACY_WORKER" 2>/dev/null || true
systemctl disable "$FINAL_WORKER" 2>/dev/null || true
systemctl disable "$FINAL_DELIVERY" 2>/dev/null || true
systemctl disable "$DISPATCH_WORKER" 2>/dev/null || true
systemctl disable "$DISPATCH_DELIVERY" 2>/dev/null || true

echo "LEGACY_WORKER_ACTIVE=$(systemctl is-active "$LEGACY_WORKER" 2>/dev/null || true)"
echo "FINAL_WORKER_ACTIVE=$(systemctl is-active "$FINAL_WORKER" 2>/dev/null || true)"
echo "FINAL_DELIVERY_ACTIVE=$(systemctl is-active "$FINAL_DELIVERY" 2>/dev/null || true)"
echo "DISPATCH_WORKER_ACTIVE=$(systemctl is-active "$DISPATCH_WORKER" 2>/dev/null || true)"
echo "DISPATCH_DELIVERY_ACTIVE=$(systemctl is-active "$DISPATCH_DELIVERY" 2>/dev/null || true)"

echo
echo "IMPORTANT:"
echo "The database and protected configuration are restored."
echo "Worker services remain stopped."
echo "Do not start RFID workers until reader/Odoo connectivity has been verified."
echo
echo "RFID_EXACT_DR_RESTORE=PASS"
