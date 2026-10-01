# Known limitations

Be explicit with users and operators about these.

## Not verified in this build

* Docker images and `docker compose up` were not built or run here (the same code runs natively and is tested).
* Google/GitHub OAuth, GitHub API sync and webhooks from real GitHub, SMTP delivery, S3 storage and the Docker
  evaluation sandbox were not exercised against live services (webhooks were tested with signed synthetic payloads).
* No load or performance testing; capacity figures in docs are estimates for a single ~8 GB node.
* No formal accessibility audit (WCAG AA intent: semantic markup, labels, keyboard support, contrast tokens).

## Functional

* The rate limiter is in-memory per API process. Running several API replicas multiplies limits — use one replica or
  add a shared backend.
* The web CSP allows inline scripts (Next.js hydration without nonces). API responses use a strict CSP.
* Malware scanning is a pluggable hook with a no-op default; files are still type/size checked and never executed.
* Only CSV prediction evaluation is implemented (accuracy, macro-F1, RMSE, MAE, R², log loss, ROC AUC).
* Qualification rounds are linked for display; advancing top teams to the next round is manual.
* Team discovery is read-only (no "request to join" flow); captains invite members.
* Some aggregate lists (dashboard widgets, search results) don't carry per-item demo flags; the landing page and item
  pages label demo content.
* `mypy app` reports ~100 findings, almost all SQLAlchemy typing-stub noise; it is configured but not a CI gate.
* English-only UI. Deadlines render in the viewer's time zone, with the event's own zone shown on competition pages.
* Billing is manual: plans and entitlements exist but there is no payment processor.
* Organizers cannot edit an announcement's text after posting (delete and re-post instead).

## Data

* Account deletion anonymizes rather than hard-deletes, to keep competition history consistent.
* Demo data is synthetic and marked `is_demo`; purge it before using a database in production.
