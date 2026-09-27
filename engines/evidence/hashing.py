"""Evidence integrity: content hashing and a per-audit hash chain (tamper-evident evidence log)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def content_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str | None, this_content_hash: str) -> str:
    return hashlib.sha256(f"{prev_hash or 'GENESIS'}:{this_content_hash}".encode()).hexdigest()


@dataclass
class ChainState:
    head: str | None = None
    count: int = 0

    def append(self, payload: Any) -> tuple[str, str, str | None]:
        """Return (content_hash, chain_hash, prev_hash) and advance the chain head."""
        c = content_hash(payload)
        prev = self.head
        ch = chain_hash(prev, c)
        self.head = ch
        self.count += 1
        return c, ch, prev


def verify_chain(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify an ordered list of evidence records ({content_hash, chain_hash, prev_hash})."""
    prev: str | None = None
    broken: list[int] = []
    for i, rec in enumerate(records):
        expected = chain_hash(prev, rec["content_hash"])
        if expected != rec.get("chain_hash") or rec.get("prev_hash") != prev:
            broken.append(i)
        prev = rec.get("chain_hash")
    return {"valid": not broken, "records": len(records), "broken_indices": broken, "head": prev}
