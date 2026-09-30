#!/usr/bin/env python
"""Seed the clearly labelled ``[DEMO]`` Scientist Lab content into an organization (idempotent).

Creates a ``[DEMO] Optimization methods lab`` project with a Rastrigin annealing-vs-random-search experiment
template (code bundle + platform objective harness), a demo strategy, a synthetic dataset and a DRAFT mission at
autonomy L1. Nothing is executed and no results are fabricated: runs, metrics and claims only appear after a
human launches the mission.

Usage:
    uv run python scripts/seed_lab_demo.py --org-id <uuid> [--owner-id <uuid>]
    uv run python scripts/seed_lab_demo.py --org-slug aegis-demo        # owner = the organization's first owner
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select  # noqa: E402

from aegis_api.db.session import admin_session_scope  # noqa: E402
from aegis_api.models import Membership, Organization  # noqa: E402
from aegis_api.services.lab import reference, seed  # noqa: E402


def _resolve(args: argparse.Namespace) -> tuple[uuid.UUID, uuid.UUID | None]:
    with admin_session_scope() as db:
        reference.seed(db)  # platform catalogue (builtin environment, evaluators); idempotent
        if args.org_id:
            org = db.get(Organization, uuid.UUID(args.org_id))
        else:
            org = db.scalar(select(Organization).where(Organization.slug == args.org_slug))
        if org is None:
            raise SystemExit("organization not found")
        owner = (
            uuid.UUID(args.owner_id)
            if args.owner_id
            else db.scalar(
                select(Membership.user_id)
                .where(Membership.organization_id == org.id, Membership.role == "owner", Membership.status == "active")
                .order_by(Membership.created_at)
                .limit(1)
            )
        )
        return org.id, owner


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed [DEMO] Scientist Lab content")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--org-id")
    target.add_argument("--org-slug")
    parser.add_argument("--owner-id", help="user who owns the demo project (defaults to the org's first owner)")
    args = parser.parse_args()
    org_id, owner_id = _resolve(args)
    ids = seed.seed_lab_demo(org_id, owner_id)
    print(json.dumps({"organization_id": str(org_id), **{k: str(v) for k, v in ids.items()}}, indent=2))


if __name__ == "__main__":
    main()
