#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD="${OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD:-0}"
export OPENQ_GUARD_DEVICE="${OPENQ_GUARD_DEVICE:-cpu}"
export OPENQ_GUARD_RUNTIME="${OPENQ_GUARD_RUNTIME:-formal}"

echo "[INFO] 运行全量实验导出"
uv run python - <<'PY'
import asyncio
import json

from app.main import build_container


async def main() -> None:
    container = build_container()
    report = await container.orchestrator.run_experiments()
    payload = report.model_dump(mode="json")
    print(json.dumps(
        {
            "run_id": payload.get("run_id"),
            "total_scenarios": payload.get("total_scenarios"),
            "total_runs": payload.get("total_runs"),
            "paper_ablation_runs": payload.get("paper_ablation_runs"),
            "exported_dir": payload.get("exported_dir"),
            "report_json": payload.get("exported_json"),
            "report_csv": payload.get("exported_csv"),
            "paper_ablation_json": payload.get("paper_ablation_json"),
            "paper_ablation_csv": payload.get("paper_ablation_csv"),
            "manifest_json": payload.get("manifest_json"),
        },
        ensure_ascii=False,
        indent=2,
    ))


asyncio.run(main())
PY
