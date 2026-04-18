#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
SCRIPT_LOG="${LOG_DIR}/fisco-up.log"
FISCO_DIR="${ROOT_DIR}/runtime-deps/fisco-portable"
NODES_DIR="${FISCO_DIR}/nodes/127.0.0.1"
IMAGE_TAG="fiscoorg/fiscobcos:v3.6.0"

source "${SCRIPT_DIR}/fisco-common.sh"

required_ports=(20200 20201 20202 20203 30300 30301 30302 30303)

mkdir -p "${LOG_DIR}"
exec > >(tee -a "${SCRIPT_LOG}") 2>&1

if [[ ! -d "${NODES_DIR}" ]]; then
    echo "[ERROR] 缺少链目录 ${NODES_DIR}"
    exit 1
fi

mode=$(fisco_mode)
if [[ "${mode}" == "unavailable" ]]; then
    echo "[ERROR] Docker 不可用，且仓库内未找到可执行的本地 fisco-bcos 二进制。"
    exit 1
fi

if [[ "${mode}" == "docker" ]]; then
    bash "${SCRIPT_DIR}/ensure-docker.sh"

    if ! docker network inspect openq-fisco >/dev/null 2>&1; then
        echo "[INFO] 创建链专用 Docker 网络 openq-fisco"
        docker network create openq-fisco >/dev/null
    fi

    "${SCRIPT_DIR}/fisco-load-image.sh"

    expected_names=()
    for idx in 0 1 2 3; do
        node_path="${NODES_DIR}/node${idx}"
        expected_names+=("${node_path//\//}")
        set_nodes_file "${node_path}" "nodes.json"
    done

    running_expected=0
    for name in "${expected_names[@]}"; do
        if docker ps --format '{{.Names}}' | grep -Fx "${name}" >/dev/null 2>&1; then
            running_expected=$((running_expected + 1))
        fi
    done

    if [[ "${running_expected}" -eq 4 ]]; then
        echo "[INFO] 仓库内 FISCO BCOS 已在运行 (docker)"
        exit 0
    fi

    if command -v ss >/dev/null 2>&1; then
        occupied=()
        for port in "${required_ports[@]}"; do
            if ss -ltnH "( sport = :${port} )" | grep -q .; then
                occupied+=("${port}")
            fi
        done
        if [[ "${#occupied[@]}" -gt 0 ]]; then
            echo "[ERROR] 以下端口已被占用: ${occupied[*]}"
            echo "[ERROR] 请先停止已有链服务，再启动仓库内 FISCO BCOS。"
            exit 1
        fi
    fi

    echo "[INFO] 正在启动仓库内 FISCO BCOS (docker)"
    bash "${NODES_DIR}/start_all.sh"
    echo "[INFO] 启动完成"
    exit 0
fi

echo "[WARN] Docker 不可用，切换到本地二进制模式启动 FISCO BCOS"
if command -v ss >/dev/null 2>&1; then
    occupied=()
    for port in "${required_ports[@]}"; do
        if ss -ltnH "( sport = :${port} )" | grep -q .; then
            occupied+=("${port}")
        fi
    done
    if [[ "${#occupied[@]}" -gt 0 ]]; then
        echo "[ERROR] 以下端口已被占用: ${occupied[*]}"
        echo "[ERROR] 请先停止已有链服务，再启动仓库内 FISCO BCOS。"
        exit 1
    fi
fi

for idx in 0 1 2 3; do
    node_path="${NODES_DIR}/node${idx}"
    echo "[INFO] 启动本地节点 node${idx}"
    start_local_node "${node_path}"
done

sleep 3
for port in 20200 20201 20202 20203; do
    if ! ss -ltnH "( sport = :${port} )" | grep -q .; then
        echo "[ERROR] 本地节点未成功监听 RPC 端口 ${port}"
        exit 1
    fi
done
echo "[INFO] 本地模式启动完成"
