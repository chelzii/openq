from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

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


@lru_cache(maxsize=4)
def _load_sentence_transformer(model_name: str, device: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device)


@lru_cache(maxsize=4)
def _load_reranker_tokenizer(model_name: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)


@lru_cache(maxsize=4)
def _load_reranker_model(model_name: str, device: str):
    from transformers import AutoModelForSequenceClassification

    model = AutoModelForSequenceClassification.from_pretrained(model_name, trust_remote_code=True)
    model.to(device)
    model.eval()
    return model


def _default_device() -> str:
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
    ):
        self.model_name = model_name
        self.device = device or _default_device()

    @property
    def backend(self) -> str:
        return f"sentence_transformers:{self.device}"

    def score(self, left_text: str, right_text: str) -> EmbeddingScore:
        model = _load_sentence_transformer(self.model_name, self.device)
        embeddings = model.encode(
            [left_text, right_text],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        score = float(embeddings[0].dot(embeddings[1]))
        return EmbeddingScore(score=round(score, 4), model_name=self.model_name, backend=self.backend)


class BGEReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str | None = None):
        self.model_name = model_name
        self.device = device or _default_device()

    @property
    def backend(self) -> str:
        return f"transformers_seqcls:{self.device}"

    def score(self, left_text: str, right_text: str) -> RerankerScore:
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
