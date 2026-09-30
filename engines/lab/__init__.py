"""AI Scientist Evolution Lab — pure domain layer.

Everything under ``engines.lab`` is deterministic, side-effect free domain logic: state machines, the
policy engine, the model router, experiment design validation, evaluators, failure intelligence, the
evolution engine, verification criteria and prompt security. Nothing here imports the database, FastAPI
or application services (see AGENTS.md, rule 2). The application layer (``aegis_api``) persists state,
performs I/O and calls into these modules.
"""
