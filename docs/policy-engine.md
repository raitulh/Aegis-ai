# Policy engine

The policy engine turns governance *documents* into *executable tests*. It compiles a policy or framework into a chain that preserves provenance at every hop:

```
   Document ──▶ Requirement ──▶ Control ──▶ Test ──▶ Evidence
   (page/§)      (extracted)    (executable) (cases)  (hash-chained)
```

Every requirement retains its **source page and section**, and every control links back to the requirement it came from — so a finding can always be traced to the sentence in the source document that motivated the test.

## Stages

### 1. Document
A policy is uploaded (PDF, DOCX, or text). It is stored, parsed, and split into passages that keep their location metadata.

### 2. Requirement extraction
The compiler extracts discrete, testable **requirements** from the passages. Each requirement records its text and its provenance (`requirement_key`, source page, section). Extraction is deterministic-first; model assistance may help segment prose, but the requirement always carries the verbatim source text.

### 3. Control generation
Each requirement compiles into one or more **controls** — the executable unit. A control declares:

- a **test type** (which evaluation dimension proves it — fairness, hallucination, safety, privacy, security, agent),
- a **domain** and **automation** level,
- a **severity**,
- whether it **needs human review** (ambiguous requirements are flagged rather than silently automated),
- its **source provenance** (`source: "compiled"`, back to the requirement).

### 4. Test
At audit time, controls generate concrete **test cases** for the target system (deterministic and seeded, so a re-run is comparable).

### 5. Evidence
Executing the tests produces hash-chained evidence, which rolls up into findings and, ultimately, the control's pass/fail state.

## Framework mapping (compliance)

Compiled controls map to reference frameworks — **NIST AI RMF**, **OWASP LLM Top 10**, **ISO/IEC 42001** — via `engines/compliance`. These mappings power coverage views and reports.

> Aegis reports **assessment and readiness**, never certification. The mappings show how your controls *align to* a framework's expectations; they are not a statement of legal compliance. Language throughout the product uses "assessment", "readiness", and "aligned to" — never "certified" or "compliant".

Reference framework packs ship under `database/seed` and are loaded idempotently at startup.

## Human-in-the-loop

Compilation is a draft, not an oracle. Controls flagged `needs_human_review` surface in the console for an owner to confirm, edit, or reject before they carry weight in an audit. This keeps a human accountable for what "compliant" means for their organisation.

## Re-compilation

Compiling is idempotent per policy: re-running regenerates controls from the current requirements. Provenance keys keep controls stable across recompiles where the underlying requirement is unchanged, so audit history stays coherent.

## API

- `POST /api/v1/policies` — upload a policy document.
- `POST /api/v1/policies/{id}/compile` — extract requirements and (re)generate controls; returns the requirements, controls, and a compile report.
- `GET /api/v1/policies/{id}/controls` — the compiled controls with provenance.

See [`api.md`](api.md) for conventions.
