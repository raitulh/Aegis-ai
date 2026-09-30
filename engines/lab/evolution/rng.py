"""Deterministic random streams for the evolution engine.

Every stochastic decision in the engine draws from a stream derived from ``(seed, purpose, generation,
index, …)`` via SHA-256, never from global state. Two consequences:

* the same inputs always produce the same outputs (reproducible evolution runs, replayable workflows);
* independent decisions (e.g. mutating child 3 vs. child 4) use independent streams, so adding a child
  does not perturb the others.

Derived seeds are 31-bit non-negative integers so they fit the ``strategy_mutations.seed`` INTEGER
column and can be recorded next to the mutation they reproduce.
"""

from __future__ import annotations

import hashlib
import random

import numpy as np

_SEED_MASK = 0x7FFFFFFF  # 31 bits → fits a signed 32-bit INTEGER column


def derive_seed(*parts: object) -> int:
    """Return a stable 31-bit seed for ``parts`` (independent of ``PYTHONHASHSEED``)."""
    material = "\x1f".join(repr(p) if not isinstance(p, str) else p for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & _SEED_MASK


def make_rng(*parts: object) -> random.Random:
    """A :class:`random.Random` seeded with :func:`derive_seed` of ``parts``."""
    return random.Random(derive_seed(*parts))


def make_np_rng(*parts: object) -> np.random.Generator:
    """A NumPy PCG64 generator seeded with :func:`derive_seed` of ``parts``."""
    return np.random.default_rng(derive_seed(*parts))
