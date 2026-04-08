from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from functools import lru_cache

from app.core.utils import tokenize

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
def _load_sentence_transformer(model_name: str, device: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device, local_files_only=not _allow_remote_model_download())


@lru_cache(maxsize=4)
def _load_reranker_tokenizer(model_name: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )


@lru_cache(maxsize=4)
def _load_reranker_model(model_name: str, device: str):
    from transformers import AutoModelForSequenceClassification

    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        trust_remote_code=True,
        local_files_only=not _allow_remote_model_download(),
    )
    model.to(device)
    model.eval()
    return model


def _allow_remote_model_download() -> bool:
    value = os.getenv("OPENQ_GUARD_ALLOW_REMOTE_DOWNLOAD", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _default_device() -> str:
    requested = os.getenv("OPENQ_GUARD_DEVICE", "cpu").strip().lower()
    if requested and requested != "auto":
        return requested
    try:
        import torch
    except ModuleNotFoundError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


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
        return f"sentence_transformers:{self.device}"

    def score(self, left_text: str, right_text: str) -> EmbeddingScore:
        try:
            model = _load_sentence_transformer(self.model_name, self.device)
            embeddings = model.encode(
                [left_text, right_text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            score = float(embeddings[0].dot(embeddings[1]))
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
