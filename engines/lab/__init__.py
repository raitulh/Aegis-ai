"""AI Scientist Evolution Lab — pure domain logic.

Everything under ``engines/lab`` is deterministic, side-effect free and independent of the database,
FastAPI and application services (see AGENTS.md, rule 2). The application layer in
``aegis_api.lab`` persists state and performs IO; this package decides *what is allowed* and *what
something means*: lifecycle state machines, autonomy gates, policy evaluation, experiment-design
validation, statistics, evaluators, failure classification, the evolution engine and verification
criteria.
"""
