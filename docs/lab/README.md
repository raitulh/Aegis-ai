# AI Scientist Evolution Lab

The Scientist Lab is the autonomous research-and-development backend inside Aegis. A **mission** states an
objective; bounded, auditable automation then plans research, generates falsifiable hypotheses, designs and
executes sandboxed experiments, measures them with platform-owned harnesses, verifies claims (including
independent reproductions), and routes candidate discoveries to **human review**. Strategies that drive the
work can be evolved, but only inside an immutable governance envelope and a statistical promotion gate.

It is built on the principle the rest of Aegis follows:

> **Deterministic where possible, model-assisted where useful, evidence-backed everywhere.**
> A model may *propose* (a plan, a hypothesis, code, a narrative); measurements, statistics, verification
> decisions and promotions are computed deterministically and recorded as hash-chained evidence.

## What it is not

- Not a chatbot or a prompt wrapper: agent outputs are schema-validated, tool use is brokered and audited, and
  every consequential step runs as a durable workflow activity with idempotent side effects.
- Not a claim of scientific truth: a "verified" claim means the platform's checks passed on the recorded
  evidence; a "discovery" is a human-approved candidate, never an automatic conclusion.
- Not a compliance certification: governance features support *readiness assessments* and *alignment*;
  they do not certify anything.

## Documentation map

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Components, processes, request and mission flows (Mermaid) |
| [domain-model.md](domain-model.md) | Entities and their state machines |
| [database.md](database.md) | `lab` schema, tenancy (RLS), immutability triggers, migrations |
| [api.md](api.md) | REST conventions: errors, pagination, idempotency, concurrency, SSE; endpoint map |
| [workflows.md](workflows.md) | The 12 durable workflows, engine-neutral definitions, inline and Temporal engines |
| [agent-runtime.md](agent-runtime.md) | 17 agent roles, state machine, ModelGateway, ToolBroker, prompt-injection defences |
| [scientific-memory.md](scientific-memory.md) | Memory governance, knowledge ingestion, knowledge graph, hybrid search |
| [execution.md](execution.md) | Execution fabric: Docker/Kubernetes sandboxes, harnesses, measurements |
| [evolution.md](evolution.md) | Strategy registry, governance envelope, mutation, promotion gate, rollback |
| [verification.md](verification.md) | Claims, verification checks, reproductions, discoveries, cited reports |
| [reproducibility.md](reproducibility.md) | Run manifests, reproducibility packages, replay |
| [security.md](security.md) | Threat model and the controls that enforce it (with the tests that prove them) |
| [deployment.md](deployment.md) | Compose stack, Kubernetes, configuration, production checklist, limitations |

## Quick start (local)

```bash
cp .env.example .env
docker compose up --build            # api :8000 · temporal-ui :8080 · minio console :9001
# In another shell, sign up, then seed the clearly-labelled [DEMO] lab project for your organization:
uv run python scripts/seed_lab_demo.py --org-id <org uuid> --owner-id <user uuid>
```

Without Docker Compose, the API can run with the in-process **inline** workflow engine and a local Docker
daemon; see [deployment.md](deployment.md).
