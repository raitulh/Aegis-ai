# Deployment

The reference deployment is a **single node** (~8 GB RAM, 2–4 vCPU, no GPU) running Docker Compose:
PostgreSQL 16, the API, one worker, and the Next.js server. Only the web container publishes a port.

## 1. Configure

```bash
cp .env.example .env
```

Set at least `POSTGRES_PASSWORD`, `SECRET_KEY` (≥32 random characters), `WEB_BASE_URL=https://your-domain`,
`COOKIE_SECURE=true`. With `ENV=production` the API **refuses to start** if the secret key is a default, cookies are not
secure, `WEB_BASE_URL` is not https, GitHub is enabled without a webhook secret, or S3/SMTP are selected but unconfigured.

## 2. Start

```bash
docker compose up -d --build        # migrate runs once before api/worker start
docker compose logs -f api worker
```

The API image never auto-migrates; the one-shot `migrate` service runs `alembic upgrade head` on each `up`.

## 3. HTTPS reverse proxy

Terminate TLS in front of the `web` service (port 3000). Example Caddyfile:

```
your-domain.example {
  encode zstd gzip
  reverse_proxy 127.0.0.1:3000
}
```

The API is reachable only through the web server's `/api/v1` rewrite, so cookies stay first-party and CSRF origin checks
see a single origin. Uvicorn trusts `X-Forwarded-*` because only the web container can reach it on the internal network;
if you expose the API directly, restrict `--forwarded-allow-ips`.

## 4. Storage

* **Local** (default): the `storage` volume is shared by `api` and `worker`. Back it up with the database.
* **S3-compatible**: set `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, credentials and optionally `S3_ENDPOINT_URL`
  (MinIO, R2, …). Install `boto3` in the image (`pip install boto3` or build with `.[s3]`). Keep the bucket private;
  downloads use short-lived signed URLs, and hidden ground truth lives under the `private/` prefix which is never
  served.

## 5. Email

`EMAIL_BACKEND=smtp` with `SMTP_HOST/PORT/USERNAME/PASSWORD`, `EMAIL_FROM`. Keep `EMAIL_STORE_BODIES=false` in
production (bodies of sent emails are not persisted). Failed sends retry with backoff and show on the admin dashboard.

## 6. Evaluation sandbox

* `EVALUATOR_SANDBOX=subprocess` (default): isolated Python (`-I`), scrubbed environment, new session, CPU/memory/file
  size/open-file rlimits, wall-clock timeout with process-group kill. The evaluator is platform code (no participant
  code runs), so this is adequate for CSV scoring.
* `EVALUATOR_SANDBOX=docker` (stronger isolation, recommended before adding any code-execution evaluator): build the
  image `docker build -f backend/Dockerfile.evaluator -t databattles-evaluator:latest backend`, run the **worker on the
  host** (or give it access to a Docker daemon) and set `EVALUATOR_WORKDIR_ROOT` to a directory that exists at the same
  path for the worker and the Docker daemon. Containers run with `--network none --read-only --cap-drop ALL
  --security-opt no-new-privileges --user 65534 --pids-limit 64 --memory --cpus 1`. Never mount the Docker socket into
  internet-facing containers.

## 7. Backups and restore

```bash
docker compose exec db pg_dump -U databattles -Fc databattles > backup-$(date +%F).dump
docker run --rm -v databattles_storage:/data -v "$PWD":/out alpine tar czf /out/storage-$(date +%F).tgz -C /data .
# restore
docker compose exec -T db pg_restore -U databattles -d databattles --clean < backup.dump
```

Test restores regularly. The audit log is append-only by trigger; `pg_restore --clean` recreates it.

## 8. Operations

* Health: `GET /healthz` (liveness), `GET /readyz` (database), admin dashboard `/admin` (latency percentiles, error rate,
  job queue, stuck submissions, failed email, rate-limit incidents, suspicious sign-ins).
* Logs are JSON (`LOG_JSON=true`) with request ids; secrets, tokens and passwords are redacted.
* Scale out: add worker replicas (jobs use `SKIP LOCKED`; periodic tasks use advisory locks). Multiple API replicas need
  a shared rate-limit backend (the in-memory limiter is per process) — see ROADMAP.
* Demo data: keep `DEMO_MODE=false` in production and don't run the seed; if you did, purge it from **Admin → Tools**.

## 9. GitHub and OAuth

See [GITHUB_SETUP.md](GITHUB_SETUP.md). Google sign-in: create an OAuth client with redirect URI
`https://your-domain/api/v1/auth/oauth/google/callback` and set `GOOGLE_CLIENT_ID/SECRET`.
