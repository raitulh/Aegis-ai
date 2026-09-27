#!/usr/bin/env python
"""Seed (or reset) the Aegis demo workspace: three simulated systems audited by the real engines.

Usage:
    uv run python scripts/seed_demo.py [--reset] [--owner-email demo@aegis.example]

Re-runnable: with --reset it deletes the existing demo organization first. Requires the database to be
migrated (scripts/migrate.sh) and DATABASE_ADMIN_URL configured.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select  # noqa: E402

from aegis_api.config import get_settings  # noqa: E402
from aegis_api.db.session import session_factory  # noqa: E402
from aegis_api.models import Organization  # noqa: E402
from aegis_api.services import auth_service, demo_service, policy_service  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Aegis demo workspace")
    parser.add_argument("--reset", action="store_true", help="Delete the existing demo workspace first")
    parser.add_argument("--owner-email", default="demo-owner@aegis.example")
    parser.add_argument("--password", default="Aegis-Demo-Pass!1")
    args = parser.parse_args()

    settings = get_settings()
    slug = settings.demo_reference_org_slug
    session = session_factory(admin=True)()
    try:
        policy_service.seed_frameworks(session)
        session.commit()
        existing = session.scalar(select(Organization).where(Organization.slug == slug))
        if existing and args.reset:
            print(f"Deleting existing demo workspace {existing.id} ...")
            session.execute(_set_delete_allowed())
            session.delete(existing)
            session.commit()
            existing = None
        if existing:
            print(f"Demo workspace already exists ({existing.id}). Use --reset to rebuild.")
            return
        email = (
            f"demo-{uuid.uuid4().hex[:6]}@aegis.example"
            if args.owner_email == "demo-owner@aegis.example"
            else args.owner_email
        )
        result = auth_service.signup(
            session, email=email, password=args.password, full_name="Demo Owner", org_name="Aegis Demo Workspace"
        )
        org = result.organization
        org.slug = slug
        session.flush()
        print("Seeding demo systems and running audits with the real engines ...")
        t0 = time.time()
        out = demo_service.seed_workspace(session, org, minimal=False)
        session.commit()
        print(f"Done in {time.time() - t0:.1f}s.")
        print(f"  Owner login: {email} / {args.password}")
        for s in out.get("systems", []):
            print(f"  System: {s['system']}  audit={s['audit_id']}")
        flagship = out.get("flagship", {})
        print(f"  Flagship remediation verdict: {flagship.get('verdict')}")
    finally:
        session.close()


def _set_delete_allowed():
    from sqlalchemy import text

    return text("set aegis.allow_evidence_delete = 'on'")


if __name__ == "__main__":
    main()
