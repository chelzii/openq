#!/usr/bin/env bash

set -euo pipefail

OPENCLAW_PORT="${OPENCLAW_PORT:-18789}"
OPENCLAW_URL="${OPENCLAW_URL:-ws://127.0.0.1:${OPENCLAW_PORT}}"

if ! command -v openclaw >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 openclaw 命令。"
    echo "[ERROR] 请先按官方方式安装: https://docs.openclaw.ai/install"
    exit 1
fi

echo "[INFO] OpenClaw 版本"
openclaw --version

echo
echo "[INFO] OpenClaw 健康检查"
openclaw health || true

echo
echo "[INFO] OpenClaw TCP 探测"
python3 - "${OPENCLAW_URL}" <<'PY'
import socket
import sys
from urllib.parse import urlparse

target = urlparse(sys.argv[1])
host = target.hostname or "127.0.0.1"
port = target.port or (443 if target.scheme == "wss" else 80)
try:
    with socket.create_connection((host, port), timeout=2.0):
        print(f"[INFO] TCP connect ok: {host}:{port}")
except OSError as exc:
    print(f"[WARN] TCP connect failed: {host}:{port} -> {exc}")
PY
