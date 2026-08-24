#!/bin/bash

set -Eeuo pipefail

DB_NAME="rfid_bridge"
BACKUP_ROOT="/var/lib/rfid_bridge/backups"
CONFIG_DIR="/etc/rfid_bridge"
APP_DIR="/opt/rfid_bridge/app"

STAMP="$(date +%Y%m%d_%H%M%S)"
HOST="$(hostname)"
OUT="$BACKUP_ROOT/exact_${HOST}_${STAMP}"

echo "===== RFID BRIDGE EXACT DISASTER RECOVERY BACKUP ====="
echo "DATE=$(date -Is)"
echo "HOST=$HOST"
echo "DATABASE=$DB_NAME"
echo "OUTPUT=$OUT"
echo

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

for FILE in \
    "$CONFIG_DIR/app.env" \
    "$CONFIG_DIR/secrets.env"
do
    if [ ! -f "$FILE" ]; then
        echo "FAIL: Missing required recovery file: $FILE"
        exit 1
    fi
done

if [ ! -d "$APP_DIR/.git" ]; then
    echo "FAIL: RFID application Git repository not found."
    exit 1
fi

# Preserve the canonical runtime ownership of the parent backup directory.
install -d -o rfidbridge -g rfidbridge -m 0750 "$BACKUP_ROOT"

# Exact recovery sets contain database contents and live secrets.
# Each recovery set itself is root-only.
install -d -o root -g root -m 0700 "$OUT"
install -d -o root -g root -m 0700 "$OUT/config"

echo "===== DATABASE DUMP ====="

runuser -u postgres -- \
    pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    "$DB_NAME" \
    > "$OUT/rfid_bridge.dump"

chmod 0600 "$OUT/rfid_bridge.dump"

echo "DATABASE_DUMP=PASS"

echo
echo "===== MATCHING PROTECTED CONFIG ====="

install -o root -g root -m 0600 \
    "$CONFIG_DIR/app.env" \
    "$OUT/config/app.env"

install -o root -g root -m 0600 \
    "$CONFIG_DIR/secrets.env" \
    "$OUT/config/secrets.env"

echo "CONFIG_BACKUP=PASS"

echo
echo "===== MANIFEST ====="

{
    echo "RFID_DR_FORMAT=1"
    echo "CREATED_AT=$(date -Is)"
    echo "HOSTNAME=$HOST"
    echo "DATABASE=$DB_NAME"
    echo "GIT_COMMIT=$(git -C "$APP_DIR" rev-parse HEAD)"
    echo "GIT_BRANCH=$(git -C "$APP_DIR" branch --show-current)"
    echo "POSTGRES_VERSION=$(pg_dump --version)"
    echo "OS=$(grep '^PRETTY_NAME=' /etc/os-release | cut -d= -f2-)"
    echo "ARCH=$(dpkg --print-architecture)"
} > "$OUT/MANIFEST.txt"

chmod 0600 "$OUT/MANIFEST.txt"

echo
echo "===== CHECKSUMS ====="

(
    cd "$OUT"
    sha256sum \
        rfid_bridge.dump \
        config/app.env \
        config/secrets.env \
        MANIFEST.txt \
        > SHA256SUMS
)

chmod 0600 "$OUT/SHA256SUMS"

echo
echo "===== FINAL VERIFY ====="

(
    cd "$OUT"
    sha256sum -c SHA256SUMS
)

chmod -R go-rwx "$OUT"

echo
echo "BACKUP_PATH=$OUT"
echo "RFID_EXACT_DR_BACKUP=PASS"
echo
echo "IMPORTANT:"
echo "This backup contains secrets and database contents."
echo "Keep it protected and never commit it to Git."
