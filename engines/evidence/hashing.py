"""Evidence integrity: content hashing and a per-audit hash chain (tamper-evident evidence log)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Single source of truth for the hashing scheme: the standalone package verifier ships to customers, so the
# hashes it recomputes must be byte-for-byte the ones written here.
from engines.evidence.package import chain_hash, content_hash

__all__ = ["ChainState", "chain_hash", "content_hash", "verify_chain"]


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
