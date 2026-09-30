"""Platform-owned evaluation harnesses.

Harnesses are small, stdlib-only scripts executed in a *separate* sandbox container after the candidate
code finishes. The candidate's output directory and the hidden (harness-only) dataset split are mounted
read-only; only the harness writes ``/output/metrics.json``. This separation means the scientist agent's code
never measures itself when a harness exists.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HARNESS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class HarnessInfo:
    key: str
    version: str
    filename: str
    description: str
    requires_data: bool
    self_reported: bool
    sha256: str

    def files(self) -> dict[str, bytes]:
        return {
            "harness.py": (HARNESS_DIR / self.filename).read_bytes(),
            "_common.py": (HARNESS_DIR / "_common.py").read_bytes(),
        }


_DEFS = (
    (
        "classification",
        "1.0.0",
        "classification.py",
        "Hidden labels vs predictions: accuracy, macro-F1, coverage",
        True,
        False,
    ),
    ("regression", "1.0.0", "regression.py", "Hidden targets vs predictions: MSE, RMSE, MAE, R²", True, False),
    ("objective", "1.0.0", "objective.py", "Re-evaluates the reported solution on a benchmark function", False, False),
    (
        "self_reported",
        "1.0.0",
        "self_reported.py",
        "Validates candidate-reported metrics (flagged self-reported)",
        False,
        True,
    ),
)


@lru_cache(maxsize=1)
def harnesses() -> dict[str, HarnessInfo]:
    out: dict[str, HarnessInfo] = {}
    common = (HARNESS_DIR / "_common.py").read_bytes()
    for key, version, filename, desc, needs_data, self_rep in _DEFS:
        digest = hashlib.sha256((HARNESS_DIR / filename).read_bytes() + b"\x00" + common).hexdigest()
        out[key] = HarnessInfo(key, version, filename, desc, needs_data, self_rep, digest)
    return out


def get(key: str) -> HarnessInfo:
    try:
        return harnesses()[key]
    except KeyError as exc:
        raise KeyError(f"Unknown harness '{key}'") from exc
