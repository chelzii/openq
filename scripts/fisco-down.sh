#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
SCRIPT_LOG="${LOG_DIR}/fisco-down.log"
NODES_DIR="${ROOT_DIR}/runtime-deps/fisco-portable/nodes/127.0.0.1"

if [[ ! -d "${NODES_DIR}" ]]; then
    echo "[ERROR] 缺少链目录 ${NODES_DIR}"
    exit 1
fi

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${SCRIPT_LOG}") 2>&1

echo "[INFO] 正在停止仓库内 FISCO BCOS"
bash "${NODES_DIR}/stop_all.sh"
echo "[INFO] 停止完成"
