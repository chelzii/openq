#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONSOLE_DIR="${ROOT_DIR}/runtime-deps/fisco-portable/console"
JDK_DIR="${ROOT_DIR}/runtime-deps/jdks/temurin-11"

if [[ ! -d "${CONSOLE_DIR}" ]]; then
    echo "[ERROR] 缺少 console 目录 ${CONSOLE_DIR}"
    exit 1
fi

if [[ ! -f "${CONSOLE_DIR}/conf/config.toml" ]]; then
    cp "${CONSOLE_DIR}/conf/config-example.toml" "${CONSOLE_DIR}/conf/config.toml"
fi

if [[ -x "${JDK_DIR}/bin/java" ]]; then
    export JAVA_HOME="${JDK_DIR}"
    export PATH="${JAVA_HOME}/bin:${PATH}"
fi

cd "${CONSOLE_DIR}"
bash "${CONSOLE_DIR}/start.sh" "$@"
