# Architecture

## Overview

```
Browser ──HTTPS──▶ Next.js (web, :3000)
                     │  pages (App Router, client components + TanStack Query)
                     │  /api/v1/* rewrite (same origin → first-party HttpOnly cookies)
                     ▼
                  FastAPI (api, :8000) ──▶ PostgreSQL 16 ◀── Worker (python -m app.worker)
                     │                        ▲  jobs table (SKIP LOCKED)     │
                     │                        │  search_documents (tsvector)   │ sandboxed evaluator
                     ▼                        │                               ▼ (subprocess or docker)
                  Object storage (local volume / S3) ◀────────────────────────┘
```

* **Modular monolith.** Each domain lives in `backend/app/modules/<name>/` with `service.py` (business rules) and
  `router.py` (HTTP contract). Modules call each other's services, never each other's routers.
* **PostgreSQL does triple duty**: relational store, job queue (`jobs`, claimed with `FOR UPDATE SKIP LOCKED`) and
  full-text search (`search_documents.tsv`, weighted `tsvector`, GIN index). No Redis/Elasticsearch needed at MVP scale.
* **Same-origin web → API.** The browser only talks to the Next server; `/api/v1/*` is rewritten to FastAPI. Session
  cookies are therefore first-party, `HttpOnly`, `SameSite=Lax`.

## Backend layers

| Layer | Location | Responsibility |
|---|---|---|
| Config | `app/core/config.py` | Env-driven settings; production refuses insecure defaults |
| Middleware | `app/core/middleware.py` | Request ids, security headers, CSRF (origin + double submit), streamed body-size limits |
| Auth | `app/core/deps.py`, `modules/auth` | Opaque server-side sessions (hashed tokens), bearer tokens for API clients, argon2id, lockout |
| Authorization | `app/core/permissions.py` | Single place for visibility/management rules; hidden resources return **404** |
| Audit | `app/core/audit.py` + DB trigger | Append-only `audit_logs` (UPDATE/DELETE/TRUNCATE rejected by trigger) |
| Errors | `app/core/errors.py`, `main.py` | One JSON error shape `{error:{code,message,details,request_id}}` |
| Jobs | `app/jobs` | Transactional enqueue, idempotency keys, exponential backoff, dead-letter, stale recovery, periodic tasks with advisory locks |
| Storage | `app/storage` | Local/S3 adapters, random keys, `private/` prefix for hidden data, HMAC-signed short-lived download URLs |
| Evaluation | `app/evaluation` | Pure-stdlib validator + metrics; sandbox runner in a separate process |

## Key flows

### Submission scoring

1. `POST /competitions/{slug}/submissions` (multipart, optional `Idempotency-Key`). The API checks participation, team,
   time window, daily/total limits (row-locked per team), extension and header; streams the file to storage with a size
   cap and SHA-256; inserts `submissions(status=queued)` and enqueues `score_submission` **in the same transaction**.
2. The worker claims the job, copies submission + hidden ground truth into an ephemeral directory and runs
   `runner_entry.py` in the sandbox: `validate` (schema, ids, duplicates, value types, row-level errors) then `score`
   (public and private splits using the `Usage` column). No code from participants is ever executed.
3. Results are stored with evaluator version, config hash and config version for reproducibility. Deterministic
   validation failures → `rejected` (not counted against quotas); infrastructure failures retry, then `failed`.
4. The first scored submission locks the evaluation config (`evaluation_locked_at`).

### Leaderboards (rules v1)

* Live: each team's best **public** score among valid scored submissions.
* Final: per team, candidates are the selected finals (or top-N public if none selected); final score = best **private**
  score among candidates. Ties → earlier submission, then team id.
* Finalization writes an immutable `leaderboard_snapshots` row (version 1) and per-user `competition_results`.
  Corrections create a new version with a recorded reason; old versions stay readable.

### Credentials

* Certificates: idempotent issuance (dedupe key per user/event/kind), content snapshotted at issuance, checksummed
  public ids (`DB-XXXX-XXXX-CC`, HMAC check segment), public verification page + QR, revocation with reason.
* Badges: rules engine (`first_submission`, `competition_rank`, `courses_completed`, `merged_prs`, …) evaluated on
  domain events; awards store evidence and criteria version; manual badges require an authorized issuer.

### Organizations and trust

* Membership is **verified** only via institutional email on a platform-approved domain, an admin invite, or admin
  approval. Self-declared affiliation is shown separately and never as verified.
* Email domains can be set only by platform admins and work only for verified organizations.

### GitHub

* OAuth (least privilege `read:user`) links an account; the token is Fernet-encrypted at rest and never sent to browsers.
* Repositories are tracked by immutable numeric id; sync uses ETags and backs off on rate limits.
* Webhooks: HMAC-SHA256 verification, `X-GitHub-Delivery` de-duplication, out-of-order protection via `updated_at`.
* Contributions count only for merged PRs whose author id matches a linked account.

### Notifications

In-app rows with dedupe/group keys; optional email via the outbox (`email_outbox` + `send_email` job), per-kind
preferences, signed one-click unsubscribe links. Periodic tasks send deadline reminders and "ready to finalize" notices.

## Frontend

* `src/lib/api.ts` — fetch wrapper (CSRF header, error normalization) and XHR uploads with progress.
* `src/components/ui` — design system on semantic CSS tokens (dark-first, light theme, reduced motion).
* Pages are client components using TanStack Query; every screen implements loading, empty, error, permission-denied
  and success states. `src/proxy.ts` redirects signed-out users early; the API remains the authority.
* Accessibility: skip link, labelled controls, `aria-current`, keyboard-operable Radix menus/dialogs, charts with
  hidden data tables, focus rings, reduced-motion support.

## Data model (main tables)

`users, auth_sessions, auth_tokens, auth_events, oauth_identities, platform_roles` ·
`organizations, departments, org_memberships, org_invites, plans, org_subscriptions` ·
`competitions, competition_staff, competition_sponsors, competition_config_versions, announcements, schedule_items,
award_categories, competition_participants, teams, team_members, team_invitations` ·
`evaluation_assets, submissions, leaderboard_snapshots, competition_results, event_submissions, rubrics,
judge_assignments, judge_conflicts, judge_scores, presentation_slots` ·
`datasets, dataset_versions, dataset_files, dataset_terms_acceptances, dataset_download_stats` ·
`projects, project_members, project_media, project_datasets, uploads` ·
`github_accounts, github_repositories, github_issues, github_pull_requests, webhook_deliveries` ·
`courses, lessons, quiz_questions, course_enrollments, lesson_progress, learning_paths` ·
`certificate_templates, certificates, badge_definitions, badge_awards` ·
`discussion_categories, threads, comments, post_revisions, thread_mutes, reports, notifications,
notification_preferences, email_outbox` · `audit_logs, jobs, feature_flags, app_error_logs, search_documents, seed_markers`.

All ids are UUIDv7 (time-ordered). All timestamps are stored in UTC and rendered in the viewer's time zone.
