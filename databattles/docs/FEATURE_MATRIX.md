# Feature matrix

Legend: ✅ implemented and exercised by tests or the E2E/crawl · 🟡 implemented, not verified end-to-end here ·
⏳ deferred (see ROADMAP).

| Area | Feature | Status |
|---|---|---|
| Accounts | Email signup, verification, login, logout, password reset/change, email change, sessions list/revoke | ✅ |
| | Lockout + rate limits, generic errors, account deletion (anonymization), data export | ✅ |
| | Google / GitHub sign-in (OAuth) | 🟡 code complete; not run against live providers |
| | Two-factor authentication | ⏳ |
| Profiles | Verified vs self-declared sections, privacy toggles, achievements visibility, activity heatmap, timeline | ✅ |
| Organizations | Universities/clubs/communities/sponsors/companies, departments, requests, invites, roles, domain email verification | ✅ |
| | University admin dashboard & roster export, sponsor dashboard with consent-based talent list | ✅ |
| Competitions | Public / university / invite-only / private; draft→published→finalized→archived; freeze; clone | ✅ |
| | Publish checklist, config versioning, evaluation lock, announcements, schedule, awards, sponsors, staff | ✅ |
| | Teams (invites by handle/email, captaincy, lock, limits), withdraw | ✅ |
| | CSV submissions: idempotent upload, row-level validation, sandboxed scoring, public/private split, daily/total limits | ✅ |
| | Live and final leaderboards, final selection, snapshots, corrections, CSV export | ✅ |
| | Qualification rounds (parent competition link) | 🟡 model + display; no automatic advancement |
| | Judged events: rubrics, assignments, conflicts, project submissions, presentation slots, finalization | ✅ |
| | Organizer analytics (funnel, activity, histogram, errors, university breakdown with suppression) | ✅ |
| | Code-execution evaluators (notebooks/models) | ⏳ requires Docker sandbox work |
| Credentials | Certificates (templates, idempotent issuance, verification page, QR, revocation) | ✅ |
| | Badges (rules engine, manual awards, verification page, recalculation) | ✅ |
| | Certificate PDF download | ⏳ (print-friendly page available) |
| Datasets | Versions, uploads with limits/quota, CSV preview, licenses, citation, terms acceptance, signed downloads, takedown | ✅ |
| Projects | Showcase, members, media gallery, datasets/competition links, featuring, takedown, verified maintainer | ✅ |
| Open source | Repo registration, issues feed (beginner/promoted), webhooks (signature, dedupe, ordering), attribution | ✅ |
| | Live GitHub sync against api.github.com | 🟡 not run against GitHub here |
| Learning | Catalog, paths, enrollment, articles, server-graded quizzes, challenge lessons verified by submissions | ✅ |
| | Authoring (lessons, questions, reorder, publish/versioning), completion badges & certificates | ✅ |
| Community | Discussions (categories, competition/project forums), mentions, accepted answers, edit history, mute | ✅ |
| | Reports, moderation queue, actions, suspensions | ✅ |
| Notifications | In-app feed (cursor), unread count, preferences, email outbox with retries, signed unsubscribe | ✅ |
| | SMTP delivery | 🟡 console backend verified; SMTP not exercised |
| Search | PostgreSQL full-text search with facets, highlights, visibility filtering, command palette | ✅ |
| Admin | Health, errors, jobs (retry), flags, users & roles, audit log, org verification, revenue, emails, tools | ✅ |
| Monetization | Plans & entitlements, manual provider, admin plan changes | ✅ (no payment processor) |
| Platform | Dark/light themes, reduced motion, responsive layout, accessibility basics | ✅ (no formal audit) |
| | Docker Compose deployment, CI workflow | 🟡 files provided; images/CI not run here |
| | S3 storage adapter | 🟡 not exercised |
| | i18n (multiple languages) | ⏳ English only; copy is centralized per component |
