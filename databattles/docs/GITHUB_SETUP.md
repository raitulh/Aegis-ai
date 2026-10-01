# GitHub integration

GitHub is optional. Without it, the open-source hub still works with manually registered public repositories (public
metadata is fetched anonymously, subject to GitHub's low anonymous rate limit).

## 1. OAuth app (sign-in and account linking)

Create a GitHub OAuth App (Settings → Developer settings → OAuth Apps):

* Homepage URL: `https://your-domain`
* Authorization callback URL: `https://your-domain/api/v1` — GitHub accepts any callback **under** this path, which
  covers both `/api/v1/auth/oauth/github/callback` (sign-in) and `/api/v1/opensource/github/callback` (linking).

Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.

Scopes: linking requests only `read:user`; sign-in additionally requests `user:email` to read the verified primary email.
No repository scopes are requested. Access tokens are encrypted with Fernet (`ENCRYPTION_KEY`) and never sent to the browser.

## 2. Webhooks (near-real-time issues/PRs)

In each repository (or organization): Settings → Webhooks → Add webhook

* Payload URL: `https://your-domain/api/v1/opensource/github/webhook`
* Content type: `application/json`
* Secret: a long random string → `GITHUB_WEBHOOK_SECRET` (required in production when GitHub is enabled)
* Events: *Issues*, *Pull requests*, *Repository*

Behavior: signatures are verified with HMAC-SHA256 (constant-time compare); each `X-GitHub-Delivery` is processed at
most once; events older than the stored `updated_at` are ignored; renamed/transferred repositories keep their identity
(numeric id); deleted/privatized repositories are hidden from the hub.

## 3. Background sync

The worker re-syncs stale repositories hourly when `GITHUB_API_TOKEN` (a fine-grained token with public read access)
or OAuth is configured, using ETags and backing off until the rate-limit reset time. Maintainers can request a manual
sync at most every 10 minutes.

## 4. Attribution rules

A merged PR counts for a member only if its author's numeric GitHub id matches the account they linked via OAuth.
Typed-in usernames never count. Projects show "Verified maintainer" only when the owner's linked account owns the
registered repository.
