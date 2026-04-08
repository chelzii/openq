#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"
export OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD="${OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD:-0}"
export OPENQ_GUARD_DEVICE="${OPENQ_GUARD_DEVICE:-cpu}"
export OPENQ_GUARD_RUNTIME="${OPENQ_GUARD_RUNTIME:-formal}"

echo "[INFO] OpenQ 预检开始"
echo "[INFO] ROOT=${ROOT_DIR}"
echo "[INFO] OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD=${OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD}"
echo "[INFO] OPENQ_GUARD_DEVICE=${OPENQ_GUARD_DEVICE}"
echo "[INFO] OPENQ_GUARD_RUNTIME=${OPENQ_GUARD_RUNTIME}"

echo
echo "[INFO] Python / Guard 模型状态"
uv run python - <<'PY'
from app.guards.embedding import BGEEmbeddingEncoder, BGEReranker

try:
    import torch
    print(f"[INFO] torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
except Exception as exc:
    print(f"[WARN] torch unavailable: {exc}")

encoder = BGEEmbeddingEncoder()
embedding = encoder.score("请查看北京天气", "查询天气信息")
print(f"[INFO] embedding_model={embedding.model_name} backend={embedding.backend} score={embedding.score}")

reranker = BGEReranker()
try:
    score = reranker.score("请查看北京天气", "查询天气信息")
    print(f"[INFO] reranker_model={score.model_name} backend={score.backend} score={score.score}")
except Exception as exc:
    print(f"[WARN] reranker unavailable, runtime will proxy to embedding fallback: {type(exc).__name__}: {exc}")
PY

echo
echo "[INFO] FISCO 状态"
"${SCRIPT_DIR}/fisco-status.sh" || true

echo
echo "[INFO] OpenClaw 状态"
"${SCRIPT_DIR}/openclaw-check.sh" || true

echo
echo "[INFO] FastAPI 应用装载"
uv run python - <<'PY'
from app.main import create_app

app = create_app()
print(f"[INFO] app title={app.title} version={app.version}")
PY

echo
echo "[INFO] 预检完成"
