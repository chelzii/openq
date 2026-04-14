#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
PID_DIR="${LOG_DIR}/pids"
SCRIPT_LOG="${LOG_DIR}/openq-down.log"

APP_PORT="${OPENQ_PORT:-8000}"
OPENCLAW_PORT="${OPENCLAW_PORT:-18789}"

APP_PID_FILE="${PID_DIR}/app.pid"
OPENCLAW_PID_FILE="${PID_DIR}/openclaw.pid"

mkdir -p "${PID_DIR}"

exec > >(tee -a "${SCRIPT_LOG}") 2>&1

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

read_pid_file() {
    local file="$1"
    if [[ ! -f "${file}" ]]; then
        return 1
    fi
    tr -d '[:space:]' < "${file}"
}

pid_alive() {
    kill -0 "$1" >/dev/null 2>&1
}

pid_matches_cmd() {
    local pid="$1"
    local pattern="$2"
    local cmdline=""
    if ! pid_alive "${pid}"; then
        return 1
    fi
    if has_cmd ps; then
        cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
        [[ -n "${cmdline}" ]] && [[ "${cmdline}" == *"${pattern}"* ]]
        return
    fi
    return 1
}

pids_from_port() {
    local port="$1"
    local result=""
    if has_cmd ss; then
        result="$(ss -ltnp "( sport = :${port} )" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)"
        if [[ -n "${result}" ]]; then
            printf '%s\n' "${result}"
            return 0
        fi
    fi
    if has_cmd lsof; then
        lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true
    fi
}

pids_from_pattern() {
    local pattern="$1"
    if has_cmd pgrep; then
        pgrep -f "${pattern}" 2>/dev/null || true
        return 0
    fi
    if has_cmd ps; then
        ps -ef | awk -v pat="${pattern}" '$0 ~ pat { print $2 }' || true
    fi
}

dedupe_pids() {
    awk 'NF && !seen[$0]++ { print $0 }'
}

collect_pids() {
    local pid_file="$1"
    local cmd_pattern="$2"
    local port="$3"

    {
        if pid_from_file="$(read_pid_file "${pid_file}" 2>/dev/null)"; then
            if [[ "${pid_from_file}" =~ ^[0-9]+$ ]] && pid_matches_cmd "${pid_from_file}" "${cmd_pattern}"; then
                printf '%s\n' "${pid_from_file}"
            fi
        fi
        pids_from_port "${port}" || true
        pids_from_pattern "${cmd_pattern}" || true
    } | dedupe_pids
}

stop_pids() {
    local label="$1"
    shift
    local pids=("$@")
    if [[ "${#pids[@]}" -eq 0 ]]; then
        echo "[INFO] ${label} 未发现运行中的进程"
        return 0
    fi

    echo "[INFO] 停止 ${label}: ${pids[*]}"
    local pid
    for pid in "${pids[@]}"; do
        kill "${pid}" >/dev/null 2>&1 || true
    done

    local remaining=()
    local i
    for i in 1 2 3 4 5; do
        remaining=()
        for pid in "${pids[@]}"; do
            if pid_alive "${pid}"; then
                remaining+=("${pid}")
            fi
        done
        if [[ "${#remaining[@]}" -eq 0 ]]; then
            break
        fi
        sleep 1
    done

    if [[ "${#remaining[@]}" -gt 0 ]]; then
        echo "[WARN] ${label} 未在超时时间内退出，执行强制终止: ${remaining[*]}"
        for pid in "${remaining[@]}"; do
            kill -9 "${pid}" >/dev/null 2>&1 || true
        done
    fi
}

echo "[INFO] 开始关停 OpenQ 运行环境"

if has_cmd curl && curl -fsS "http://127.0.0.1:${APP_PORT}/api/openclaw/status" >/dev/null 2>&1; then
    echo "[INFO] 检测到 FastAPI 正在运行"
fi

mapfile -t app_pids < <(collect_pids "${APP_PID_FILE}" "uvicorn app.main:app" "${APP_PORT}" || true)
stop_pids "FastAPI 演示端" "${app_pids[@]}"
rm -f "${APP_PID_FILE}"

mapfile -t openclaw_pids < <(collect_pids "${OPENCLAW_PID_FILE}" "openclaw gateway run" "${OPENCLAW_PORT}" || true)
if [[ "${#openclaw_pids[@]}" -eq 0 ]] && has_cmd openclaw; then
    if openclaw health >/dev/null 2>&1; then
        echo "[INFO] OpenClaw gateway 在线，尝试按端口/进程关闭"
    fi
fi
stop_pids "OpenClaw gateway" "${openclaw_pids[@]}"
rm -f "${OPENCLAW_PID_FILE}"

if [[ -x "${SCRIPT_DIR}/fisco-down.sh" ]]; then
    echo "[INFO] 停止 FISCO BCOS"
    "${SCRIPT_DIR}/fisco-down.sh" || true
else
    echo "[WARN] 未找到 fisco-down.sh，跳过 FISCO 停止"
fi

echo "[INFO] 关停完成"
