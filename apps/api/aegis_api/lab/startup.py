"""Idempotent start-up seeding for built-in lab catalogs (runs on the owner connection).

Each seeder belongs to its context and must be idempotent; failures are logged and never block start-up
(the API can serve without, e.g., seeded prompt templates — dependent features report a clear error).
"""

from __future__ import annotations

import importlib

import structlog

from aegis_api.db.session import session_factory

log = structlog.get_logger("aegis.lab.startup")

# (module, function) — each function takes an owner/admin Session.
SEEDERS: tuple[tuple[str, str], ...] = (
    ("aegis_api.lab.identity.service", "seed_rbac_catalog"),
    ("aegis_api.lab.prompts.registry", "seed_system_prompts"),
    ("aegis_api.lab.usage.billing", "seed_billing_plans"),
    ("aegis_api.lab.evaluation.service", "seed_evaluators"),
    ("aegis_api.lab.strategies.benchmarks", "seed_benchmark_suites"),
)


def run_startup_seeders() -> dict[str, str]:
    results: dict[str, str] = {}
    for module_name, fn_name in SEEDERS:
        key = f"{module_name}:{fn_name}"
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, fn_name)
        except (ModuleNotFoundError, AttributeError):
            results[key] = "missing"
            log.warning("lab_seeder_missing", seeder=key)
            continue
        session = session_factory(admin=True)()
        try:
            fn(session)
            session.commit()
            results[key] = "ok"
        except Exception:
            session.rollback()
            results[key] = "failed"
            log.warning("lab_seeder_failed", seeder=key, exc_info=True)
        finally:
            session.close()
    return results
