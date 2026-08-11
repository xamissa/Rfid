#!/bin/bash

# Complete RFID Bridge installation orchestrator.
#
# Run from the repository root:
#
#   sudo \
#     RFID_BRIDGE_DB_PASSWORD='strong-password' \
#     RFID_BRIDGE_DJANGO_SECRET='long-random-secret' \
#     RFID_BRIDGE_ALLOWED_HOSTS='127.0.0.1,localhost,hostname,192.168.x.x' \
#     RFID_BRIDGE_SERVER_NAMES='hostname 192.168.x.x' \
#     bash deploy/install/install_rfid_bridge.sh
#
# The web interface is installed and started.
# The RFID worker remains disabled and inactive.
# Reader and Odoo contact remain blocked.

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== COMPLETE RFID BRIDGE INSTALLATION ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

if [ -z "${RFID_BRIDGE_DB_PASSWORD:-}" ]; then
    echo "FAIL: Required environment variable is missing: RFID_BRIDGE_DB_PASSWORD"
    exit 1
fi

if [ -z "${RFID_BRIDGE_DJANGO_SECRET:-}" ]; then
    echo "FAIL: Required environment variable is missing: RFID_BRIDGE_DJANGO_SECRET"
    exit 1
fi

if [ -z "${RFID_BRIDGE_ALLOWED_HOSTS:-}" ]; then
    echo "FAIL: Required environment variable is missing: RFID_BRIDGE_ALLOWED_HOSTS"
    exit 1
fi

if [ -z "${RFID_BRIDGE_SERVER_NAMES:-}" ]; then
    echo "FAIL: Required environment variable is missing: RFID_BRIDGE_SERVER_NAMES"
    exit 1
fi

for SCRIPT in \
    01_prepare_system.sh \
    02_configure_postgresql.sh \
    03_install_application.sh \
    04_install_services.sh \
    05_verify_installation.sh
do
    if [ ! -x "$INSTALL_DIR/$SCRIPT" ]; then
        echo "FAIL: Missing executable installer: $INSTALL_DIR/$SCRIPT"
        exit 1
    fi
done

echo
echo "===== STEP 1: PREPARE SYSTEM ====="
bash "$INSTALL_DIR/01_prepare_system.sh"

echo
echo "===== STEP 2: CONFIGURE POSTGRESQL ====="
bash "$INSTALL_DIR/02_configure_postgresql.sh"

echo
echo "===== STEP 3: INSTALL APPLICATION ====="
bash "$INSTALL_DIR/03_install_application.sh"

echo
echo "===== STEP 4: INSTALL SERVICES ====="
bash "$INSTALL_DIR/04_install_services.sh"

echo
echo "===== STEP 5: VERIFY INSTALLATION ====="
bash "$INSTALL_DIR/05_verify_installation.sh"

echo
echo "===== INSTALLATION COMPLETE ====="
echo "PASS: RFID Bridge web application is installed."
echo "PASS: PostgreSQL, Gunicorn and Nginx are configured."
echo "PASS: Worker remains disabled and inactive."
echo "PASS: Reader and Odoo contact remain blocked."
echo
echo "NEXT_ACTION=Create the first Django administrator."
echo
echo "Run:"
echo "sudo -u rfidbridge \\"
echo "  /opt/rfid_bridge/venv/bin/python \\"
echo "  /opt/rfid_bridge/app/manage.py createsuperuser"
