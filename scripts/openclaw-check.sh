#!/usr/bin/env bash

set -euo pipefail

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
