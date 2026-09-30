# Durable workflows

Every multi-step lab process is a **durable workflow**: an engine-neutral `async` function over a
`WorkflowContext` (`workflows/api.py`). All I/O — database, model calls, sandbox jobs, object storage — happens in
**activities** (`workflows/activities.py`, 65 of them); workflow code only orchestrates, so it can be replayed
deterministically after a crash.

## The workflows

| Name | Purpose | Execution timeout |
| --- | --- | --- |
| `mission` (MissionWorkflow) | End-to-end mission lifecycle: brief → plan → research → hypotheses → experiments → verification → discovery → report, with checkpoints and autonomy gates | 30 days |
| `research` (ResearchWorkflow) | Gemini Deep Research (background interaction, polling, resumable) or the literature pipeline; plan approval when required | 1 day |
| `hypothesis` (HypothesisWorkflow) | Generate, critique and deterministically rank hypotheses | 7 days |
| `experiment` (ExperimentWorkflow) | Execute and measure one run (candidate container + harness container) | 1 day |
| `experiment_batch` (ExperimentBatchWorkflow) | Queue, plan and gate all runs of an experiment, execute them as parallel child `experiment` workflows, recover once from failures (failure analysis → new version → retry), record completion | 7 days |
| `evaluation` (EvaluationWorkflow) | Evaluator suite, statistics, claim extraction, model-assisted interpretation and review | 1 day |
| `verification` (VerificationWorkflow) | Reproductions and the seven verification checks → decision | 7 days |
| `evolution` (EvolutionWorkflow) | Strategy evolution in experiment or benchmark mode with gated promotion | 14 days |
| `discovery` (DiscoveryWorkflow) | Register a discovery from a verified claim and wait for human review | 30 days |
| `report` (ReportWorkflow) | Deterministic facts with `[EV:id]` citations plus a validated narrative | 1 day |
| `dataset_processing` (DatasetProcessingWorkflow) | Profile, checksum and register an immutable dataset version | 1 day |
| `artifact_processing` (ArtifactProcessingWorkflow) | Fetch (SSRF-checked) → scan → parse → injection-score → chunk → embed → graph | 1 day |
| `benchmark` | Run a platform benchmark suite | 1 day |

## Workflow API

```python
class WorkflowContext(Protocol):
    run_id: str; workflow: str; input: dict
    async def activity(self, name, payload, *, key=None, retry=DEFAULT_RETRY, timeout_seconds=900, heartbeat_seconds=None) -> dict
    async def wait_signal(self, name, *, key=None, timeout_seconds=None) -> dict | None   # None = timed out
    async def sleep(self, seconds, *, key=None) -> None
    async def child(self, workflow, payload, *, key) -> dict
    def now(self) -> datetime                                                            # deterministic clock
    async def gather(self, *awaitables) -> list[dict]
```

Rules for workflow code (enforced by review and by the replay tests):

1. No I/O, randomness or wall-clock reads — use activities and `ctx.now()`.
2. Every activity/signal/timer/child gets a **stable key** (`key=f"run:{run_id}"`, `key=f"cp:{phase}"`, ...). The
   key is the memoization identity; keys must not depend on non-deterministic data.
3. Activities must be idempotent on retry (they receive the attempt number; database writes use natural keys
   or `ON CONFLICT`).
4. Business failures surface as `ActivityFailure` after retries and may be caught by the workflow (for example
   to run failure analysis and retry with a new experiment version).

Retries: `RetryPolicy(max_attempts=3, initial_interval_seconds=2, backoff=2, max_interval_seconds=60)` by default.
`workflows.runtime.classify` decides retryability: authorization/validation/policy errors, constraint or
trigger violations and model policy/auth errors are **never retried**; transient model, storage, sandbox and
connection errors are.

## Human-in-the-loop

- `gate(ctx, mission_id, action)` asks the policy engine + autonomy rules whether an action may proceed. When a
  human is required it creates an **approval** (idempotent per resource and run), emits `APPROVAL_REQUESTED`
  and waits for the signal `approval:<id>`.
- `wait_for_approval` waits on the signal with a timeout and falls back to polling the approval's status, so
  a lost signal can never strand a workflow. Approvals expire (default 7 days); expiry also signals.
- `checkpoint(ctx, mission_id)` is called between phases: paused missions wait for `mission.resume`; cancelled
  missions stop (`WorkflowCancelled`) and child runs are cancelled with them. Autonomy changes take effect at
  the next checkpoint.

## Engines

Both engines run the same definitions; `WORKFLOW_ENGINE` (or the presence of `TEMPORAL_ADDRESS`) selects one.
`lab.workflow_runs` is the system of record for status and results on either engine.

### Temporal (production)

- Each definition becomes a Temporal workflow type of the same name (`LabWorkflow_<name>`, unsandboxed; all I/O
  is in activities). Workflow id: `lab-<workflow_run_id>`.
- One generic activity, `lab_activity(name, payload)`, dispatches to the activity registry; one generic signal,
  `lab_signal(name, payload)`, feeds `wait_signal`. Activity retry policies map to Temporal retry policies;
  non-retryable failures are raised as non-retryable `ApplicationError`s.
- The workflow marks its `workflow_runs` row running/completed/failed/cancelled through the `workflow.mark`
  activity. Child workflows are created through `workflow.create_child` (so the child row exists before the
  Temporal child starts).
- Starts, signals and cancels are sent **after commit**; if Temporal is unreachable, the scheduler's
  reconciler retries runs that never reached Temporal and signals that were never delivered.
- The worker (`python -m aegis_api.processes.temporal_worker`) retries its initial connection with backoff and
  drains in-flight activities on SIGTERM (60 s graceful shutdown).

### Inline (tests, single node)

- A replaying engine backed by `lab.workflow_steps`: each completed activity/signal/timer/child result is
  memoized by key; driving a run re-executes the workflow function and replays memoized results until it
  reaches new work.
- Driving requires a **lease** (`lease_owner`, `lease_until`, refreshed by a heartbeat thread); a crashed driver's
  lease expires and another worker resumes by replay. `drive()` returns `busy` when another holder has the
  lease.
- Waiting runs record `waiting_on` (`signal:<name>`, `timer`, `child:<id>`) and `wake_at`; `poll_due()` finds
  runs that are due (timer elapsed, signal arrived, child finished) with an index scan.
- `python -m aegis_api.processes.worker` polls and drives runs in a bounded thread pool; the API also dispatches
  a drive after commit so short workflows start immediately.

## Verified behaviour

| Test | What it proves |
| --- | --- |
| `tests/integration/lab/test_workflow_engine.py` | replay without duplicate side effects, signals, timeouts, timers, transient retries vs permanent failures, child waits, cancellation, lease exclusion |
| `tests/integration/lab/test_temporal.py` | the same semantics on a real Temporal server, including the complete mission loop |
| `tests/integration/lab/test_mission_e2e.py` | the complete mission loop through the HTTP API on the inline engine with real Docker sandboxes |
