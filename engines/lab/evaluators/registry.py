"""Registry of the built-in evaluators.

``BUILTIN_EVALUATORS`` maps ``key → (class, version, kind)``. Versions are pinned here *and* on the classes; the
module refuses to import if they disagree, so an evaluator's behaviour cannot change without a version bump.
The evaluation service seeds ``lab_evaluators`` rows (organization ``NULL``) from :func:`evaluator_manifest`
and validates organisation-defined configs with :func:`validate_config` before storing them (configs are
immutable once stored; ``config_hash`` identifies them).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from engines.lab.evaluators.base import BaseEvaluator, UnknownEvaluatorError
from engines.lab.evaluators.benchmark import BenchmarkEvaluator
from engines.lab.evaluators.classification import ClassificationEvaluator
from engines.lab.evaluators.code_quality import CodeQualityEvaluator
from engines.lab.evaluators.custom import CustomEvaluator
from engines.lab.evaluators.metric import MetricEvaluator
from engines.lab.evaluators.regression import RegressionEvaluator
from engines.lab.evaluators.reproduction import ReproductionEvaluator
from engines.lab.evaluators.resource import ResourceEvaluator
from engines.lab.evaluators.statistical import StatisticalEvaluator
from engines.lab.experiment_spec import canonical_json

BUILTIN_EVALUATORS: dict[str, tuple[type[BaseEvaluator], str, str]] = {
    "metric": (MetricEvaluator, "1.0.0", "metric"),
    "regression": (RegressionEvaluator, "1.0.0", "regression"),
    "classification": (ClassificationEvaluator, "1.0.0", "classification"),
    "benchmark": (BenchmarkEvaluator, "1.0.0", "benchmark"),
    "statistical": (StatisticalEvaluator, "1.0.0", "statistical"),
    "reproduction": (ReproductionEvaluator, "1.0.0", "reproduction"),
    "code_quality": (CodeQualityEvaluator, "1.0.0", "code_quality"),
    "resource": (ResourceEvaluator, "1.0.0", "resource"),
    "custom": (CustomEvaluator, "1.0.0", "custom"),
}
BUILTIN_EVALUATOR_KEYS: frozenset[str] = frozenset(BUILTIN_EVALUATORS)


def _check_pins() -> None:
    for key, (cls, version, kind) in BUILTIN_EVALUATORS.items():
        if (cls.key, cls.version, cls.kind) != (key, version, kind):
            raise RuntimeError(
                f"evaluator registry pin mismatch for {key!r}: class declares "
                f"({cls.key!r}, {cls.version!r}, {cls.kind!r}), registry pins ({key!r}, {version!r}, {kind!r})"
            )


_check_pins()


def get_evaluator(key: str) -> BaseEvaluator:
    """Instantiate the built-in evaluator ``key`` (raises :class:`UnknownEvaluatorError`)."""
    try:
        cls = BUILTIN_EVALUATORS[key][0]
    except KeyError as exc:
        raise UnknownEvaluatorError(key) from exc
    return cls()


def list_evaluators() -> list[dict[str, Any]]:
    """Catalogue entries (key, version, kind, name, description, independent, config JSON schema)."""
    return [BUILTIN_EVALUATORS[key][0].info() for key in sorted(BUILTIN_EVALUATORS)]


def validate_config(key: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate ``config`` against the evaluator's schema and return it normalised (defaults materialised).

    Raises :class:`~engines.lab.evaluators.base.EvaluatorConfigError` (a ``ValueError``) when invalid.
    """
    evaluator = get_evaluator(key)
    return evaluator.parse_config(config).model_dump(mode="json")


def config_hash(key: str, config: Mapping[str, Any] | None) -> str:
    """SHA-256 of the canonical JSON of the normalised config (64 hex chars, fits ``lab_evaluators``)."""
    normalised = validate_config(key, config)
    return hashlib.sha256(canonical_json({"key": key, "config": normalised}).encode("utf-8")).hexdigest()


def evaluator_manifest() -> list[dict[str, Any]]:
    """Rows for seeding built-in ``lab_evaluators`` (organization ``NULL``) with their default config."""
    rows = []
    for key in sorted(BUILTIN_EVALUATORS):
        cls, version, kind = BUILTIN_EVALUATORS[key]
        default_config: dict[str, Any] | None
        try:
            default_config = validate_config(key, {})
        except ValueError:
            default_config = None  # e.g. the custom evaluator requires an expression
        rows.append(
            {
                "key": key,
                "version": version,
                "kind": kind,
                "name": cls.name,
                "description": cls.description,
                "independent": cls.independent,
                "config_schema": cls.config_schema.model_json_schema(),
                "default_config": default_config,
                "config_hash": config_hash(key, {}) if default_config is not None else None,
            }
        )
    return rows
