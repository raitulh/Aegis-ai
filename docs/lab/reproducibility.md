# Reproducibility

## Run manifest

Every experiment run stores a **reproducibility manifest** (`ReproducibilityManifest`, `engines/lab/reproducibility.py`)
and its SHA-256 (`experiment_runs.manifest_sha256`):

| Field | Content |
| --- | --- |
| `experiment_id`, `experiment_version`, `run_id`, `run_kind` | identity |
| `code` | code bundle artifact id + SHA-256 of every file, entrypoint |
| `generated_by` | model provenance when code was generated (provider, model, prompt hash, agent run) |
| `dataset_version_id`, `dataset_checksum` | immutable dataset version and its checksum |
| `environment` | image, digest, declared packages, runtime |
| `seeds`, `parameters`, `command` | exactly what ran |
| `hardware`, `resources` | backend, CPU/memory/time limits and measured usage |
| `network_policy` | `none` unless an approved allowlist was used |
| `logs_artifact_id`, `outputs` | output artifacts with SHA-256 |
| `harness`, `evaluators` | harness key/version/fingerprint and evaluator versions |

`GET /experiment-runs/{id}/manifest` returns the manifest with a **completeness score** and the list of missing
elements (dataset checksum, digest pin, model provenance for generated code, ...).

## Reproducibility package

`GET /experiments/{id}/reproducibility-package` returns a zip:

```
README.md                 contents, manual replay steps, and a note that it is not a certification
experiment.json           experiment identity, status, hypothesis
spec.json                 the exact spec of the version
code/...                  the code bundle (+ code/ENTRYPOINT.json)
manifests/<run_id>.json   manifest + completeness per run
metrics.csv               run_id, run_kind, variant, seed, status, metric, value, self_reported
evaluations.json          evaluator results with evaluator fingerprints
```

## Replay

`POST /experiment-runs/{id}/replay` re-executes a completed run with the same code, data, environment, harness,
parameters and seed as a new run linked to the original (`reproduction_of_run_id`), through the same policy
gate and sandbox. Comparing the two measurements shows whether the result is reproducible on the current
infrastructure. Verification reproductions differ by design: they use **new** seeds to test robustness.

## What is and is not guaranteed

- Measurements are deterministic given identical inputs only when the code itself is deterministic for a seed;
  the platform records everything needed to check this, it does not make non-deterministic code deterministic.
- Unpinned images (no digest) are recorded as such; enable `EXECUTION_REQUIRE_DIGEST_PINNED_IMAGES` in
  production.
