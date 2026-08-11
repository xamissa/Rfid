#!/usr/bin/env bash

set -Eeuo pipefail

umask 022

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="${RFID_BRIDGE_PACKAGE_OUTPUT_DIR:-$HOME/rfid_bridge_handover}"

cd "$APP_DIR"

echo "===== RFID BRIDGE HANDOVER PACKAGE ====="
echo "APP_DIR=$APP_DIR"
echo "OUTPUT_DIR=$OUTPUT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "FAIL: Git working tree contains tracked changes."
    exit 1
fi

if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "FAIL: Git working tree contains untracked files."
    git ls-files --others --exclude-standard
    exit 1
fi

HEAD_COMMIT="$(git rev-parse HEAD)"
SHORT_COMMIT="$(git rev-parse --short=12 HEAD)"
PACKAGE_NAME="rfid_bridge_${SHORT_COMMIT}"

echo "HEAD_COMMIT=$HEAD_COMMIT"
echo "PACKAGE_NAME=$PACKAGE_NAME"

mkdir -p "$OUTPUT_DIR"

ARCHIVE_PATH="$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
BUNDLE_PATH="$OUTPUT_DIR/${PACKAGE_NAME}.bundle"
MANIFEST_PATH="$OUTPUT_DIR/${PACKAGE_NAME}_manifest.txt"
CHECKSUM_PATH="$OUTPUT_DIR/${PACKAGE_NAME}_SHA256SUMS.txt"

rm -f \
    "$ARCHIVE_PATH" \
    "$BUNDLE_PATH" \
    "$MANIFEST_PATH" \
    "$CHECKSUM_PATH"

echo
echo "===== CREATE SOURCE ARCHIVE ====="

git archive \
    --format=tar.gz \
    --prefix="${PACKAGE_NAME}/" \
    --output="$ARCHIVE_PATH" \
    HEAD

echo "ARCHIVE_PATH=$ARCHIVE_PATH"

echo
echo "===== CREATE GIT BUNDLE ====="

git bundle create "$BUNDLE_PATH" --all
git bundle verify "$BUNDLE_PATH"

echo "BUNDLE_PATH=$BUNDLE_PATH"

echo
echo "===== CREATE MANIFEST ====="

{
    echo "PACKAGE_NAME=$PACKAGE_NAME"
    echo "HEAD_COMMIT=$HEAD_COMMIT"
    echo "CREATED_UTC=$(date -u -Is)"
    echo "CREATED_HOST=$(hostname)"
    echo
    echo "===== GIT LOG ====="
    git log -10 --oneline
    echo
    echo "===== TRACKED FILES ====="
    git ls-tree -r --name-only HEAD
} > "$MANIFEST_PATH"

echo "MANIFEST_PATH=$MANIFEST_PATH"

echo
echo "===== CREATE CHECKSUMS ====="

cd "$OUTPUT_DIR"
sha256sum \
    "$(basename "$ARCHIVE_PATH")" \
    "$(basename "$BUNDLE_PATH")" \
    "$(basename "$MANIFEST_PATH")" \
    > "$CHECKSUM_PATH"

sha256sum --check "$CHECKSUM_PATH"

echo
echo "PASS: Handover package created successfully."
echo "CHECKSUM_PATH=$CHECKSUM_PATH"
