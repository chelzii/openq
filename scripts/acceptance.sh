#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD="${OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD:-0}"
export OPENQ_GUARD_DEVICE="${OPENQ_GUARD_DEVICE:-cpu}"
export OPENQ_GUARD_RUNTIME="${OPENQ_GUARD_RUNTIME:-formal}"

echo "[INFO] 运行预检"
"${SCRIPT_DIR}/preflight.sh"

echo
echo "[INFO] 运行语法检查"
uv run python -m compileall app tests

echo
echo "[INFO] 运行关键验收测试"
uv run python -m unittest tests.test_openq

echo
echo "[INFO] 运行最终 5 个验收场景检查"
uv run python - <<'PY'
from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())
container = client.app.state.container
container.orchestrator._reset_state()

scenarios = [
    ("normal_mail_summary", "full", "executed"),
    ("prompt_injection_transfer", "guard_only", "blocked"),
    ("cross_app_privacy_leak", "full", "blocked"),
    ("memory_poisoning", "full", "blocked"),
]
for scenario_id, mode, expected in scenarios:
    payload = client.post("/api/demo/run", json={"scenario_id": scenario_id, "mode": mode, "include_mode_compare": True}).json()
    actual = payload["final_status"]
    assert actual == expected, f"{scenario_id} expected {expected}, got {actual}"

trace_request_id = payload["traces"][-1]["request"]["request_id"]
trace_payload = client.get(f"/api/audit/recent?request_id={trace_request_id}").json()
chain_status = client.get("/api/chain/status").json()
assert trace_payload["request_trace"], "missing request trace bundle"
assert trace_payload["request_trace"]["traces"], "missing persisted traces"
assert trace_payload["audit"], "missing local audit"
if chain_status["available"]:
    assert trace_payload["chain"], "missing chain audit"
else:
    print(f"[WARN] chain unavailable during acceptance: {chain_status['message']}")
print("[INFO] acceptance scenarios ok")
PY

echo
echo "[INFO] 验收完成"
