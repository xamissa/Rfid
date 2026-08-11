#!/bin/bash

# Provision the RFID Bridge PostgreSQL role and database.
#
# Usage:
#   sudo RFID_BRIDGE_DB_PASSWORD='strong-password' \
#     bash deploy/install/02_configure_postgresql.sh
#
# This script is idempotent and does not alter an existing database's data.

DB_NAME="rfid_bridge"
DB_USER="rfid_bridge_app"
DB_PASSWORD="${RFID_BRIDGE_DB_PASSWORD:-}"

echo "===== RFID BRIDGE POSTGRESQL PROVISIONING ====="

if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: Run as root."
    exit 1
fi

if [ -z "$DB_PASSWORD" ]; then
    echo "FAIL: RFID_BRIDGE_DB_PASSWORD is required."
    exit 1
fi

systemctl is-active --quiet postgresql
POSTGRES_ACTIVE_RC=$?

if [ "$POSTGRES_ACTIVE_RC" -ne 0 ]; then
    echo "FAIL: PostgreSQL is not active."
    exit 1
fi

DB_PASSWORD_SQL="$(
    printf '%s' "$DB_PASSWORD" | sed "s/'/''/g"
)"

ROLE_EXISTS="$(
    runuser -u postgres -- psql \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command="
            SELECT 1
            FROM pg_roles
            WHERE rolname = '$DB_USER';
        "
)"

if [ "$ROLE_EXISTS" = "1" ]; then
    printf '%s\n' "
        ALTER ROLE \"$DB_USER\"
        WITH
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS
            PASSWORD '$DB_PASSWORD_SQL';
    " | runuser -u postgres -- psql \
        --no-psqlrc \
        --set=ON_ERROR_STOP=1
else
    printf '%s\n' "
        CREATE ROLE \"$DB_USER\"
        WITH
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS
            PASSWORD '$DB_PASSWORD_SQL';
    " | runuser -u postgres -- psql \
        --no-psqlrc \
        --set=ON_ERROR_STOP=1
fi

DATABASE_EXISTS="$(
    runuser -u postgres -- psql \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command="
            SELECT 1
            FROM pg_database
            WHERE datname = '$DB_NAME';
        "
)"

if [ "$DATABASE_EXISTS" != "1" ]; then
    runuser -u postgres -- createdb \
        --owner="$DB_USER" \
        --encoding=UTF8 \
        --template=template0 \
        "$DB_NAME"
fi

runuser -u postgres -- psql \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1 \
    --dbname="$DB_NAME" \
    --command="
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        GRANT USAGE, CREATE ON SCHEMA public TO \"$DB_USER\";
    "

echo
echo "===== VERIFICATION ====="

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
        WHERE datname = '$DB_NAME';

        SELECT
            rolname,
            rolcanlogin,
            rolcreatedb,
            rolcreaterole,
            rolsuper
        FROM pg_roles
        WHERE rolname = '$DB_USER';
    "

echo
echo "PASS: PostgreSQL role and database are provisioned."
