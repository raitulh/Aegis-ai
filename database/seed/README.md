# database/seed

Seed data for Aegis is **code-driven and idempotent**, not raw SQL fixtures:

- **Reference framework packs** (NIST AI RMF, OWASP LLM Top 10, ISO/IEC 42001) are defined in
  [`engines/compliance/frameworks.py`](../../engines/compliance/frameworks.py) and loaded on API startup by
  `policy_service.seed_frameworks()` (idempotent).
- **The demo workspace** (three simulated systems audited by the real engines) is built by
  [`scripts/seed_demo.py`](../../scripts/seed_demo.py) — run with `pnpm db:seed`, or automatically by the
  Docker `migrate` service.

This directory is reserved for any future declarative SQL seed files. Keeping seeds in code lets them run through
the same RLS-aware sessions and stay in lockstep with the ORM models.
