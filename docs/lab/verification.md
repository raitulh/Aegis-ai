# Verification, discoveries and reports

## From measurements to claims

After an experiment batch completes, the **EvaluationWorkflow** runs the standard evaluator suite
(`resource`, `metric`, `benchmark`, `statistical`) over all runs, computes pairwise comparisons (Welch's t-test,
Hedges' g, Holm-adjusted p-values) and seals the results as evidence. **Claims are then extracted
deterministically** from those comparisons — for example *"candidate lowers objective_value vs baseline
(Δ = −12.3, p_adj = 0.004, n = 5)"*. The claim extractor bounds the wording: overclaiming terms ("prove(s/n)",
"guarantee(s)", "definitively", "always", "breakthrough", "state-of-the-art", "certified", "compliant", ...) are
never generated and are flagged on human-entered claims. A model may write an
*interpretation*, which is stored alongside and never replaces the statistics.

## Verification

`VerificationWorkflow` (`POST /claims/{id}/verify`, or automatically inside a mission):

1. **Reproductions** — the candidate configuration is re-run with *new* seeds (offset 10007) on the same code,
   data, environment and harness.
2. **Checks** (`services/lab/verification.py::run_checks`):

| Check | Passes when |
| --- | --- |
| `evaluator_passed` | the evaluator suite passed for the experiment |
| `statistically_supported` | the claimed comparison is significant after correction (α from criteria) in the claimed direction |
| `baseline_validated` | a baseline was run and measured under the same conditions |
| `replicated` | at least `min_reproductions` reproductions agree with the claim |
| `independent_evaluation` | the platform re-derives the metric from stored outputs and it matches; artifact checksums are re-verified (tampering is detected) |
| `provenance_complete` | code, data, environment, seeds and manifests are recorded for every contributing run (EvidenceValidator) |
| `non_self_reported_metrics` | every metric behind the claim was measured by a harness, not reported by the code |

3. A **model assessment** may be recorded as an extra, *non-required* check; it can inform reviewers but can
   never flip a decision.
4. `ClaimVerifier.decide` (pure) computes the outcome from the required checks: all passed with confidence ≥
   `min_confidence` → `verified`; some passed → `partially_verified`; contradicting evidence → `contested` or
   `rejected`. Criteria have domain presets (e.g. chemistry/biology/materials require 2 reproductions and
   confidence ≥ 0.8) and may be overridden per request.

Claim lifecycle: `unverified → candidate → verified | partially_verified | rejected | contested` (contested and
partially verified claims can return to `candidate` for re-verification)
(see [domain-model.md](domain-model.md)). `GET /claims/{id}/lineage` returns the full graph: claim → evidence →
verification → evaluation → experiment → runs → code snapshot → environment → dataset → raw artifacts.

## Discoveries

A **verified** claim can become a discovery candidate (`DiscoveryWorkflow`). Discovery approval is always
human:

- policy `discovery.approve` denies non-human actors and denies approval unless the claim is `verified`;
- **separation of duties**: the person who launched the automation that produced the discovery (tracked through
  nested workflows as the delegated actor) cannot approve it; the approver needs `discovery:approve`;
- the review decides the pending approval, emits `APPROVAL_DECIDED`, and signals the waiting workflow;
- discoveries can be **contested** later with new evidence; publication is a separate human step
  (`discovery:publish`). Every state change creates an immutable `discovery_versions` row.

## Research reports

`ReportWorkflow` (`POST /missions/{id}/reports`) builds a report from **deterministic facts**: mission, hypotheses,
experiments, statistics, verification outcomes, failures and limitations, each sentence citing evidence as
`[EV:<id>]`. A model may draft a narrative, which is accepted only if:

- every citation refers to evidence in the mission's chain,
- it introduces no numbers that do not appear in the cited facts,
- it contains no overclaiming language.

Otherwise the narrative is rejected (recorded in `citation_check`) and the deterministic report stands alone.
Reports carry a fixed disclaimer, are stored as markdown artifacts, and emit `REPORT_GENERATED`.
