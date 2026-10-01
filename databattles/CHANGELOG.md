# Changelog

## 1.1.0 — premium frontend redesign

* New "data orbit" design system: semantic tokens for surfaces, hairlines, energy gradients, depth and motion; a
  display/headline/title/eyebrow type scale; dark flagship theme with a re-tuned light theme.
* Shared motion primitives (scroll reveal, magnetic CTA, tilt + spotlight cards, count-up) that honour
  `prefers-reduced-motion` and the in-app reduced-motion setting.
* Interactive WebGL hero (three.js + React Three Fiber, lazy-loaded, paused off screen, SVG fallback) showing the
  platform loop; ambient background, perspective grid and data-line visuals.
* Floating glass navigation with a gliding active indicator, a full mobile navigation sheet, theme menu, an upgraded
  command palette (grouped results, recents, contextual and create commands, preferences, keyboard hints) and a new
  footer.
* Homepage rebuilt as a narrative over live API data (metrics, interactive loop, competitions bento, datasets
  catalogue, projects, learning track, open source, verification, organizations, scoring pipeline with a real
  leaderboard).
* Every route group restyled on the shared primitives (competitions, leaderboards, submissions, datasets, projects,
  learning, open source, discussions, search, dashboard, profiles, verification, settings, organizations, organizer,
  judging, moderation, admin, auth and legal pages) with designed loading, empty, error and success states.
* No API, query-key, permission or business-logic changes; no fabricated data.

## 1.0.0 — initial release

* FastAPI modular-monolith API (266 operations) with PostgreSQL, Alembic migration `0001`, append-only audit log trigger.
* Competitions end to end: creation wizard, visibility modes, teams, sandboxed CSV scoring, live/final leaderboards,
  snapshots and corrections, judged events, analytics, certificates and badges.
* Datasets, projects, open-source hub with GitHub linking/webhooks, learning (courses, quizzes, challenges, paths),
  discussions and moderation, notifications with email outbox, full-text search, organizations and university admin,
  sponsor dashboard, platform admin.
* Next.js 16 web app (95 routes) with a dark-first design system, light theme and reduced-motion support.
* Synthetic demo seed with really-scored submissions; purge tool.
* Tests: 42 backend tests, 11 Playwright E2E tests; CI workflow; Docker Compose deployment files; documentation.
