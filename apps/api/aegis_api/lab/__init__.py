"""AI Scientist Evolution Lab — the autonomous R&D bounded contexts of the Aegis modular monolith.

Layering (see docs/architecture.md):

* ``aegis_api.lab.<context>.router``  — HTTP API (thin; no business logic)
* ``aegis_api.lab.<context>.service`` — application services: use cases and transaction boundaries
* ``engines.lab``                     — pure domain logic (state machines, policy, evolution, statistics…)
* ``aegis_api.lab.models``            — persistence (SQLAlchemy); tenant isolation via PostgreSQL RLS
* infrastructure adapters             — ``lab.llm``, ``lab.storage``, ``lab.execution.backends``,
  ``lab.workflows`` (Temporal), ``lab.tools.mcp``, ``lab.events.bus``

Each context package is an extraction boundary: contexts talk to each other only through the public
functions of their ``service`` modules (never by reaching into another context's tables directly).
"""
