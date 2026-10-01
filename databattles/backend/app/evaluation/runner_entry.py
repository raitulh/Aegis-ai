"""Sandbox entry point: `python -I runner_entry.py <workdir> <phase>`.

Reads <workdir>/job.json, runs one phase (validate | score) and prints a single
JSON object to stdout. It imports only the standard library plus the pure
evaluation modules, never application settings, the database or the network.
"""

from __future__ import annotations

import json
import os
import sys

# Isolated mode (-I) ignores PYTHONPATH, so locate the package root from this file.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.evaluation.core import EvaluationError, score, validate  # noqa: E402


def main() -> int:
    workdir, phase = sys.argv[1], sys.argv[2]
    with open(os.path.join(workdir, "job.json"), encoding="utf-8") as fh:
        job = json.load(fh)
    sub = os.path.join(workdir, "submission.csv")
    gt = os.path.join(workdir, "ground_truth.csv")
    try:
        if phase == "validate":
            report = validate(sub, gt, job["config"], job["max_rows"])
            out = {"ok": report.ok, "phase": phase, "errors": report.errors, "row_count": report.row_count}
        elif phase == "score":
            out = {"ok": True, "phase": phase, **score(sub, gt, job["config"], job["max_rows"])}
        else:
            out = {"ok": False, "phase": phase, "fatal": {"code": "bad_phase", "message": phase}}
    except EvaluationError as exc:
        out = {"ok": False, "phase": phase, "fatal": {"code": exc.code, "message": exc.message}}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
