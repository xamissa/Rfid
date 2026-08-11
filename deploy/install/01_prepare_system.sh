#!/bin/bash

# RFID Bridge base operating-system preparation.
#
# Run as root on a clean Raspberry Pi OS Lite 64-bit installation:
#
#   sudo bash deploy/install/01_prepare_system.sh
#
# This script:
# - validates the operating-system architecture
# - installs required Debian packages
# - creates the rfidbridge service account
# - creates the canonical application directories
# - applies restrictive ownership and permissions
#
# It does not:
# - configure PostgreSQL credentials
# - install application source
# - activate the worker
# - contact RFID hardware
# - contact Odoo

APP_ROOT="/opt/rfid_bridge"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
CONFIG_DIR="/etc/rfid_bridge"
DATA_DIR="/var/lib/rfid_bridge"
LOG_DIR="/var/log/rfid_bridge"
RUN_DIR="/run/rfid_bridge"

SERVICE_USER="rfidbridge"
SERVICE_GROUP="rfidbridge"
WEB_GROUP="www-data"

FAIL=0

echo "===== RFID BRIDGE BASE SYSTEM PREPARATION ====="

echo
echo "===== 1. ROOT AND PLATFORM CHECK ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run this script as root."
    exit 1
fi

ARCH="$(dpkg --print-architecture 2>/dev/null)"
OS_ID="$(
    . /etc/os-release 2>/dev/null
    printf '%s' "$ID"
)"
OS_VERSION_ID="$(
    . /etc/os-release 2>/dev/null
    printf '%s' "$VERSION_ID"
)"

echo "ARCH=$ARCH"
echo "OS_ID=$OS_ID"
echo "OS_VERSION_ID=$OS_VERSION_ID"

if [ "$ARCH" != "arm64" ]; then
    echo "FAIL: Expected arm64 architecture."
    exit 1
fi

if [ "$OS_ID" != "debian" ]; then
    echo "FAIL: Expected Debian-based Raspberry Pi OS."
    exit 1
fi

echo
echo "===== 2. PACKAGE INSTALLATION ====="

export DEBIAN_FRONTEND=noninteractive

apt-get update
APT_UPDATE_RC=$?

echo "APT_UPDATE_RC=$APT_UPDATE_RC"

if [ "$APT_UPDATE_RC" -ne 0 ]; then
    echo "FAIL: apt-get update failed."
    exit 1
fi

apt-get install -y \
    ca-certificates \
    curl \
    git \
    nginx \
    postgresql \
    postgresql-client \
    python3 \
    python3-dev \
    python3-venv \
    build-essential \
    libpq-dev
APT_INSTALL_RC=$?

echo "APT_INSTALL_RC=$APT_INSTALL_RC"

if [ "$APT_INSTALL_RC" -ne 0 ]; then
    echo "FAIL: Required package installation failed."
    exit 1
fi

echo
echo "===== 3. SERVICE ACCOUNT ====="

if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_GROUP"
    GROUPADD_RC=$?
else
    GROUPADD_RC=0
fi

echo "GROUPADD_RC=$GROUPADD_RC"

if [ "$GROUPADD_RC" -ne 0 ]; then
    echo "FAIL: Service group creation failed."
    exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd \
        --system \
        --gid "$SERVICE_GROUP" \
        --home-dir "$DATA_DIR" \
        --shell /usr/sbin/nologin \
        "$SERVICE_USER"
    USERADD_RC=$?
else
    USERADD_RC=0
fi

echo "USERADD_RC=$USERADD_RC"

if [ "$USERADD_RC" -ne 0 ]; then
    echo "FAIL: Service account creation failed."
    exit 1
fi

if ! getent group "$WEB_GROUP" >/dev/null 2>&1; then
    echo "FAIL: Required web group does not exist: $WEB_GROUP"
    exit 1
fi

echo
echo "===== 4. CANONICAL DIRECTORIES ====="

install -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 2750 \
    "$APP_ROOT"

install -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 2750 \
    "$APP_DIR"

install -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 2750 \
    "$VENV_DIR"

install -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$CONFIG_DIR"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$DATA_DIR"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$DATA_DIR/state"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$DATA_DIR/backups"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$DATA_DIR/exports"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 2750 \
    "$DATA_DIR/staticfiles"

install -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$LOG_DIR"

install -d \
    -o "$SERVICE_USER" \
    -g "$WEB_GROUP" \
    -m 0750 \
    "$RUN_DIR"

DIRECTORY_RC=$?

echo "DIRECTORY_RC=$DIRECTORY_RC"

if [ "$DIRECTORY_RC" -ne 0 ]; then
    echo "FAIL: Canonical directory creation failed."
    exit 1
fi

echo
echo "===== 5. DIRECTORY VERIFICATION ====="

for PATH_ITEM in \
    "$APP_ROOT" \
    "$APP_DIR" \
    "$VENV_DIR" \
    "$CONFIG_DIR" \
    "$DATA_DIR" \
    "$DATA_DIR/state" \
    "$DATA_DIR/backups" \
    "$DATA_DIR/exports" \
    "$DATA_DIR/staticfiles" \
    "$LOG_DIR" \
    "$RUN_DIR"
do
    stat -c \
        'PATH=%n OWNER=%U GROUP=%G MODE=%a TYPE=%F' \
        "$PATH_ITEM"
    STAT_RC=$?

    if [ "$STAT_RC" -ne 0 ]; then
        FAIL=1
    fi
done

echo
echo "===== 6. SERVICE ACCOUNT VERIFICATION ====="

getent passwd "$SERVICE_USER"
PASSWD_RC=$?

getent group "$SERVICE_GROUP"
GROUP_RC=$?

id "$SERVICE_USER"
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
echo "===== CONCLUSION ====="

if [ "$FAIL" -eq 0 ]; then
    echo "PASS: Base operating-system preparation completed."
    echo "PASS: Service account and directory boundaries are ready."
    echo "HOLD: Database, application and services are not configured yet."
else
    echo "FAIL: Base operating-system preparation is incomplete."
    exit 1
fi
