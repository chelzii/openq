from __future__ import annotations

import hashlib
import math
import os
import sys
import types
from dataclasses import dataclass
from enum import Enum
from importlib.machinery import ModuleSpec
from functools import lru_cache
from pathlib import Path

from app.core.utils import tokenize

ROOT_DIR = Path(__file__).resolve().parents[2]


def _install_torchvision_stub() -> None:
    try:
        import torchvision  # type: ignore  # noqa: F401
        return
    except Exception:
        pass

    torchvision_stub = types.ModuleType("torchvision")
    transforms_stub = types.ModuleType("torchvision.transforms")
    torchvision_stub.__spec__ = ModuleSpec("torchvision", loader=None)
    transforms_stub.__spec__ = ModuleSpec("torchvision.transforms", loader=None)

    class InterpolationMode(Enum):
        NEAREST = 0
        NEAREST_EXACT = 1
        BILINEAR = 2
        BICUBIC = 3
        BOX = 4
        HAMMING = 5
        LANCZOS = 6

    transforms_stub.InterpolationMode = InterpolationMode
    torchvision_stub.transforms = transforms_stub
    torchvision_stub.datasets = types.ModuleType("torchvision.datasets")
    torchvision_stub.io = types.ModuleType("torchvision.io")
    torchvision_stub.models = types.ModuleType("torchvision.models")
    torchvision_stub.ops = types.ModuleType("torchvision.ops")
    torchvision_stub.utils = types.ModuleType("torchvision.utils")
    torchvision_stub.datasets.__spec__ = ModuleSpec("torchvision.datasets", loader=None)
    torchvision_stub.io.__spec__ = ModuleSpec("torchvision.io", loader=None)
    torchvision_stub.models.__spec__ = ModuleSpec("torchvision.models", loader=None)
    torchvision_stub.ops.__spec__ = ModuleSpec("torchvision.ops", loader=None)
    torchvision_stub.utils.__spec__ = ModuleSpec("torchvision.utils", loader=None)
    sys.modules["torchvision"] = torchvision_stub
    sys.modules["torchvision.transforms"] = transforms_stub
    sys.modules["torchvision.datasets"] = torchvision_stub.datasets
    sys.modules["torchvision.io"] = torchvision_stub.io
    sys.modules["torchvision.models"] = torchvision_stub.models
    sys.modules["torchvision.ops"] = torchvision_stub.ops
    sys.modules["torchvision.utils"] = torchvision_stub.utils


_install_torchvision_stub()


def _local_model_dirs() -> dict[str, Path]:
    model_root = Path(
        os.getenv("OPENQ_MODEL_ROOT", str(ROOT_DIR / "runtime-deps" / "models"))
    ).expanduser()
    return {
        "BAAI/bge-base-zh-v1.5": Path(
            os.getenv("OPENQ_BGE_BASE_ZH_DIR", str(model_root / "BAAI" / "bge-base-zh-v1.5"))
        ).expanduser(),
        "BAAI/bge-reranker-v2-m3": Path(
            os.getenv("OPENQ_BGE_RERANKER_DIR", str(model_root / "BAAI" / "bge-reranker-v2-m3"))
        ).expanduser(),
    }

@dataclass(frozen=True)
class EmbeddingScore:
    score: float
    model_name: str
    backend: str


@dataclass(frozen=True)
class RerankerScore:
    score: float
    model_name: str
    backend: str


