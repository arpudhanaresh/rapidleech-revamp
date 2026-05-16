#!/bin/sh
set -e

DOWNLOAD_DIR="${DOWNLOAD_DIR:-/app/downloads}"
ARIA2_PORT="${ARIA2_PORT:-6800}"
ARIA2_RPC_SECRET="${ARIA2_RPC_SECRET:-}"

mkdir -p "$DOWNLOAD_DIR"

# Start aria2c RPC daemon in background if aria2c is available
if command -v aria2c >/dev/null 2>&1; then
    echo "[entrypoint] Starting aria2c RPC daemon on port ${ARIA2_PORT}..."
    ARIA2_SECRET_FLAG=""
    if [ -n "${ARIA2_RPC_SECRET}" ]; then
        ARIA2_SECRET_FLAG="--rpc-secret=${ARIA2_RPC_SECRET}"
    fi
    aria2c \
        --enable-rpc \
        --rpc-listen-all=false \
        --rpc-listen-port="${ARIA2_PORT}" \
        ${ARIA2_SECRET_FLAG} \
        --daemon=true \
        --quiet=true \
        --dir="${DOWNLOAD_DIR}" \
        --log-level=warn \
        --allow-overwrite=true \
        --auto-file-renaming=false || echo "[entrypoint] aria2c failed to start, falling back to httpx chunker"
else
    echo "[entrypoint] aria2c not found, using built-in httpx chunker"
fi

echo "[entrypoint] Starting RapidLeech-Py..."
exec python main.py
