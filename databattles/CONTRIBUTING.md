# Contributing

1. `make setup && make migrate && make seed`, then run `make api`, `make worker`, `make web`.
2. Keep business rules in `backend/app/modules/<area>/service.py`; routers only translate HTTP. Authorization belongs in
   `app/core/permissions.py` or the service — never only in the UI.
3. Schema changes: edit models, then `alembic revision --autogenerate -m "..."`, review the migration by hand, and make
   sure `alembic check` is clean.
4. New endpoints: document error codes, add tests (happy path + authorization), then `make openapi` to refresh
   frontend types.
5. Frontend: follow `web/FRONTEND_GUIDE.md` — every screen needs loading, empty, error, permission and success states;
   use design tokens only; no fake buttons.
6. Before opening a PR: `make lint typecheck test build` (and `make test-e2e` for UI flows).
7. Never commit secrets, real personal data or real third-party endorsements. Demo content must be synthetic and
   flagged `is_demo`.