class HashingEmbeddingEncoder:
    """Deterministic emergency fallback if the formal model backend is unavailable."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions
        self.model_name = "hashing_fallback"
        self.backend = "hashing_fallback:cpu"

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def cosine_similarity(left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return sum(l * r for l, r in zip(left, right)) / (left_norm * right_norm)

    def score(self, left_text: str, right_text: str) -> EmbeddingScore:
        left = self.encode(left_text)
        right = self.encode(right_text)
        return EmbeddingScore(
            score=round(self.cosine_similarity(left, right), 4),
            model_name=self.model_name,
            backend=self.backend,
        )


class HashingReranker:
    """Lightweight reranker proxy used for offline or reproducible runtimes."""

    def __init__(self, fallback: HashingEmbeddingEncoder | None = None):
        self.fallback = fallback or HashingEmbeddingEncoder()
        self.model_name = "hashing_reranker_proxy"
        self.backend = self.fallback.backend

    def score(self, left_text: str, right_text: str) -> RerankerScore:
        embedding = self.fallback.score(left_text, right_text)
        return RerankerScore(
            score=embedding.score,
            model_name=self.model_name,
            backend=self.backend,
        )


@lru_cache(maxsize=4)
def _load_embedding_tokenizer(model_name: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        _resolve_model_source(model_name),
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )


@lru_cache(maxsize=4)
def _load_embedding_model(model_name: str, device: str):
    from transformers import AutoModel

    model = AutoModel.from_pretrained(
        _resolve_model_source(model_name),
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )
    model.to(device)
    model.eval()
    return model


@lru_cache(maxsize=4)
def _load_reranker_tokenizer(model_name: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        _resolve_model_source(model_name),
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )


@lru_cache(maxsize=4)
def _load_reranker_model(model_name: str, device: str):
    from transformers import AutoModelForSequenceClassification

    model = AutoModelForSequenceClassification.from_pretrained(
        _resolve_model_source(model_name),
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )
    model.to(device)
    model.eval()
    return model


def _allow_remote_model_download() -> bool:
    value = os.getenv("OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _resolve_model_source(model_name: str) -> str:
    local_dir = _local_model_dirs().get(model_name)
    if local_dir is not None and local_dir.exists():
        return str(local_dir)
    return model_name


def _default_device() -> str:
    requested = os.getenv("OPENQ_GUARD_DEVICE", "cpu").strip().lower()
    if requested and requested != "auto":
        return requested
    try:
        import torch
    except ModuleNotFoundError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _mean_pool_embeddings(model_output, attention_mask):
    import torch

    token_embeddings = model_output.last_hidden_state
    mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    summed = torch.sum(token_embeddings * mask, dim=1)
    denom = torch.clamp(mask.sum(dim=1), min=1e-9)
    return summed / denom


class BGEEmbeddingEncoder:
    def __init__(
        self,
        model_name: str = "BAAI/bge-base-zh-v1.5",
        device: str | None = None,
        fallback: HashingEmbeddingEncoder | None = None,
    ):
        self.model_name = model_name
        self.device = device or _default_device()
        self.fallback = fallback or HashingEmbeddingEncoder()

    @property
    def backend(self) -> str:
        return f"transformers_encoder:{self.device}"

    def score(self, left_text: str, right_text: str) -> EmbeddingScore:
        try:
            import torch

            tokenizer = _load_embedding_tokenizer(self.model_name)
            model = _load_embedding_model(self.model_name, self.device)
            inputs = tokenizer(
                [left_text, right_text],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                outputs = model(**inputs)
                embeddings = _mean_pool_embeddings(outputs, inputs["attention_mask"])
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            score = float((embeddings[0] * embeddings[1]).sum().detach().cpu())
            return EmbeddingScore(score=round(score, 4), model_name=self.model_name, backend=self.backend)
        except Exception:
            return self.fallback.score(left_text, right_text)


class BGEReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
        fallback: HashingReranker | None = None,
    ):
        self.model_name = model_name
        self.device = device or _default_device()
        self.fallback = fallback or HashingReranker()

    @property
    def backend(self) -> str:
        return f"transformers_seqcls:{self.device}"

    def score(self, left_text: str, right_text: str) -> RerankerScore:
        try:
            import torch

            tokenizer = _load_reranker_tokenizer(self.model_name)
            model = _load_reranker_model(self.model_name, self.device)
            inputs = tokenizer(
                [left_text],
                [right_text],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                logits = model(**inputs).logits.reshape(-1)
            raw_score = float(logits[0].detach().cpu())
            bounded = 1.0 / (1.0 + math.exp(-max(min(raw_score, 20.0), -20.0)))
            return RerankerScore(score=round(bounded, 4), model_name=self.model_name, backend=self.backend)
        except Exception:
            return self.fallback.score(left_text, right_text)
