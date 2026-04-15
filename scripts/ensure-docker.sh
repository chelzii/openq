#!/usr/bin/env bash

set -euo pipefail

is_wsl() {
    grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null
}

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
if is_wsl; then
    echo "[ERROR] 当前检测到 WSL 环境。请确认："
    echo "[ERROR] 1. Windows 侧 Docker Desktop 已启动且状态为 Running"
    echo "[ERROR] 2. Docker Desktop 已启用 WSL 2 engine"
    echo "[ERROR] 3. Docker Desktop -> Settings -> Resources -> WSL Integration 已勾选当前 Ubuntu"
fi
exit 1
