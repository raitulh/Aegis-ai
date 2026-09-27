#!/usr/bin/env python
"""Run a real live audit against local Ollama Qwen3:1.7B."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.models.systems import AISystem
from aegis_api.models.audit import Audit
from aegis_api.models.enums import AuditStatus
from aegis_api.services import orchestrator


def main():
    session = session_factory(admin=True)()
    try:
        # Find Qwen3 system in Aegis Demo Workspace
        system = session.scalar(select(AISystem).where(AISystem.slug == "qwen3-1-7b"))
        if not system:
            print("System 'qwen3-1-7b' not found!")
            return

        print(f"Target System: {system.name} (ID: {system.id})")
        print(f"Organization: {system.organization_id}")

        # Set tenant context
        set_tenant(session, system.organization_id, None)

        audit = Audit(
            organization_id=system.organization_id,
            system_id=system.id,
            name="Qwen3-1.7B Live Safety & Privacy Audit",
            categories=["safety", "privacy"],
            intensity="quick",
            status=AuditStatus.QUEUED,
            config={"seed": 42, "concurrency": 2},
        )
        session.add(audit)
        session.commit()
        audit_id = audit.id
        print(f"Created Audit record: {audit_id}")

    finally:
        session.close()

    # Now execute the audit
    print("\nExecuting live audit against Ollama Qwen3:1.7B...")
    exec_session = session_factory()()
    exec_session.begin()
    set_tenant(exec_session, system.organization_id, None)
    try:
        completed_audit = orchestrator.run_audit(exec_session, audit_id)
        exec_session.commit()
        print("\n=== AUDIT COMPLETED ===")
        print(f"Status: {completed_audit.status}")
        print(f"Score: {completed_audit.score}")
        print(f"Findings Count: {completed_audit.findings_count}")
        print(f"Summary: {completed_audit.summary}")
    except Exception as e:
        exec_session.rollback()
        print(f"Audit execution error: {e}")
        raise
    finally:
        exec_session.close()


if __name__ == "__main__":
    main()
