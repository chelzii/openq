#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
PID_DIR="${LOG_DIR}/pids"
SCRIPT_LOG="${LOG_DIR}/openq-up.log"
FISCO_LOG="${LOG_DIR}/fisco-up.log"

OPENCLAW_PORT="${OPENCLAW_PORT:-18789}"
APP_PORT="${OPENQ_PORT:-8000}"
APP_HOST="${OPENQ_HOST:-0.0.0.0}"
APP_URL_HOST="${OPENQ_URL_HOST:-127.0.0.1}"

OPENCLAW_PID_FILE="${PID_DIR}/openclaw.pid"
APP_PID_FILE="${PID_DIR}/app.pid"

mkdir -p "${LOG_DIR}"
mkdir -p "${PID_DIR}"

exec > >(tee -a "${SCRIPT_LOG}") 2>&1

wait_for() {
    local attempts="$1"
    shift
    local cmd=("$@")
    local i
    for ((i = 1; i <= attempts; i++)); do
        if "${cmd[@]}"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

fisco_running() {
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi
    local idx node_path node_name
    for idx in 0 1 2 3; do
        node_path="${ROOT_DIR}/runtime-deps/fisco-portable/nodes/127.0.0.1/node${idx}"
        node_name="${node_path//\//}"
        if ! docker ps --format '{{.Names}}' | grep -Fxq "${node_name}"; then
            return 1
        fi
    done
    return 0
}

openclaw_healthy() {
    openclaw health >/dev/null 2>&1
}

app_healthy() {
    curl -fsS "http://${APP_URL_HOST}:${APP_PORT}/api/openclaw/status" >/dev/null 2>&1
}

echo "[INFO] 启动链服务"
if fisco_running; then
    echo "[INFO] FISCO BCOS 已在运行"
else
    bash "${SCRIPT_DIR}/ensure-docker.sh"
    echo "[INFO] 后台拉起 FISCO BCOS，日志: ${FISCO_LOG}"
    nohup setsid bash "${SCRIPT_DIR}/fisco-up.sh" >/dev/null 2>&1 </dev/null &
    if ! wait_for 90 fisco_running; then
        echo "[ERROR] FISCO BCOS 启动失败。"
        tail -n 40 "${FISCO_LOG}" || true
        exit 1
    fi
fi

echo "[INFO] 检查 OpenClaw gateway"
if openclaw_healthy; then
    echo "[INFO] OpenClaw gateway 已在运行"
else
    echo "[INFO] 后台拉起 OpenClaw gateway，日志: ${LOG_DIR}/openclaw.log"
    nohup setsid openclaw gateway run \
        --port "${OPENCLAW_PORT}" \
        --bind loopback \
        --allow-unconfigured \
        --compact \
        >"${LOG_DIR}/openclaw.log" 2>&1 </dev/null &
    printf '%s\n' "$!" > "${OPENCLAW_PID_FILE}"
    if ! wait_for 30 openclaw_healthy; then
        echo "[ERROR] OpenClaw gateway 启动失败。"
        tail -n 40 "${LOG_DIR}/openclaw.log" || true
        exit 1
    fi
fi

echo "[INFO] 拉起 FastAPI 演示端，日志: ${LOG_DIR}/openq-app.log"
if app_healthy; then
    echo "[INFO] FastAPI 演示端已在运行"
else
    nohup setsid uv run --active uvicorn app.main:app --host "${APP_HOST}" --port "${APP_PORT}" \
        >"${LOG_DIR}/openq-app.log" 2>&1 </dev/null &
    printf '%s\n' "$!" > "${APP_PID_FILE}"
    if ! wait_for 45 app_healthy; then
        echo "[ERROR] FastAPI 演示端启动失败。"
        tail -n 60 "${LOG_DIR}/openq-app.log" || true
        exit 1
    fi
fi

echo
echo "[INFO] 启动完成"
echo "[INFO] 演示页: http://${APP_URL_HOST}:${APP_PORT}"
echo "[INFO] OpenClaw: ws://127.0.0.1:${OPENCLAW_PORT}"
echo "[INFO] OpenClaw 状态: http://${APP_URL_HOST}:${APP_PORT}/api/openclaw/status"
echo "[INFO] 链状态: http://${APP_URL_HOST}:${APP_PORT}/api/chain/status"
echo "[INFO] 日志目录: ${LOG_DIR}"
echo "[INFO] 状态检查"
"${SCRIPT_DIR}/fisco-status.sh" || true
if command -v openclaw >/dev/null 2>&1; then
    openclaw health || true
fi
echo "[INFO] API /api/chain/status"
curl -fsS "http://${APP_URL_HOST}:${APP_PORT}/api/chain/status" || true
echo
echo "[INFO] API /api/openclaw/status"
curl -fsS "http://${APP_URL_HOST}:${APP_PORT}/api/openclaw/status" || true
echo
echo
echo "[INFO] 说明: 现在是后台启动模式，脚本结束后服务仍会继续运行。"
