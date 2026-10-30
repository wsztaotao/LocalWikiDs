"""Optional memory embeddings (design §8.5).

Default: deterministic hash embedding for tests (no network).
When sqlite-vec / real embedding route is available, swap ``embed_fn``.
Falls back to in-memory cosine (≤20k rows) when vec0 is unavailable.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import Callable, Sequence


def hash_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic bag-of-hashes embedding (offline, reproducible)."""
    vec = [0.0] * dim
    tokens = text.lower().replace("，", " ").replace("。", " ").split()
    if not tokens:
        # Character bigrams for CJK without spaces
        s = "".join(ch for ch in text.lower() if not ch.isspace())
        tokens = [s[i : i + 2] for i in range(max(0, len(s) - 1))] or [s or ""]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, min(len(digest), dim * 4), 4):
            idx = struct.unpack_from(">I", digest, i)[0] % dim
            sign = 1.0 if digest[i] % 2 == 0 else -1.0
            vec[idx] += sign
    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


EmbedFn = Callable[[str], list[float]]


@dataclass
class EmbeddingIndex:
    """In-memory vector map; sqlite-vec can back this later."""

    dim: int = 64
    vectors: dict[str, list[float]] = field(default_factory=dict)
    embed_fn: EmbedFn = hash_embed

    def upsert(self, uid: str, vector: Sequence[float] | None = None, text: str = "") -> None:
        if vector is not None:
            self.vectors[uid] = list(vector)
        elif text:
            self.vectors[uid] = self.embed_fn(text)

    def remove(self, uid: str) -> None:
        self.vectors.pop(uid, None)

    def search(
        self, query_vec: Sequence[float], *, top_k: int = 20
    ) -> list[tuple[str, float]]:
        scored = [(uid, cosine(query_vec, vec)) for uid, vec in self.vectors.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
