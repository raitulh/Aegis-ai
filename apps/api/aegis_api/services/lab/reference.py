"""Global lab reference data (admin session, idempotent): built-in environment and the evaluator catalogue."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.models.lab import LabEvaluator
from aegis_api.services.lab import environments
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY


def seed(admin_db: Session) -> None:
    environments.seed_builtin(admin_db)
    for entry in DEFAULT_REGISTRY.catalog():
        exists = admin_db.scalar(
            select(LabEvaluator.id).where(LabEvaluator.key == entry["key"], LabEvaluator.version == entry["version"])
        )
        if exists is None:
            admin_db.add(
                LabEvaluator(
                    key=entry["key"],
                    version=entry["version"],
                    kind=str(entry.get("kind", "metric")),
                    fingerprint=str(entry["fingerprint"]),
                    description=entry.get("description"),
                    default_config=dict(entry.get("default_config") or {}),
                )
            )
    admin_db.flush()
