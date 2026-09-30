# Domain model

All lab entities live in the `lab` PostgreSQL schema, carry `organization_id` (tenant) and, where they belong to a
project, `project_id`. Every status change goes through an explicit state machine
(`engines/lab/state_machines.py`, `<MACHINE>.ensure(current, target)`); transitions not listed below are
rejected. The diagrams in this document are generated from that code.

## Entity map

```mermaid
erDiagram
  ORGANIZATION ||--o{ WORKSPACE : has
  WORKSPACE ||--o{ PROJECT : groups
  PROJECT ||--o{ MISSION : runs
  MISSION ||--o{ MISSION_VERSION : "versioned definition"
  MISSION ||--o{ EVENT : "ordered stream"
  MISSION ||--o{ AGENT_RUN : "agents act"
  MISSION ||--o{ HYPOTHESIS : proposes
  HYPOTHESIS ||--o{ EXPERIMENT : "tested by"
  EXPERIMENT ||--o{ EXPERIMENT_VERSION : "immutable spec + code"
  EXPERIMENT_VERSION ||--o{ EXPERIMENT_RUN : "baseline/candidate/ablation x seeds"
  EXPERIMENT_RUN ||--o{ COMPUTE_JOB : "sandbox jobs"
  EXPERIMENT_RUN ||--o{ ARTIFACT : outputs
  EXPERIMENT ||--o{ EVALUATION_RUN : "evaluator suite"
  EXPERIMENT ||--o{ RUN_COMPARISON : statistics
  EXPERIMENT ||--o{ CLAIM : "deterministic extraction"
  CLAIM ||--o{ VERIFICATION : "checks + reproductions"
  CLAIM ||--o| DISCOVERY : "human review"
  STRATEGY ||--o{ STRATEGY_VERSION : lineage
  STRATEGY_VERSION ||--o{ STRATEGY_EVALUATION : measured
  EVOLUTION_RUN ||--o{ STRATEGY_MUTATION : proposes
  MISSION ||--o{ EVIDENCE : "hash chain"
  MISSION ||--o{ RESEARCH_REPORT : "cited [EV:id]"
  WORKFLOW_RUN ||--o{ WORKFLOW_STEP : "memoized activities"
  WORKFLOW_RUN ||--o{ WORKFLOW_SIGNAL : signals
```

## Core concepts

| Concept | Meaning | Key invariants |
| --- | --- | --- |
| **Mission** | A research objective with constraints, success criteria, budgets, allowed tools, risk and autonomy level. | Definition edits only while `draft`/`planned`/`paused`/`failed`; every edit snapshots a `MissionVersion`; optimistic locking via `lock_version`. |
| **Hypothesis** | A falsifiable statement with a *measurable prediction* (`metric`, `direction`). | Citations to unknown sources are dropped; ranking is deterministic (critique 0.6, feasibility 0.25, confidence 0.15). |
| **Experiment / version** | Spec (IO contract, metrics, seeds, baselines, ablations, harness) + code bundle. | Versions are immutable; failed experiments are retried as a *new* version. |
| **Experiment run** | One sandboxed execution (baseline, candidate or ablation × seed). | Metrics come from the harness container; `self_reported` is recorded and never trusted for claims. |
| **Evaluation run** | The evaluator suite over all runs (`resource`, `metric`, `benchmark`, `statistical`). | Welch tests with Holm-adjusted p-values; results sealed as evidence. |
| **Claim** | A statement extracted deterministically from evaluations (or entered by a human). | Wording is bounded (no overclaiming terms); status only via verification. |
| **Verification** | Seven checks plus independent reproductions. | Model assessment is recorded but never decisive. |
| **Discovery** | A verified claim routed to human review. | Human-only approval, separation of duties, policy `discovery.approve`. |
| **Strategy / version** | A parameterised way of working plus an immutable governance envelope. | Versions may never widen governance; promotion needs the statistical gate and a human. |
| **Evidence** | Append-only, hash-chained records per mission (or project) scope. | Database triggers reject UPDATE/DELETE of recorded fields. |
| **Approval** | A human decision a workflow waits on (signal-based). | Human-only, permission per kind, separation of duties, TTL expiry. |

## Lifecycles

### Mission

```mermaid
stateDiagram-v2
  [*] --> draft
  approved --> cancelled
  approved --> planned
  approved --> running
  archived --> [*]
  cancelled --> archived
  completed --> archived
  draft --> archived
  draft --> cancelled
  draft --> planned
  failed --> archived
  failed --> running
  paused --> cancelled
  paused --> failed
  paused --> running
  planned --> approved
  planned --> cancelled
  planned --> draft
  running --> cancelled
  running --> completed
  running --> failed
  running --> paused
```

### Agent run

