# Operations and recovery

## Monitoring

Prometheus metrics at `/metrics` (bearer `METRICS_TOKEN`):

| Metric | What to watch |
| --- | --- |
| `aegis_http_requests_total{method,route,status}` / `aegis_http_request_duration_seconds` | error rate (5xx), latency per route |
| `aegis_job_duration_seconds{job,outcome}` | failed outcomes; audit duration |
| `aegis_job_queue_depth{status}` | growing `queued`/`retrying` → workers down or saturated |
| `aegis_audits_finished_total{status}` | spikes in `failed` |
| `aegis_runtime_events_total{event_type}`, `aegis_runtime_decisions_total` | ingestion volume, block/approval rates |
| `aegis_policy_violations_total{action}` | sudden increases after a policy publish |
| `aegis_webhook_deliveries_total` | failed deliveries |
| `aegis_sse_connections` | open audit streams |

Suggested alerts: `/ready` failing for > 1 min; 5xx > 1 % for 5 min; `aegis_job_queue_depth{status="queued"}` rising for 10 min; any `dead` jobs; webhook failure ratio > 20 %.

Logs are structured JSON (`LOG_JSON=true`) with `request_id`, `trace_id`, `tenant`, and `job_id` where relevant. Inbound `traceparent` headers are honoured and echoed, so traces correlate across your gateway and Aegis.

## Background work

All asynchronous work goes through the job ledger (`job_runs`): idempotency keys, attempts, classified failures (`transient` → retried with exponential backoff, then `dead`; `permanent` → `failed`). The maintenance tick runs: webhook delivery, stale-run recovery, retry of due jobs (inline backend), expired-sandbox purge, idempotency-key pruning, retention, risk-acceptance expiry, runtime-approval expiry, scheduled assurance audits and usage alerts.

Operator commands (inside the API container: `docker compose exec api python -m aegis_api.ops …`):

```bash
python -m aegis_api.ops jobs --status dead          # dead-lettered jobs with their last error
python -m aegis_api.ops redrive <job-run-id>        # re-arm (attempts reset) and dispatch again
python -m aegis_api.ops tick --only reap_stale_runs # run maintenance tasks now
```

Workspace admins see their own jobs under **Audit log → Background jobs**.

## Runbooks

**Audits stuck in `running`.** Workers crashed or were killed mid-run. The reaper re-queues runs whose heartbeat is older than `JOB_LEASE_TIMEOUT_SECONDS` (up to `AUDIT_MAX_ATTEMPTS`, then fails them with a recorded reason). Check worker health and `aegis_job_queue_depth`; run `tick --only reap_stale_runs` to act immediately.

**Webhooks failing.** Deliveries retry with backoff; after 20 consecutive failures a webhook is disabled. Fix the endpoint, re-enable it in **Integrations**, and use **Test** to confirm.

**`/ready` returns 503 with a migration check failure.** The database is behind the code. Run `alembic upgrade head` (owner connection) — the `migrate` service does this on deploy.

**Login throttled for a user.** Per-account throttling blocks after `LOGIN_MAX_FAILURES` within `LOGIN_FAILURE_WINDOW_SECONDS`; it clears automatically at the end of the window.

**Suspected evidence tampering.** **Evidence → Integrity** (or `GET /audits/{id}/evidence/verify`) reports `TAMPERED` with the first broken link. Database triggers block edits by the application role, so tampering implies database-owner access: treat it as a security incident, preserve the database, and compare with previously exported packages (verifiable offline).

## Backup and disaster recovery

- **State**: PostgreSQL holds everything durable (tenants, evidence, ledgers, encrypted secrets). Redis is a broker and event bus only. Uploaded files live under `STORAGE_LOCAL_DIR` (or the configured bucket) — back that up too.
- **Backups**: continuous WAL archiving / PITR on managed Postgres; daily logical dumps for long-term retention. Test restores regularly.
- **Keys**: back up `SECRETS_ENCRYPTION_KEY`, `API_KEY_PEPPER` and `EVIDENCE_SIGNING_KEY` separately from database backups. A restore without the matching encryption key cannot decrypt provider credentials or webhook secrets.
- **Restore procedure**: restore Postgres → deploy the same or newer release with the same keys → `alembic upgrade head` → start api/worker/scheduler → check `/ready` → run **Evidence → Integrity** to confirm chains verify.
- **Targets** depend on your Postgres setup; with PITR, RPO is minutes and RTO is the time to restore plus one deploy.

## Key rotation

- **API keys**: create a new key, switch the integration, revoke the old key (immediate).
- **Webhook secrets**: delete and re-create the webhook (the secret is shown once).
- **`EVIDENCE_SIGNING_KEY`**: rotate during a quiet period; record the old public key and key id (shown on every export) so earlier packages stay verifiable with `--public-key`.
- **`SECRETS_ENCRYPTION_KEY` / `API_KEY_PEPPER`**: rotation requires re-encrypting stored secrets / re-issuing API keys; there is no automated tool in this release.
