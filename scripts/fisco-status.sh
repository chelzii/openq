#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NODES_DIR="${ROOT_DIR}/runtime-deps/fisco-portable/nodes/127.0.0.1"

source "${SCRIPT_DIR}/fisco-common.sh"

if [[ ! -d "${NODES_DIR}" ]]; then
    echo "[ERROR] 缺少链目录 ${NODES_DIR}"
    exit 1
fi

mode=$(fisco_mode)
if [[ "${mode}" == "docker" ]]; then
    echo "[INFO] 运行中的 FISCO 容器"
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep -E 'fiscobcos|NAMES' || true
else
    echo "[INFO] 本地 FISCO 进程"
    for idx in 0 1 2 3; do
        node_dir="${NODES_DIR}/node${idx}"
        pid_file=$(local_pid_file "${node_dir}")
        if is_local_node_running "${node_dir}"; then
            echo "node${idx}: running pid=$(cat "${pid_file}")"
        else
            echo "node${idx}: stopped"
        fi
    done
fi

echo
echo "[INFO] SDK 证书目录"
echo "${NODES_DIR}/sdk"

echo
echo "[INFO] 关键端口监听"
if command -v ss >/dev/null 2>&1; then
    ss -ltn | grep -E ':2020[0-3]|:3030[0-3]' || true
else
    echo "当前系统无 ss 命令，跳过端口检查。"
fi
