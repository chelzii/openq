#!/usr/bin/env bash

set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    echo "[ERROR] docker 不可用，请先安装 Docker Desktop 或 Docker Engine。"
    exit 1
fi

if docker info >/dev/null 2>&1; then
    exit 0
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user status docker-desktop >/dev/null 2>&1; then
    echo "[INFO] docker daemon 未就绪，尝试启动 docker-desktop.service"
    systemctl --user start docker-desktop
    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            exit 0
        fi
        sleep 1
    done
fi

echo "[ERROR] docker daemon 未启动，请先启动 Docker Desktop 或 Docker Engine。"
exit 1
