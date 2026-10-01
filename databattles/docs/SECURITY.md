# Security

## Principles

Deny by default · authorize on the server for every request · never execute participant code in the API ·
never expose hidden evaluation data · least privilege for integrations · audit sensitive actions · minimize personal data.

## Controls

| Area | Implementation |
|---|---|
| Passwords | argon2id (argon2-cffi defaults), ≥10 chars, common/email-derived passwords rejected, rehash on login |
| Sessions | 256-bit opaque tokens stored **hashed**; HttpOnly, SameSite=Lax, Secure in production; revocable per device; password reset/change revokes other sessions |
| Brute force | Per-IP and per-account rate limits; account lockout window after repeated failures; generic login errors; signup/reset don't reveal account existence |
| CSRF | Origin allow-list + double-submit token for cookie sessions; OAuth `state` in a signed short-lived cookie |
| XSS | Markdown rendered with raw HTML disabled, then sanitized by an allow-list (nh3); safe URL schemes only; `rel="nofollow noopener noreferrer ugc"`; strict CSP on API responses; React escapes everything else |
| Uploads | Extension allow-list, streamed size caps (incl. chunked bodies), SHA-256, zip-slip & zip-bomb checks, scanner hook, images decoded and **re-encoded** (strips EXIF, blocks polyglots/SVG), random storage keys, `nosniff`, downloads served as attachments, HTML/SVG never served inline |
| Hidden data | Ground truth under `private/` storage prefix: no endpoint serves it; private scores hidden until finalization |
| Evaluation | Separate process, isolated interpreter, scrubbed env, rlimits (CPU, memory, file size, fds), timeout + group kill; optional Docker sandbox (no network, read-only, no caps, non-root, pids/memory/cpu limits) |
| SSRF | Only GitHub API is fetched server-side (fixed base URL; pagination links restricted to it); `is_public_hostname` guard for any future URL fetching |
| Integrations | OAuth least-privilege scopes; tokens Fernet-encrypted at rest; never returned to browsers; webhook HMAC + delivery de-dup |
| Authorization | Central `permissions.py`; hidden resources → 404; org roles owner > admin > manager > member with no self-escalation; last-owner/last-admin guards |
| Audit | `audit_logs` append-only via trigger (UPDATE/DELETE/TRUNCATE blocked; only FK `actor_id → NULL` on user deletion allowed); records actor, target, reason, request id, pseudonymized IP |
| Logging | Structured logs with request ids; passwords, tokens, secrets and emails redacted; slow-query logs never include parameters |
| Headers | HSTS (when secure), X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy, CSP (web + API) |
| Secrets | From environment only; production startup refuses defaults; `.env` git-ignored |

## Permission matrix (summary)

| Capability | Who |
|---|---|
| View public competition | Everyone |
| View university-only competition | Active members of the host org, participants, staff |
| View invite-only | Anyone with the link; joining requires the invite code |
| View private / draft | Participants/invitees (private), staff only (draft) |
| Manage competition | Platform admin, competition organizers, host org owner/admin |
| Judge | Assigned judges (only their assigned entries), organizers |
| Freeze competitions, take down content, suspend users | Moderators and platform admins (bans: admins only) |
| Manage org | Owner/admin; managers can view roster & dashboards and manage content |
| Set org email domains / verify orgs | Platform admins only |
| Grant platform roles | Platform admins (cannot remove the last admin) |

## Privacy

* Profiles separate verified facts from self-declared information; each profile section has a privacy toggle.
* Rosters and analytics never expose personal emails; analytics suppress groups smaller than 3.
* Sponsors see only participants who opted in via "Open to opportunities", and only public profile data.
* Account deletion anonymizes the user (email/handle/name replaced), revokes sessions, unlinks GitHub; competition
  results and certificates remain as historical records attributed to "Deleted user". Users can export their data.

## Known gaps

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md): nonce-based CSP for the web app (currently `'unsafe-inline'` for
scripts, required by Next.js without nonces), in-memory rate limiter (per process), no built-in antivirus (a scanner
hook exists), no 2FA yet.

## Reporting vulnerabilities

Please report privately to the operator's security contact (configure one before launch). Do not open public issues.
