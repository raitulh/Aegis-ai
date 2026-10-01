# API

Base path: `/api/v1`. Interactive docs: `http://localhost:8000/docs` (development only). The machine-readable contract is
`backend/openapi.json` (regenerate with `make openapi`); frontend types are generated from it.

## Conventions

| Topic | Rule |
|---|---|
| Auth (browser) | `db_session` cookie (HttpOnly, SameSite=Lax, Secure in production), opaque server-side session |
| Auth (API clients) | `POST /auth/login {"issue_bearer": true}` → `Authorization: Bearer <token>` |
| CSRF | Unsafe methods with a session cookie must send `X-CSRF-Token` equal to the `db_csrf` cookie; `Origin` must be allowed |
| Errors | `{"error": {"code", "message", "details", "request_id"}}`; `details.fields` holds per-field messages |
| Hidden resources | Return **404** (not 403) when revealing existence would leak information |
| Pagination | Lists: `?page=&page_size=` → `{items,total,page,page_size,has_next}`; feeds: `?cursor=` → `{items,next_cursor}` |
| Idempotency | Uploads accept `Idempotency-Key`; retries return the original resource |
| Rate limits | `429 rate_limited` with `Retry-After`; per-IP for anonymous endpoints, per-user otherwise |
| Time | ISO-8601 UTC timestamps; clients render in local time |
| Markdown | Stored as markdown, returned as sanitized `*_html`; raw HTML is never rendered |

## Endpoint groups (266 operations)

| Prefix | Purpose |
|---|---|
| `/auth` | signup, verify email, login/logout, password reset/change, email change, sessions, OAuth (Google/GitHub) |
| `/me`, `/users/{handle}` | profile, privacy, onboarding, dashboard, avatar, data export, account deletion, public profiles |
| `/orgs` | organizations, departments, memberships (request, domain email verification, review, roles), invites, admin & sponsor dashboards, subscription |
| `/competitions` | discovery, CRUD, publish checks, lifecycle (publish/freeze/archive/clone), join/withdraw, staff, sponsors, announcements, schedule, awards, ground truth, participants, analytics |
| `/competitions/{slug}/teams`, `/me/team-invitations` | teams, invitations (handle or email), captaincy, leave/remove |
| `/competitions/{slug}/submissions`, `/submissions/{id}` | uploads, history, cancel, final selection, organizer view, invalidation |
| `/competitions/{slug}/leaderboard`, `/finalize`, `/results/*` | live/final leaderboards, finalization, corrections, history, CSV export |
| `/competitions/{slug}/rubric`, `/judges`, `/judging/*`, `/project-submission(s)`, `/presentations`, `/judge/events` | judged events |
| `/certificates`, `/certificate-templates`, `/badges` | issue/verify/revoke certificates (+QR), templates, badge catalog, awards, verification |
| `/datasets` | datasets, versions, file upload/preview, signed downloads, terms acceptance, takedown |
| `/projects` | showcase projects, members, media, featuring, takedown |
| `/opensource` | hub overview, repositories, issues feed, promotion, GitHub account linking, **webhook receiver** |
| `/learn` | catalog, paths, enrollment, lessons, quizzes (server-graded), challenges, authoring |
| `/discussions`, `/reports`, `/moderation` | threads, comments, revisions, accept/lock/pin/hide/mute; reports and moderation queue |
| `/notifications` | cursor feed, unread count, mark read, preferences, signed unsubscribe |
| `/search` | full-text search with facets, suggestions |
| `/admin` | health, errors, jobs, flags, users & roles, audit log, verification queue, revenue, emails, tools |
| `/meta`, `/billing`, `/files` | public config, landing stats, markdown preview; plans; signed file delivery and public media |

Health probes (no prefix): `GET /healthz`, `GET /readyz`.

## Stable error codes (selection)

Authentication: `unauthenticated`, `invalid_credentials`, `login_locked`, `email_not_verified`, `account_suspended`,
`invalid_token`, `reauth_failed`, `csrf_failed`, `rate_limited`.
Validation: `validation_error`, `invalid_file`, `invalid_file_type`, `file_too_large`, `payload_too_large`,
`invalid_archive`, `invalid_image`, `malware_detected`, `quota_exceeded`.
Competitions: `publish_requirements_unmet`, `invalid_transition`, `scoring_locked`, `competition_finalized`,
`competition_not_ended`, `pending_submissions`, `registration_closed`, `rules_not_accepted`, `invite_required`,
`already_joined`, `not_participant`, `submissions_closed`, `submission_limit_reached`, `selection_limit`,
`ground_truth_invalid`, `evaluation_not_configured`.
Teams: `team_full`, `team_locked`, `team_name_taken`, `invitation_expired`, `invitation_closed`, `invitee_ineligible`,
`captain_must_transfer`.
Judging: `rubric_missing`, `rubric_locked`, `judging_finalized`, `not_assigned`, `conflict_of_interest`.
Credentials: `certificate_invalid`, `certificate_not_found`, `already_awarded`, `badge_automatic`.
Organizations: `requests_closed`, `domain_verification_unavailable`, `org_email_used`, `invite_invalid`,
`invite_email_mismatch`, `last_owner`, `already_member`.
Learning: `not_enrolled`, `quiz_required`, `quiz_empty`, `course_empty`, `quiz_attempt_limit`, `challenge_unavailable`.
Discussions: `thread_locked`, `thread_deleted`, `own_content`.
GitHub: `github_disabled`, `github_repo_not_found`, `github_repo_private`, `github_rate_limited`, `github_unavailable`,
`github_linked_elsewhere`, `github_already_linked`, `sync_too_soon`.

## Webhooks

`POST /api/v1/opensource/github/webhook` — requires `X-GitHub-Event`, `X-GitHub-Delivery` and a valid
`X-Hub-Signature-256` (HMAC-SHA256 with `GITHUB_WEBHOOK_SECRET`). Responses: `202 {"status": "processed" | "ignored" |
"duplicate"}`, `401` bad signature, `404` webhooks disabled, `500 {"status":"failed"}` (GitHub may redeliver).
