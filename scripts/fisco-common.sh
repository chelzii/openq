#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FISCO_DIR="${ROOT_DIR}/runtime-deps/fisco-portable"
NODES_DIR="${FISCO_DIR}/nodes/127.0.0.1"
LOCAL_BINARY="${ROOT_DIR}/runtime-deps/fisco-image/rootfs/fisco-bcos"
LOCAL_RUN_DIR="${ROOT_DIR}/runtime-deps/fisco-portable/local-run"

fisco_mode() {
    if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
        echo "docker"
        return 0
    fi
    if [[ -x "${LOCAL_BINARY}" ]]; then
        echo "local"
        return 0
    fi
    echo "unavailable"
}

local_pid_file() {
    local node_dir=${1}
    printf '%s/%s.pid\n' "${LOCAL_RUN_DIR}" "$(basename "${node_dir}")"
}

local_stdout_log() {
    local node_dir=${1}
    printf '%s/%s.stdout.log\n' "${LOCAL_RUN_DIR}" "$(basename "${node_dir}")"
}

read_port_from_section() {
    local config_path=${1}
    local section=${2}
    awk -F= -v target="${section}" '
        /^\[/ {
            in_section = ($0 == "[" target "]")
            next
        }
        in_section && $1 ~ /listen_port/ {
            gsub(/[[:space:]]/, "", $2)
            print $2
            exit
        }
    ' "${config_path}"
}

find_local_node_pid() {
    local node_dir=${1}
    local rpc_port
    rpc_port=$(read_port_from_section "${node_dir}/config.ini" "rpc")
    if [[ -n "${rpc_port}" ]] && command -v ss >/dev/null 2>&1; then
        local pid
        pid=$(ss -ltnp "( sport = :${rpc_port} )" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n1)
        if [[ -n "${pid}" ]]; then
            printf '%s\n' "${pid}"
            return 0
        fi
    fi
    pgrep -af "${LOCAL_BINARY} -c config.ini -g config.genesis" | awk -v dir="${node_dir}" '$0 ~ dir { print $1; exit }'
}

prepare_local_nodes_file() {
    local node_dir=${1}
    cat > "${node_dir}/nodes.local.json" <<'EOF'
{"nodes":["127.0.0.1:30300","127.0.0.1:30301","127.0.0.1:30302","127.0.0.1:30303"]}
EOF
}

set_nodes_file() {
    local node_dir=${1}
    local target_file=${2}
    python - "${node_dir}/config.ini" "${target_file}" <<'PY'
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
target = sys.argv[2]
text = config_path.read_text(encoding="utf-8")
lines = []
for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("nodes_file="):
        indent = line[: len(line) - len(line.lstrip())]
        lines.append(f"{indent}nodes_file={target}")
    else:
        lines.append(line)
config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

is_local_node_running() {
    local node_dir=${1}
    local pid_file
    pid_file=$(local_pid_file "${node_dir}")
    if [[ -f "${pid_file}" ]]; then
        local pid
        pid=$(cat "${pid_file}")
        if kill -0 "${pid}" >/dev/null 2>&1; then
            return 0
        fi
    fi
    local discovered_pid
    discovered_pid=$(find_local_node_pid "${node_dir}" || true)
    if [[ -n "${discovered_pid}" ]]; then
        mkdir -p "${LOCAL_RUN_DIR}"
        printf '%s\n' "${discovered_pid}" > "${pid_file}"
        return 0
    fi
    return 1
}

start_local_node() {
    local node_dir=${1}
    mkdir -p "${LOCAL_RUN_DIR}"
    prepare_local_nodes_file "${node_dir}"
    set_nodes_file "${node_dir}" "nodes.local.json"
    if is_local_node_running "${node_dir}"; then
        echo "[INFO] $(basename "${node_dir}") 本地进程已在运行"
        return 0
    fi
    local pid_file stdout_log
    pid_file=$(local_pid_file "${node_dir}")
    stdout_log=$(local_stdout_log "${node_dir}")
    (
        cd "${node_dir}"
        : > "${stdout_log}"
        setsid -f "${LOCAL_BINARY}" -c config.ini -g config.genesis >> "${stdout_log}" 2>&1
    )
    local pid=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pid=$(find_local_node_pid "${node_dir}" || true)
        if [[ -n "${pid}" ]]; then
            printf '%s\n' "${pid}" > "${pid_file}"
            return 0
        fi
        sleep 0.5
    done
    echo "[ERROR] $(basename "${node_dir}") 本地进程启动后未找到有效 PID" >&2
    return 1
}

stop_local_node() {
    local node_dir=${1}
    local pid_file
    pid_file=$(local_pid_file "${node_dir}")
    local pid=""
    if [[ -f "${pid_file}" ]]; then
        pid=$(cat "${pid_file}")
    else
        pid=$(find_local_node_pid "${node_dir}" || true)
    fi
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
        kill "${pid}" >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            if ! kill -0 "${pid}" >/dev/null 2>&1; then
                break
            fi
            sleep 0.5
        done
        if kill -0 "${pid}" >/dev/null 2>&1; then
            kill -9 "${pid}" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "${pid_file}"
}
