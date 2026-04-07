#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="fiscoorg/fiscobcos:v3.6.0"
IMAGE_ARCHIVE="${ROOT_DIR}/runtime-deps/images/fiscobcos-v3.6.0-image.tar"

if ! command -v docker >/dev/null 2>&1; then
    echo "[ERROR] docker 不可用，请先安装 Docker Desktop 或 Docker Engine。"
    exit 1
fi

if docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
    echo "[INFO] 本地已存在镜像 ${IMAGE_TAG}"
    exit 0
fi

if [[ ! -f "${IMAGE_ARCHIVE}" ]]; then
    echo "[ERROR] 缺少镜像归档 ${IMAGE_ARCHIVE}"
    exit 1
fi

echo "[INFO] 正在导入镜像 ${IMAGE_TAG}"
docker load -i "${IMAGE_ARCHIVE}"
echo "[INFO] 镜像导入完成"
