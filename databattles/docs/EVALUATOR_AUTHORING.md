# Evaluator authoring

Evaluators turn a participant's file + hidden ground truth into scores. They are **platform code**, reviewed like any
other code — participant code is never executed.

## Contract

* Entry point: `app/evaluation/runner_entry.py <workdir> <phase>` — runs inside the sandbox with only the standard
  library and `app/evaluation/*` importable. It must not import settings, the database, or network clients.
* Inputs in `<workdir>`: `submission.csv`, `ground_truth.csv`, `job.json` (`{"config": {...}, "max_rows": N}`).
* Phases:
  * `validate` → `{"ok": bool, "errors": [{"code","message","row"?,"column"?}], "row_count": N}` or
    `{"fatal": {"code","message"}}` for configuration problems (these fail the submission and alert organizers).
  * `score` → `{"public": float, "private": float|null, "secondary": {...}}`.
* Output: exactly one JSON object on stdout (≤1 MB). Anything else is a sandbox error.
* Determinism: no randomness, no time dependence, stable sort orders; `PYTHONHASHSEED=0` is set.

## Ground truth format (csv_prediction)

`id_column`, `target_column`, optional `Usage` column with `Public`/`Private` (missing → public). Duplicate ids are
rejected at upload; numeric metrics require parseable numbers. Rows marked `Private` are only used for final ranking.

## Adding a metric

1. Implement a pure function in `app/evaluation/metrics.py` (`fn(y_true, y_pred, **kw) -> float`), raising
   `MetricUndefined` for undefined cases (e.g. single-class AUC).
2. Register a `Metric(key, label, direction, kind, fn, description)` in `METRICS`. `kind` is `label`, `numeric` or
   `probability` and drives value validation.
3. Add unit tests with known values in `backend/tests/test_units.py`.
4. The metric appears automatically in `/meta/config` and the competition wizard.

## Adding an evaluator (new task type)

1. Add an `EvaluatorSpec` to `EVALUATORS` in `app/evaluation/registry.py` with a **versioned** key (`name/1.0.0`).
   Bump the version whenever scoring behavior changes — submissions store `evaluator_version` and `config_hash`.
2. Implement `validate` and `score` for the new format and dispatch on `config["evaluator"]` in `runner_entry.py`.
3. If the evaluator must run untrusted artifacts (e.g. code, model files), require `EVALUATOR_SANDBOX=docker`, keep
   `--network none`, and add explicit time/memory budgets. Never do this with the subprocess sandbox.
4. Extend `normalize_evaluation_config` for new config keys (validate everything; unknown keys are dropped).
5. Test: valid file, each validation error, public/private split, boundary values, timeout behavior.

## Reproducibility

Each scored submission records `evaluator_version`, `config_hash` (SHA-256 of spec version + normalized config + ground
truth hash) and `config_version`. The first scored submission locks the evaluation config; ground truth can then only be
replaced by a platform admin, and finalized results can only change through the audited corrections flow.