```mermaid
stateDiagram-v2
  [*] --> created
  cancelled --> [*]
  completed --> [*]
  created --> cancelled
  created --> executing
  created --> failed
  created --> planning
  evaluating --> completed
  evaluating --> executing
  evaluating --> failed
  executing --> cancelled
  executing --> completed
  executing --> evaluating
  executing --> failed
  executing --> waiting_child
  executing --> waiting_human
  executing --> waiting_tool
  failed --> [*]
  planning --> cancelled
  planning --> executing
  planning --> failed
  planning --> waiting_human
  waiting_child --> cancelled
  waiting_child --> executing
  waiting_child --> failed
  waiting_human --> cancelled
  waiting_human --> executing
  waiting_human --> failed
  waiting_tool --> cancelled
  waiting_tool --> executing
  waiting_tool --> failed
  waiting_tool --> waiting_human
```

### Hypothesis

```mermaid
stateDiagram-v2
  [*] --> generated
  archived --> [*]
  critiqued --> archived
  critiqued --> rejected
  critiqued --> selected
  experiment_designed --> archived
  experiment_designed --> testing
  generated --> archived
  generated --> critiqued
  generated --> rejected
  inconclusive --> archived
  inconclusive --> testing
  rejected --> archived
  selected --> archived
  selected --> experiment_designed
  supported --> archived
  supported --> testing
  testing --> inconclusive
  testing --> rejected
  testing --> supported
```

### Experiment

```mermaid
stateDiagram-v2
  [*] --> draft
  archived --> [*]
  completed --> archived
  completed --> evaluating
  draft --> archived
  draft --> validating
  evaluating --> archived
  evaluating --> completed
  evaluating --> failed
  evaluating --> rejected
  evaluating --> reproducing
  evaluating --> verified
  failed --> archived
  failed --> rejected
  failed --> retrying
  queued --> archived
  queued --> failed
  queued --> running
  rejected --> archived
  reproducing --> evaluating
  reproducing --> failed
  reproducing --> rejected
  reproducing --> verified
  retrying --> failed
  retrying --> queued
  retrying --> running
  running --> completed
  running --> failed
  validating --> draft
  validating --> queued
  validating --> rejected
  verified --> archived
  verified --> reproducing
```

### Experiment run / compute job

```mermaid
stateDiagram-v2
  [*] --> queued
  cancelled --> [*]
  failed --> [*]
  paused --> cancelled
  paused --> running
  provisioning --> cancelled
  provisioning --> failed
  provisioning --> running
  provisioning --> timed_out
  queued --> cancelled
  queued --> failed
  queued --> provisioning
  running --> cancelled
  running --> failed
  running --> paused
  running --> succeeded
  running --> timed_out
  succeeded --> verification_pending
  timed_out --> [*]
  verification_pending --> failed
  verification_pending --> verified
  verified --> [*]
```

### Claim

```mermaid
stateDiagram-v2
  [*] --> unverified
  candidate --> contested
  candidate --> partially_verified
  candidate --> rejected
  candidate --> verified
  contested --> candidate
  contested --> partially_verified
  contested --> rejected
  contested --> verified
  partially_verified --> candidate
  partially_verified --> contested
  partially_verified --> rejected
  partially_verified --> verified
  rejected --> candidate
  rejected --> contested
  unverified --> candidate
  unverified --> rejected
  verified --> contested
```

### Discovery

```mermaid
stateDiagram-v2
  [*] --> candidate
  approved --> contested
  approved --> published
  candidate --> rejected
  candidate --> verification_pending
  contested --> rejected
  contested --> verification_pending
  human_review --> approved
  human_review --> contested
  human_review --> rejected
  published --> contested
  rejected --> [*]
  verification_pending --> contested
  verification_pending --> rejected
  verification_pending --> verified
  verified --> contested
  verified --> human_review
```

### Strategy

```mermaid
stateDiagram-v2
  [*] --> candidate
  candidate --> experimental
  candidate --> retired
  experimental --> retired
  experimental --> surviving
  promoted --> retired
  promoted --> rolled_back
  retired --> [*]
  rolled_back --> experimental
  rolled_back --> retired
  surviving --> experimental
  surviving --> promoted
  surviving --> retired
```

### Approval

```mermaid
stateDiagram-v2
  [*] --> pending
  approved --> [*]
  cancelled --> [*]
  expired --> [*]
  pending --> approved
  pending --> cancelled
  pending --> expired
  pending --> rejected
  rejected --> [*]
```

### Research task

```mermaid
stateDiagram-v2
  [*] --> created
  approved --> cancelled
  approved --> running
  cancelled --> [*]
  completed --> [*]
  created --> cancelled
  created --> failed
  created --> planning
  created --> running
  failed --> [*]
  plan_review --> approved
  plan_review --> cancelled
  plan_review --> planning
  planning --> approved
  planning --> cancelled
  planning --> failed
  planning --> plan_review
  running --> cancelled
  running --> completed
  running --> failed
```
