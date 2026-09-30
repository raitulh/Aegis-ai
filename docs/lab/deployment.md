# Deploying the Scientist Lab

## Local: Docker Compose

```bash
cp .env.example .env        # optional for local use; required secrets for production are listed inside
docker compose up --build
```

| Service | Purpose | Port |
| --- | --- | --- |
| `db` | PostgreSQL 16 + pgvector (app role `aegis` with RLS, owner `postgres`) | 5432 |
| `redis` | event bus wake-ups, rate limits, Celery broker | 6379 |
| `minio`, `minio-init` | S3-compatible object storage; private `aegis-lab` bucket | 9000 / 9001 (console) |
| `temporal-db`, `temporal`, `temporal-ui` | durable workflow engine and its UI | 7233 / 8080 |
| `sandbox` | isolated Docker-in-Docker daemon for generated code (mutual TLS; only job-submitting workers can reach it) | — |
| `migrate` | one-shot: `alembic upgrade head` + demo seed | — |
| `api` | FastAPI (no sandbox access, never runs generated code) | 8000 |
| `worker` | Celery worker for assurance jobs | — |
| `lab-temporal-worker` | Temporal worker: lab workflows + activities, submits sandbox jobs | — |
| `lab-scheduler` | approval expiry, reconciliation, sandbox reaping, retention, billing roll-ups | — |
| `lab-event-consumer` | signed webhook delivery | — |
| `web` | Next.js console | 3000 |
| `otel-collector`, `prometheus`, `grafana` | optional, `--profile observability` | 4317/4318, 9090, 3001 |
| `lab-worker` | optional inline engine instead of Temporal, `--profile inline` with `WORKFLOW_ENGINE=inline` | — |

Seed the clearly labelled `[DEMO]` lab content for an organization (a Rastrigin annealing-vs-random-search
experiment template with harness, a demo strategy, a synthetic dataset and a draft mission):

```bash
docker compose exec api python scripts/seed_lab_demo.py --org-id <org uuid> --owner-id <user uuid>
```

Model providers are optional: without an API key (or without the organization's `allow_external_models`
consent) routing fails closed for model-dependent steps, and embeddings use the local hash embedder. Set
`GEMINI_API_KEY` and the `GEMINI_*_MODEL` ids to enable Gemini.

## Local without containers

```bash
uv sync && pnpm install
bash scripts/migrate.sh
WORKFLOW_ENGINE=inline OBJECT_STORAGE_BACKEND=local uv run uvicorn aegis_api.app:app --reload   # API
WORKFLOW_ENGINE=inline uv run python -m aegis_api.processes.worker                              # inline workflows
uv run python -m aegis_api.processes.scheduler
```

The inline engine needs a local Docker daemon for experiment execution (or `EXECUTION_BACKEND=disabled`).

## Kubernetes

`deploy/k8s/` is a kustomize base (validated against the Kubernetes 1.31 schemas):

| File | Content |
| --- | --- |
| `namespaces.yaml` | `aegis` and `aegis-sandbox`, both Pod Security `restricted` |
| `runtimeclass.yaml` | `gvisor` RuntimeClass for sandbox Jobs (requires runsc nodes) |
| `rbac.yaml` | API ServiceAccount without a token; lab-worker ServiceAccount limited to Jobs/ConfigMaps/Pods(logs) in `aegis-sandbox` |
| `configmap.yaml`, `secrets.example.yaml` | configuration; create the real Secret from your secret manager |
| `migrate-job.yaml` | migrations with the owner role (run per release) |
| `api.yaml` | Deployment (non-root, read-only FS, probes `/health/live` + `/health/ready`), Service, HPA, PDB |
| `lab-workers.yaml` | Temporal worker (HPA, PDB, 90 s drain), singleton scheduler, event consumer |
| `networkpolicies.yaml` | default-deny ingress; scoped egress; sandbox namespace deny-all except output upload |
| `sandbox-quota.yaml` | ResourceQuota and LimitRange for sandbox Jobs |

```bash
kubectl apply -f deploy/k8s/namespaces.yaml
kubectl -n aegis create secret generic aegis-secrets --from-env-file=secrets.env
kubectl apply -k deploy/k8s
```

External dependencies are expected as managed services or their own charts: PostgreSQL 16 with pgvector,
Redis, Temporal (namespace `temporal`), S3 or MinIO, an ingress controller, and optionally an OpenTelemetry
collector (`observability` namespace).

## Health, metrics, tracing

- `GET /health/live` — process liveness only (never restart-loops on a dependency outage).
- `GET /health/ready` — 503 unless PostgreSQL (with the `lab` schema), object storage, Temporal (when it is the
  engine) and Redis (when configured) are healthy; model providers and the execution backend are reported.
- `GET /metrics` — Prometheus metrics: HTTP requests/latency, database and Redis operation latency, model
  requests/latency/tokens, agent runs, workflow durations and activities, tool calls, experiment runs, GPU
  seconds, verification durations, active SSE streams and policy decisions; protect with `METRICS_TOKEN`.
- OpenTelemetry traces over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; trace ids are propagated into
  events, agent runs and model usage.
- `GET /api/v1/system/info` — version, environment, workflow engine, execution backend, object storage,
  enabled features and a disclaimer.

## Production checklist

- [ ] `ENVIRONMENT=production` (the app refuses to start without `SECRETS_ENCRYPTION_KEY`, `API_KEY_PEPPER`,
      `JWT_SIGNING_KEY` ≥ 32 chars, S3 object storage, and `TEMPORAL_ADDRESS` when Temporal is the engine).
- [ ] `AGENT_MESSAGE_SIGNING_KEY` set; `METRICS_TOKEN` set.
- [ ] `EXECUTION_BACKEND=kubernetes` with gVisor/Kata; `EXECUTION_REQUIRE_DIGEST_PINNED_IMAGES=true`; the default
      image pinned by digest.
- [ ] Sandbox egress: object storage via ClusterIP; egress gateway if any mission may request network access.
- [ ] `MALWARE_SCANNER=clamav` for user uploads.
- [ ] Organization quotas reviewed: autonomy ceiling, `allow_external_models` consent, LLM/compute spend limits.
- [ ] Backups for PostgreSQL (evidence is append-only; restore procedures must preserve it) and object storage
      (versioning recommended).
- [ ] Log shipping with retention appropriate to your policies (logs are structured JSON with secrets redacted).

## Known limitations

- The Docker Compose sandbox (privileged dind) is suitable for development and single-tenant use only.
- The billing layer ships a no-op provider (`BILLING_PROVIDER=none`) with daily usage roll-ups; connecting a
  payment processor is left to the operator.
- Enterprise SSO is configuration-ready (`FEATURE_ENTERPRISE_SSO`) and must be connected to an identity
  provider before use.
- Throughput and scale have been exercised by the automated test suite only; load testing against your target
  workload is required before relying on it in production.
