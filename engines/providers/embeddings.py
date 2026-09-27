"""Embedding backends. The default ``HashEmbedder`` is a deterministic, local feature-hashing embedder
(lexical, not semantic) so that pgvector search works without any external service. Configure an
Ollama/Gemini/OpenAI embedding model for semantic retrieval; its dimension must equal ``EMBEDDING_DIM``.
"""

from __future__ import annotations

import hashlib
import math
from itertools import pairwise
from typing import Protocol

from engines.common.text import content_tokens
from engines.providers.base import ModelProvider


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.name = f"aegis-hash-{dim}"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = content_tokens(text)
        features = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * (1.0 if "_" not in feature else 0.5)
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class ProviderEmbedder:
    def __init__(self, provider: ModelProvider, model: str, dim: int) -> None:
        self.provider = provider
        self.model = model
        self.dim = dim
        self.name = f"{provider.kind}:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.provider.embed(texts, model=self.model)
        for v in vectors:
            if len(v) != self.dim:
                raise ValueError(f"Embedding model {self.name} returned {len(v)} dims; EMBEDDING_DIM is {self.dim}")
        return vectors


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0
