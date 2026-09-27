"""Celery worker package.

The task definitions and the configured Celery application live in
:mod:`aegis_api.jobs.tasks`. This package re-exports that application so the
worker can be launched either as::

    celery -A aegis_api.jobs.tasks:celery_app worker   # canonical (scripts/run-worker.sh)
    celery -A workers:celery_app worker                # equivalent shorthand

Keeping the entrypoint here also gives the top-level ``workers`` import root a
concrete module for tooling (mypy, coverage) to discover.
"""

from __future__ import annotations

from aegis_api.jobs.tasks import celery_app

__all__ = ["celery_app"]
