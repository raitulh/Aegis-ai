# Evaluation engines

The engines are where Aegis decides whether an AI system's behaviour is acceptable. They live under `engines/`, are **pure Python** (no database or framework imports), and follow one principle above all others:

> **Deterministic where possible, model-assisted where useful, evidence-backed everywhere.**

A verdict must be reproducible from its inputs. A model may *assist* a judgment (e.g. paraphrase detection, semantic similarity), but it is never the *sole* basis for a finding — every failing verdict pairs a model signal with a deterministic one, and both are recorded in evidence.

## Anatomy of an evaluator

Each evaluator takes plain inputs (the test case, the system's output, and any reference material) and returns a result with, at minimum:

- a **verdict** (`pass` / `fail`, sometimes `inconclusive`),
- a **confidence** in `[0, 1]`,
- **reasons** — the deterministic signals that drove the verdict,
- the **evidence** payload to be hash-chained.

Because engines are pure, the same call runs identically inside an audit, inside the worker, inside monitoring, and inside a unit test.

## Dimensions

### Hallucination / grounding (`engines/hallucination`)
Decomposes an answer into atomic **claims** (`claims.py`), resolves pronoun subjects to their antecedents, then checks each claim against the provided sources. Support and contradiction are tracked **separately** (`verification.py`): a claim is `supported` when its best supporting overlap clears a threshold, `contradicted` when a source refutes it, and `unverifiable` when neither the source set nor the claim's own subject can ground it. This separation avoids the classic failure of treating "no support" as "contradiction".

### Fairness / bias (`engines/fairness`)
Two complementary methods:

- **Counterfactual** (`counterfactual.py`) — hold the scenario fixed, vary a protected attribute, and measure the change in outcome. A single case is weak evidence, so per-case magnitude is combined with a **finding-level** aggregation (`aggregate()`) that pools across cases for a proper p-value. A case fails when the magnitude is meaningful *and* (statistically significant *or* strongly large), or when the decision outright flips for a majority of variants.
- **Group parity** — compares outcome rates across groups against configured parity thresholds.

### Safety (`engines/safety`)
Checks whether the system refuses or safely handles disallowed requests and whether outputs contain unsafe content, using deterministic detectors backed by optional model assistance.

### Privacy / PII (`engines/privacy`)
Detects personal data in outputs. Input **echo** is excluded by default (`allow_user_echo=False`) so that merely repeating a value the user supplied isn't scored as a leak — a real leak is the system surfacing PII it was not given in that turn.

### Security (`engines/security`)
Prompt-injection and jailbreak *resistance*. This engine evaluates how the system responds to an **imported** probe corpus; it contains **no built-in attack payloads or mutation strategies** (see [`security.md`](security.md)).

### Agent trace auditing (`engines/agent`)
Walks an agent's execution trace span by span: which tools were called, with what arguments, whether calls that require human approval got it, and whether sensitive data crossed a tool boundary. Violations attach to the spans and tool calls that caused them.

## Model assistance & the router

`engines/providers` exposes a `ModelRouter` that can call Ollama, Gemini, OpenAI, or Anthropic when a model judgment adds value. Everything degrades gracefully: with no provider configured, evaluators fall back to their deterministic paths and a **local hash embedder** stands in for embeddings, so the platform is fully functional offline. When a model *is* used, its output is one signal among several and is written into evidence alongside the deterministic signals.

## Cost

Token cost is **only** reported when a price is explicitly configured (`MODEL_PRICING_JSON`). Aegis never invents or estimates prices.

## Confidence & risk

An evaluator's confidence feeds risk scoring (`engines/risk`), which combines severity, occurrence count, sample size, and dimension into a finding-level risk score and level. Low-N results are deliberately conservative — a single anomalous case does not become a high-confidence finding on its own.

## Testing the engines

- `tests/unit` — evaluator logic on small, hand-checked inputs.
- `tests/evaluation` — correctness on labelled fixtures, including **remediation before/after** deltas that prove a fix actually changes measured behaviour.

Because the engines are pure, these tests need no database and run fast.
