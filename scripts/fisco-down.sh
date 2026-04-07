#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NODES_DIR="${ROOT_DIR}/runtime-deps/fisco-portable/nodes/127.0.0.1"

if [[ ! -d "${NODES_DIR}" ]]; then
    echo "[ERROR] 缺少链目录 ${NODES_DIR}"
    exit 1
fi

echo "[INFO] 正在停止仓库内 FISCO BCOS"
bash "${NODES_DIR}/stop_all.sh"
echo "[INFO] 停止完成"
