# Roadmap

Near term

* Nonce-based Content-Security-Policy for the web app (remove `'unsafe-inline'` scripts).
* Shared rate-limit backend (Redis or PostgreSQL) for multiple API replicas.
* Two-factor authentication (TOTP) for staff accounts.
* Certificate PDF generation (server-side) in addition to the print view.
* Antivirus integration (ClamAV) behind the existing scanner hook.
* Automatic qualification-round advancement.

Medium term

* Code/notebook evaluators in the Docker sandbox (time/memory budgets, artifact caching).
* Image, audio and segmentation metrics; per-task evaluator plugins.
* Payment provider integration behind the billing abstraction (Stripe or regional providers).
* Internationalization (message catalogs, RTL support) and additional languages.
* Team formation marketplace (requests to join teams).
* Richer analytics exports and scheduled reports for universities.

Longer term

* Split scoring workers into a separate service with a dedicated queue once load requires it.
* Federated verification (Open Badges 3.0 / Verifiable Credentials) for certificates and badges.
