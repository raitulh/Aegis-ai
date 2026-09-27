#!/usr/bin/env python
"""Register local Ollama Qwen3:1.7B as a Provider and AI System in Aegis."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select
from aegis_api.db.session import session_factory
from aegis_api.db.base import utcnow
from aegis_api.models.systems import AISystem, Provider
from aegis_api.models.tenancy import Organization
from engines.providers.ollama import OllamaProvider
from engines.providers.base import GenerationRequest


def main():
    print("Connecting to local Ollama on http://localhost:11434...")
    ollama = OllamaProvider(base_url="http://localhost:11434", default_model="Qwen3:1.7B")
    health = ollama.health_check()
    print(f"Ollama Health Check: {health}")
    if not health.ok:
        print(f"Warning: Ollama health check returned false: {health.details}")

    session = session_factory(admin=True)()
    try:
        orgs = session.scalars(select(Organization)).all()
        print(f"Found {len(orgs)} organization(s): {[o.name for o in orgs]}")

        for org in orgs:
            # 1. Ensure Ollama Provider
            prov = session.scalar(
                select(Provider).where(Provider.organization_id == org.id, Provider.kind == "ollama")
            )
            if prov is None:
                prov = Provider(
                    organization_id=org.id,
                    kind="ollama",
                    name="Local Ollama",
                    base_url="http://localhost:11434",
                    default_model="Qwen3:1.7B",
                    allow_data_processing=True,
                    status="connected",
                    settings={"base_url": "http://localhost:11434"},
                    last_checked_at=utcnow(),
                )
                session.add(prov)
                session.flush()
                print(f"[{org.name}] Created Ollama provider: {prov.id}")
            else:
                prov.status = "connected"
                prov.default_model = "Qwen3:1.7B"
                prov.last_checked_at = utcnow()
                session.flush()
                print(f"[{org.name}] Found existing Ollama provider: {prov.id}")

            # 2. Ensure Qwen3:1.7B AI System
            sys_item = session.scalar(
                select(AISystem).where(AISystem.organization_id == org.id, AISystem.slug == "qwen3-1-7b")
            )
            if sys_item is None:
                sys_item = AISystem(
                    organization_id=org.id,
                    name="Qwen3-1.7B",
                    slug="qwen3-1-7b",
                    description="Local Ollama Qwen3:1.7B model hosted at http://localhost:11434",
                    system_type="llm",
                    environment="development",
                    business_purpose="Local enterprise reasoning and conversational intelligence",
                    risk_tier="limited",
                    owner_name="Local AI Engineer",
                    provider_id=prov.id,
                    model_name="Qwen3:1.7B",
                    endpoint_url="http://localhost:11434",
                    status="active",
                    config={
                        "temperature": 0.2,
                        "think": False,
                        "domain": "general",
                        "guardrails": {},
                    },
                )
                session.add(sys_item)
                session.flush()
                print(f"[{org.name}] Created AI System 'Qwen3-1.7B': {sys_item.id}")
            else:
                sys_item.provider_id = prov.id
                sys_item.model_name = "Qwen3:1.7B"
                sys_item.status = "active"
                session.flush()
                print(f"[{org.name}] Updated AI System 'Qwen3-1.7B': {sys_item.id}")

        session.commit()
        print("\nAll organizations successfully configured with Ollama Qwen3:1.7B!")

    except Exception as e:
        session.rollback()
        print(f"Error registering system: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
