"""Claim extraction, independent verification and machine-readable lineage.

Claims are extracted deterministically from measured comparisons (never from free text) and start
UNVERIFIED → CANDIDATE. A verification runs the configured checks — each recorded as a ``verification_runs``
row with its evidence — and ``ClaimVerifier.decide`` derives the claim status from the checks alone:

* ``evaluator_passed`` / ``statistically_supported`` — platform evaluator verdicts;
* ``baseline_validated`` — baseline runs exist, completed, with enough seeds and a real configuration diff;
* ``replicated`` — fresh reproduction runs (new seeds, same code/data/environment) agree within tolerance;
* ``independent_evaluation`` — evaluators re-run from the raw metric rows with an independent resampling seed
  and output checksums re-verified against object storage;
* ``provenance_complete`` — lineage nodes present, artifact checksums intact, evidence hash chain valid and the
  reproducibility manifest complete;
* ``non_self_reported_metrics`` — every sample was measured by a platform harness.

A model-assisted verifier assessment may be recorded as an *additional* check; it is never a required check
and never the sole basis for any status.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import InvalidState, NotFound
from aegis_api.infrastructure.observability import metrics as prom
from aegis_api.models import Evidence, Project
from aegis_api.models.lab import (
    AgentRun,
    Artifact,
    ArtifactVersion,
    ClaimEvidence,
    EvaluationRun,
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    ExperimentVersion,
    ScientificClaim,
    Verification,
    VerificationRun,
)
from aegis_api.security.context import Principal
from aegis_api.services.lab import artifacts as artifact_service
from aegis_api.services.lab import events, evidence, graph
from aegis_api.services.lab.access import accessible_project_ids, get_scoped
from aegis_api.services.lab.common import Actor
from engines.lab.enums import ClaimStatus, ExperimentStatus, LabEventType, RunKind
from engines.lab.evaluation.base import EvaluationContext
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.reproducibility import ReproducibilityManifest, completeness
from engines.lab.state_machines import CLAIM, EXPERIMENT
from engines.lab.verification.claims import ClaimExtractor, overclaiming_terms
from engines.lab.verification.criteria import CheckResult, ClaimVerifier, VerificationCriteria, criteria_for
from engines.lab.verification.evidence import EvidenceValidator

REPRO_SEED_OFFSET = 10_007
MIN_MANIFEST_COMPLETENESS = 0.8


# --- claims --------------------------------------------------------------------------------------------------


def extract_claims(
    organization_id: uuid.UUID,
    experiment_id: uuid.UUID,
    evaluation: dict[str, Any],
    *,
    actor: Actor,
) -> list[str]:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = ExperimentSpec.model_validate(version.spec)
        extracted = ClaimExtractor().extract(
            evaluation.get("comparisons") or {},
            evaluation.get("statistical_rows") or [],
            experiment_title=experiment.title,
            experiment_id=str(experiment.id),
            baseline_name=spec.baseline.name if spec.baseline else "baseline",
            dataset_label=spec.dataset.dataset_version_id if spec.dataset else None,
        )
        eval_evidence = [e["evidence_id"] for e in evaluation.get("evaluations", []) if e.get("evidence_id")]
        ids: list[str] = []
        for c in extracted:
            if overclaiming_terms(c.statement):
                continue  # deterministic statements never overclaim; guard kept for defense in depth
            claim = db.scalar(
                select(ScientificClaim).where(
                    ScientificClaim.organization_id == organization_id, ScientificClaim.fingerprint == c.fingerprint
                )
            )
            if claim is None:
                claim = ScientificClaim(
                    organization_id=organization_id,
                    project_id=experiment.project_id,
                    mission_id=experiment.mission_id,
                    experiment_id=experiment.id,
                    hypothesis_id=experiment.hypothesis_id,
                    statement=c.statement,
                    claim_type=c.claim_type,
                    metric=c.metric,
                    source="deterministic",
                    status=ClaimStatus.UNVERIFIED,
                    confidence=0.0,
                    fingerprint=c.fingerprint,
                    scope=c.scope,
                    details=c.to_dict(),
                    uncertainty={"ci": list(c.ci) if c.ci else None, "p_value": c.p_value, "n": c.n_candidate},
                )
                db.add(claim)
                db.flush()
            for ev in eval_evidence:
                _link_evidence(db, claim, uuid.UUID(ev), "supports")
            if eval_evidence and claim.status == ClaimStatus.UNVERIFIED:
                claim.status = CLAIM.ensure(claim.status, ClaimStatus.CANDIDATE)
            graph.link_refs(
                db,
                organization_id=organization_id,
                project_id=experiment.project_id,
                source=("claim", str(claim.id), c.statement[:200]),
                target=("experiment", str(experiment.id), experiment.title),
                relation="derived_from",
            )
            ids.append(str(claim.id))
        return ids


def _link_evidence(
    db: Session, claim: ScientificClaim, evidence_id: uuid.UUID, relation: str, note: str | None = None
) -> None:
    exists = db.scalar(
        select(ClaimEvidence.id).where(
            ClaimEvidence.claim_id == claim.id,
            ClaimEvidence.evidence_id == evidence_id,
            ClaimEvidence.relation == relation,
        )
    )
    if exists is None:
        db.add(
            ClaimEvidence(
                organization_id=claim.organization_id,
                claim_id=claim.id,
                evidence_id=evidence_id,
                relation=relation,
                note=note,
            )
        )


def create_manual_claim(db: Session, principal: Principal, data: dict[str, Any]) -> ScientificClaim:
    principal.require("verification:run")
    from aegis_api.services.lab.access import get_project
    from aegis_api.services.lab.common import sha256_json

    project = get_project(db, principal, data["project_id"])
    flags = overclaiming_terms(data["statement"])
    claim = ScientificClaim(
        organization_id=principal.organization_id,
        project_id=project.id,
        mission_id=data.get("mission_id"),
        experiment_id=data.get("experiment_id"),
        statement=data["statement"],
        claim_type=data.get("claim_type") or "assertion",
        metric=data.get("metric"),
        source="human",
        status=ClaimStatus.UNVERIFIED,
        fingerprint=sha256_json({"statement": data["statement"], "project": str(project.id)})[:32],
        details={"overclaiming_terms": flags},
    )
    db.add(claim)
    db.flush()
    return claim


# --- verification --------------------------------------------------------------------------------------------


def start(
    db: Session, principal: Principal, claim_id: uuid.UUID | str, *, criteria_overrides: dict[str, Any] | None = None
) -> Verification:
    from aegis_api.workflows import client as workflow_client

    principal.require("verification:run")
    claim = get_scoped(db, principal, ScientificClaim, claim_id, label="Claim")
    if claim.experiment_id is None:
        raise InvalidState("Only experiment-backed claims can be verified automatically")
    verification = create_verification(db, claim, requested_by=principal.actor_label, overrides=criteria_overrides)
    run = workflow_client.start(
        db,
        organization_id=claim.organization_id,
        workflow="verification",
        business_key=f"verification:{verification.id}",
        payload={"verification_id": str(verification.id)},
        principal=principal,
    )
    verification.workflow_run_id = run.id
    return verification


def create_verification(
    db: Session, claim: ScientificClaim, *, requested_by: str, overrides: dict[str, Any] | None = None
) -> Verification:
    project = db.get(Project, claim.project_id)
    base = dict(project.verification_criteria or {}) if project else {}
    domain = str(base.get("domain") or (project.domain if project else "default"))
    criteria = criteria_for(domain, {**{k: v for k, v in base.items() if k != "domain"}, **(overrides or {})})
    verification = Verification(
        organization_id=claim.organization_id,
        claim_id=claim.id,
        project_id=claim.project_id,
        criteria=criteria.model_dump(),
        status="pending",
        requested_by=requested_by[:160],
    )
    db.add(verification)
    db.flush()
    return verification


def _record_check(
    db: Session,
    verification: Verification,
    result: CheckResult,
    *,
    verifier: str,
    independent: bool,
    data: dict[str, Any] | None = None,
) -> None:
    row = db.scalar(
        select(VerificationRun).where(
            VerificationRun.verification_id == verification.id, VerificationRun.check_name == result.name
        )
    )
    if row is not None:
        return  # immutable; first result stands (replay-safe)
    db.add(
        VerificationRun(
            organization_id=verification.organization_id,
            verification_id=verification.id,
            check_name=result.name,
            passed=result.passed,
            contradicts=result.contradicts,
            detail=result.detail[:4000],
            verifier=verifier,
            independent=independent,
            evidence_ids=result.evidence_ids,
            data=data or {},
        )
    )


def _context(
    db: Session, verification_id: uuid.UUID
) -> tuple[Verification, ScientificClaim, Experiment, ExperimentVersion, ExperimentSpec]:
    verification = db.get(Verification, verification_id)
    if verification is None:
        raise NotFound("Verification not found")
    claim = db.get(ScientificClaim, verification.claim_id)
    assert claim is not None and claim.experiment_id is not None
    experiment = db.get(Experiment, claim.experiment_id)
    assert experiment is not None
    version = db.get(ExperimentVersion, experiment.current_version_id)
    assert version is not None
    return verification, claim, experiment, version, ExperimentSpec.model_validate(version.spec)


def begin(organization_id: uuid.UUID, verification_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        verification, claim, experiment, _version, spec = _context(db, verification_id)
        if verification.status == "pending":
            verification.status = "running"
        if experiment.status == ExperimentStatus.EVALUATING:
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.REPRODUCING)
        if claim.mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=claim.mission_id,
                project_id=claim.project_id,
                event_type=LabEventType.VERIFICATION_STARTED,
                message=f"Verifying claim: {claim.statement[:200]}",
                data={
                    "claim_id": str(claim.id),
                    "verification_id": str(verification.id),
                    "criteria": verification.criteria,
                },
                actor=actor,
            )
        criteria = VerificationCriteria.model_validate(verification.criteria)
        return {
            "experiment_id": str(experiment.id),
            "claim_id": str(claim.id),
            "min_reproductions": max(criteria.min_reproductions, spec.reproducibility.min_reproductions)
            if "replicated" in criteria.required_checks
            else 0,
        }


def plan_reproductions(organization_id: uuid.UUID, verification_id: uuid.UUID, count: int) -> list[str]:
    """Reproduction runs: candidate configuration, *new* seeds, same code/data/environment/harness."""
    with session_scope(organization_id) as db:
        verification, _claim, experiment, version, _spec = _context(db, verification_id)
        originals = list(
            db.scalars(
                select(ExperimentRun)
                .where(
                    ExperimentRun.experiment_version_id == version.id,
                    ExperimentRun.run_kind == RunKind.CANDIDATE,
                    ExperimentRun.status == "completed",
                )
                .order_by(ExperimentRun.seed)
            ).all()
        )
        if not originals:
            return []
        ids: list[str] = []
        for i in range(count):
            original = originals[i % len(originals)]
            seed = REPRO_SEED_OFFSET + original.seed + i
            key = f"{verification.id}:reproduction:{i}"
            existing = db.scalar(
                select(ExperimentRun.id).where(
                    ExperimentRun.organization_id == organization_id, ExperimentRun.idempotency_key == key
                )
            )
            if existing:
                ids.append(str(existing))
                continue
            run = ExperimentRun(
                organization_id=organization_id,
                project_id=experiment.project_id,
                mission_id=experiment.mission_id,
                experiment_id=experiment.id,
                experiment_version_id=version.id,
                run_kind=RunKind.REPRODUCTION,
                variant="reproduction",
                seed=seed,
                parameters=dict(original.parameters or {}),
                status="queued",
                reproduction_of_run_id=original.id,
                idempotency_key=key,
            )
            db.add(run)
            db.flush()
            ids.append(str(run.id))
        return ids


def run_checks(
    organization_id: uuid.UUID, verification_id: uuid.UUID, *, reproduction_run_ids: list[str], actor: Actor
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        verification, claim, experiment, version, spec = _context(db, verification_id)
        criteria = VerificationCriteria.model_validate(verification.criteria)
        runs = list(db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_version_id == version.id)).all())
        baseline = [r for r in runs if r.run_kind == RunKind.BASELINE and r.status == "completed"]
        candidate = [r for r in runs if r.run_kind == RunKind.CANDIDATE and r.status == "completed"]
        repro_ids = {uuid.UUID(i) for i in reproduction_run_ids}
        repro = [r for r in runs if r.id in repro_ids]
        evals = {
            e.evaluator_key: e
            for e in db.scalars(
                select(EvaluationRun)
                .where(EvaluationRun.experiment_version_id == version.id, EvaluationRun.independent.is_(False))
                .order_by(EvaluationRun.created_at)
            ).all()
        }
        checks: dict[str, CheckResult] = {}
        metric = claim.metric or (spec.primary_metric.name if spec.primary_metric else None)

        # evaluator_passed
        bench, met = evals.get("benchmark"), evals.get("metric")
        verdicts = [e for e in (bench, met) if e is not None]
        checks["evaluator_passed"] = CheckResult(
            "evaluator_passed",
            None if not verdicts else all(e.passed is not False for e in verdicts) and any(e.passed for e in verdicts),
            "; ".join(f"{e.evaluator_key}={e.verdict}" for e in verdicts) or "no evaluation recorded",
            [str(e.evidence_id) for e in verdicts if e.evidence_id],
        )
        # statistically_supported
        stat = evals.get("statistical")
        row = next((r for r in (stat.details.get("rows") if stat else []) or [] if r.get("metric") == metric), None)
        if stat is None or row is None or row.get("insufficient"):
            checks["statistically_supported"] = CheckResult(
                "statistically_supported", None, "insufficient samples or no test"
            )
        else:
            ok = bool(row.get("supports_improvement"))
            checks["statistically_supported"] = CheckResult(
                "statistically_supported",
                ok,
                f"{row.get('test')} p_adj={row.get('p_adjusted')} g={row.get('effect_size_g')} (alpha {criteria.alpha})",
                [str(stat.evidence_id)] if stat.evidence_id else [],
                contradicts=bool(row.get("significant")) and not ok,
            )
        # baseline_validated
        diff = {
            k
            for k in set(spec.baseline.parameters if spec.baseline else {}) | set(spec.parameters)
            if (spec.baseline.parameters if spec.baseline else {}).get(k) != spec.parameters.get(k)
        }
        baseline_ok = spec.baseline is not None and len(baseline) >= spec.statistical_plan.min_seeds and bool(diff)
        checks["baseline_validated"] = CheckResult(
            "baseline_validated",
            baseline_ok,
            f"{len(baseline)} completed baseline run(s); configuration differs in {sorted(diff)[:6]}"
            if spec.baseline
            else "no baseline declared",
        )
        # replicated
        repro_done = [r for r in repro if r.status == "completed"]
        repro_passed = repro_failed = 0
        if repro and metric:
            ctx = EvaluationContext(
                candidate=_samples(candidate),
                reproduction=_samples(repro_done),
                config={"tolerance": spec.reproducibility.relative_tolerance},
            )
            rres = DEFAULT_REGISTRY.get("reproduction").evaluate(spec, {}, ctx)
            crashed = len(repro) - len(repro_done)  # a reproduction that cannot even run counts as failed
            if rres.passed is True:
                repro_passed, repro_failed = len(repro_done), crashed
            elif rres.passed is False:
                repro_passed, repro_failed = 0, len(repro)
            else:
                repro_passed, repro_failed = 0, crashed
            record = evidence.seal(
                db,
                organization_id=organization_id,
                mission_id=claim.mission_id,
                project_id=claim.project_id,
                kind="reproduction",
                title=f"Reproduction ({len(repro_done)}/{len(repro)} runs): {rres.verdict}",
                content={
                    "claim_id": str(claim.id),
                    "result": rres.model_dump(mode="json"),
                    "runs": [str(r.id) for r in repro],
                },
                confidence=rres.confidence,
            )
            checks["replicated"] = CheckResult(
                "replicated",
                rres.passed,
                f"{rres.verdict}: " + "; ".join(rres.warnings[:3]),
                [str(record.id)],
                contradicts=rres.passed is False,
            )
        else:
            checks["replicated"] = CheckResult("replicated", None, "no reproduction runs executed")
        # independent_evaluation: recompute from raw metric rows with an independent resampling seed
        metric_rows = list(
            db.scalars(
                select(ExperimentMetric).where(
                    ExperimentMetric.experiment_id == experiment.id,
                    ExperimentMetric.run_id.in_([r.id for r in baseline + candidate]),
                )
            ).all()
        )
        raw: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
        for m in metric_rows:
            if m.source in ("harness", "self_reported"):
                raw[m.run_id][m.name] = m.value
        ictx = EvaluationContext(
            baseline=_samples_from(raw, baseline),
            candidate=_samples_from(raw, candidate),
            self_reported=any(r.self_reported for r in baseline + candidate),
            seed=REPRO_SEED_OFFSET + 1,
        )
        ibench = DEFAULT_REGISTRY.get("benchmark").evaluate(spec, {}, ictx)
        tampered = []
        for r in candidate:
            for out in (r.manifest or {}).get("outputs", []):
                aid = out.get("artifact_id")
                artifact = db.get(Artifact, uuid.UUID(aid)) if aid else None
                av = (
                    db.get(ArtifactVersion, artifact.current_version_id)
                    if artifact and artifact.current_version_id
                    else None
                )
                if av is not None and av.purged_at is None:
                    recorded, actual = artifact_service.verify_checksum(av)
                    if actual is not None and recorded != actual:
                        tampered.append(aid)
        agrees = (bench is None or ibench.passed == bench.passed) and not tampered
        irec = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=claim.mission_id,
            project_id=claim.project_id,
            kind="independent_evaluation",
            title=f"Independent re-evaluation: {ibench.verdict}" + (" (checksum mismatch!)" if tampered else ""),
            content={
                "claim_id": str(claim.id),
                "result": ibench.model_dump(mode="json"),
                "tampered_artifacts": tampered,
            },
            confidence=ibench.confidence,
        )
        db.add(
            EvaluationRun(
                organization_id=organization_id,
                project_id=experiment.project_id,
                experiment_id=experiment.id,
                experiment_version_id=version.id,
                run_ids=sorted(str(r.id) for r in baseline + candidate),
                evaluator_key=ibench.evaluator_key,
                evaluator_version=ibench.evaluator_version,
                evaluator_fingerprint=ibench.fingerprint,
                verdict=ibench.verdict,
                passed=ibench.passed,
                confidence=ibench.confidence,
                metrics=ibench.metrics,
                warnings=ibench.warnings,
                evidence=ibench.evidence,
                details=ibench.details,
                evidence_id=irec.id,
                independent=True,
                evaluated_by="verification",
            )
        )
        checks["independent_evaluation"] = CheckResult(
            "independent_evaluation",
            bool(agrees and ibench.passed) if ibench.passed is not None else None,
            f"independent verdict {ibench.verdict}; agrees with original: {agrees}; tampered: {len(tampered)}",
            [str(irec.id)],
            contradicts=bool(tampered) or (bench is not None and ibench.passed is False and bench.passed is True),
        )
        # provenance_complete
        prov = provenance(db, claim, experiment, version, candidate)
        checks["provenance_complete"] = CheckResult(
            "provenance_complete",
            prov["ok"],
            f"lineage missing {prov['missing']}; problems {prov['problems'][:3]}; manifest completeness {prov['completeness']}",
            [],
            contradicts=bool(prov["integrity_problems"]),
        )
        # non_self_reported_metrics
        self_rep = any(r.self_reported for r in baseline + candidate)
        checks["non_self_reported_metrics"] = CheckResult(
            "non_self_reported_metrics",
            not self_rep if (baseline or candidate) else None,
            "all samples measured by the platform harness" if not self_rep else "self-reported metrics present",
        )
        for result in checks.values():
            _record_check(
                db,
                verification,
                result,
                verifier="platform",
                independent=result.name in ("replicated", "independent_evaluation", "provenance_complete"),
            )
        return {
            "checks": {
                k: {"passed": v.passed, "contradicts": v.contradicts, "detail": v.detail} for k, v in checks.items()
            },
            "reproductions_passed": repro_passed,
            "reproductions_failed": repro_failed,
        }


def _samples(runs: list[ExperimentRun]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in sorted(runs, key=lambda x: (x.seed, str(x.id))):
        for k, v in (r.metrics or {}).items():
            if isinstance(v, int | float):
                out[k].append(float(v))
    return dict(out)


def _samples_from(raw: dict[uuid.UUID, dict[str, float]], runs: list[ExperimentRun]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in sorted(runs, key=lambda x: (x.seed, str(x.id))):
        for k, v in raw.get(r.id, {}).items():
            out[k].append(float(v))
    return dict(out)


def provenance(
    db: Session,
    claim: ScientificClaim,
    experiment: Experiment,
    version: ExperimentVersion,
    candidate: list[ExperimentRun],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"type": "experiment", "id": str(experiment.id)}]
    checksums: dict[str, tuple[str | None, str | None]] = {}
    scores: list[float] = []
    for r in candidate[:10]:
        manifest = ReproducibilityManifest.model_validate(r.manifest) if r.manifest else None
        nodes.append({"type": "experiment_run", "id": str(r.id)})
        if manifest is None:
            continue
        nodes.append({"type": "code_snapshot", "id": manifest.code.artifact_id, "sha256": manifest.code.sha256})
        nodes.append(
            {
                "type": "environment",
                "id": manifest.environment.environment_id,
                "image": manifest.environment.image,
                "image_digest": manifest.environment.image_digest,
            }
        )
        if manifest.dataset_version_id:
            nodes.append({"type": "dataset_version", "id": manifest.dataset_version_id})
        for out in manifest.outputs:
            nodes.append({"type": "raw_artifact", "id": out.artifact_id, "sha256": out.sha256})
            if out.artifact_id:
                artifact = db.get(Artifact, uuid.UUID(out.artifact_id))
                av = (
                    db.get(ArtifactVersion, artifact.current_version_id)
                    if artifact and artifact.current_version_id
                    else None
                )
                checksums[out.artifact_id] = (out.sha256, av.sha256 if av else None)
        score, _missing = completeness(
            manifest,
            requires_dataset=bool(manifest.dataset_version_id),
            llm_generated_code=manifest.generated_by is not None,
        )
        scores.append(score)
    for e in db.scalars(select(EvaluationRun).where(EvaluationRun.experiment_version_id == version.id)).all():
        nodes.append({"type": "evaluation", "id": str(e.id), "evaluator_fingerprint": e.evaluator_fingerprint})
    scope = evidence.scope_for(mission_id=claim.mission_id, project_id=claim.project_id)
    chain = [
        {"content_hash": r.content_hash, "chain_hash": r.chain_hash, "prev_hash": r.prev_hash}
        for r in evidence.chain(db, claim.organization_id, scope)
    ]
    validation = EvidenceValidator(require_dataset=any(n["type"] == "dataset_version" for n in nodes)).validate(
        nodes, artifact_checksums=checksums, evidence_chain=chain
    )
    manifest_score = round(min(scores), 4) if scores else 0.0
    integrity = [p for p in validation.problems if "mismatch" in p or "chain" in p]
    return {
        "ok": validation.complete and validation.integrity_ok and manifest_score >= MIN_MANIFEST_COMPLETENESS,
        "missing": validation.missing,
        "problems": validation.problems,
        "integrity_problems": integrity,
        "completeness": manifest_score,
    }


def record_model_assessment(
    organization_id: uuid.UUID, verification_id: uuid.UUID, assessment: dict[str, Any], agent_run_id: str
) -> None:
    """Model-assisted assessment: stored as an extra, non-required check (never decisive on its own)."""
    with session_scope(organization_id) as db:
        verification = db.get(Verification, verification_id)
        if verification is None:
            return
        label = assessment.get("assessment")
        _record_check(
            db,
            verification,
            CheckResult(
                "model_assessment",
                True if label == "supports" else False if label == "contradicts" else None,
                "; ".join(assessment.get("reasons", [])[:5]),
            ),
            verifier=f"agent_run:{agent_run_id}",
            independent=False,
            data={"confidence": assessment.get("confidence"), "source": "model_assisted"},
        )


def decide(
    organization_id: uuid.UUID,
    verification_id: uuid.UUID,
    *,
    reproductions_passed: int,
    reproductions_failed: int,
    actor: Actor,
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        verification, claim, experiment, _version, _spec = _context(db, verification_id)
        if verification.status == "completed":
            return {"status": verification.resulting_status, "decision": verification.decision}
        criteria = VerificationCriteria.model_validate(verification.criteria)
        rows = db.scalars(select(VerificationRun).where(VerificationRun.verification_id == verification.id)).all()
        checks = {
            r.check_name: CheckResult(r.check_name, r.passed, r.detail or "", list(r.evidence_ids or []), r.contradicts)
            for r in rows
            if r.check_name in criteria.required_checks
        }
        decision = ClaimVerifier(criteria).decide(
            checks, reproductions_passed=reproductions_passed, reproductions_failed=reproductions_failed
        )
        before = claim.status
        if claim.status == ClaimStatus.UNVERIFIED and decision.status != ClaimStatus.REJECTED:
            claim.status = CLAIM.ensure(claim.status, ClaimStatus.CANDIDATE)
        if claim.status != decision.status:
            claim.status = CLAIM.ensure(claim.status, decision.status)
        claim.confidence = decision.confidence
        if decision.status == ClaimStatus.VERIFIED:
            claim.verified_at = utcnow()
        record = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=claim.mission_id,
            project_id=claim.project_id,
            kind="verification",
            title=f"Claim {decision.status}: {claim.statement[:200]}",
            content={
                "claim_id": str(claim.id),
                "verification_id": str(verification.id),
                "criteria": verification.criteria,
                "decision": decision.to_dict(),
                "checks": {
                    k: {"passed": v.passed, "detail": v.detail, "evidence": v.evidence_ids} for k, v in checks.items()
                },
            },
            confidence=decision.confidence,
        )
        _link_evidence(db, claim, record.id, "supports" if decision.status == ClaimStatus.VERIFIED else "context")
        verification.status = "completed"
        verification.decision = decision.to_dict()
        verification.resulting_status = str(decision.status)
        verification.confidence = decision.confidence
        verification.reproductions_passed = reproductions_passed
        verification.reproductions_failed = reproductions_failed
        verification.evidence_id = record.id
        verification.completed_at = utcnow()
        if experiment.status == ExperimentStatus.REPRODUCING:
            experiment.status = EXPERIMENT.ensure(
                experiment.status,
                ExperimentStatus.VERIFIED
                if decision.status == ClaimStatus.VERIFIED
                else ExperimentStatus.REJECTED
                if decision.status == ClaimStatus.REJECTED
                else ExperimentStatus.EVALUATING,
            )
        prom.VERIFICATION_DURATION.labels(outcome=str(decision.status)).observe(
            (utcnow() - verification.created_at).total_seconds() if verification.created_at else 0.0
        )
        if claim.mission_id or claim.project_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=claim.mission_id,
                project_id=claim.project_id,
                event_type=LabEventType.VERIFICATION_COMPLETED,
                message=f"Claim {decision.status} (confidence {decision.confidence}): {decision.rationale}",
                data={
                    "claim_id": str(claim.id),
                    "verification_id": str(verification.id),
                    "status": str(decision.status),
                    "before": before,
                    "evidence_id": str(record.id),
                },
                actor=actor,
            )
        return {"status": str(decision.status), "decision": decision.to_dict(), "claim_id": str(claim.id)}


def fail(organization_id: uuid.UUID, verification_id: uuid.UUID, error: str) -> None:
    with session_scope(organization_id) as db:
        verification = db.get(Verification, verification_id)
        if verification is not None and verification.status != "completed":
            verification.status = "failed"
            verification.decision = {"error": error[:2000]}
            verification.completed_at = utcnow()


# --- queries -------------------------------------------------------------------------------------------------


def list_claims(
    db: Session,
    principal: Principal,
    *,
    mission_id: uuid.UUID | None = None,
    status: str | None = None,
    project_id: uuid.UUID | None = None,
) -> Select[ScientificClaim]:
    stmt = select(ScientificClaim).where(ScientificClaim.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(ScientificClaim.project_id.in_(visible))
    if mission_id:
        stmt = stmt.where(ScientificClaim.mission_id == mission_id)
    if project_id:
        stmt = stmt.where(ScientificClaim.project_id == project_id)
    if status:
        stmt = stmt.where(ScientificClaim.status == status)
    return stmt.order_by(ScientificClaim.created_at.desc())


def verification_detail(db: Session, verification: Verification) -> dict[str, Any]:
    rows = db.scalars(select(VerificationRun).where(VerificationRun.verification_id == verification.id)).all()
    return {
        "id": str(verification.id),
        "claim_id": str(verification.claim_id),
        "status": verification.status,
        "criteria": verification.criteria,
        "decision": verification.decision,
        "resulting_status": verification.resulting_status,
        "confidence": verification.confidence,
        "reproductions": {"passed": verification.reproductions_passed, "failed": verification.reproductions_failed},
        "evidence_id": str(verification.evidence_id) if verification.evidence_id else None,
        "checks": [
            {
                "check": r.check_name,
                "passed": r.passed,
                "contradicts": r.contradicts,
                "detail": r.detail,
                "verifier": r.verifier,
                "independent": r.independent,
                "evidence_ids": r.evidence_ids,
            }
            for r in rows
        ],
        "workflow_run_id": str(verification.workflow_run_id) if verification.workflow_run_id else None,
        "completed_at": verification.completed_at.isoformat() if verification.completed_at else None,
    }


def lineage(db: Session, claim: ScientificClaim) -> dict[str, Any]:
    """Machine-readable provenance: Claim → Evidence → Verification → Evaluation → Experiment → ExperimentRun
    → CodeSnapshot → DatasetVersion → ModelVersion → Environment → RawArtifact."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def node(node_type: str, ident: str | None, **attrs: Any) -> str | None:
        if not ident:
            return None
        key = f"{node_type}:{ident}"
        nodes.setdefault(
            key, {"id": key, "type": node_type, "ref": ident, **{k: v for k, v in attrs.items() if v is not None}}
        )
        return key

    def edge(src: str | None, dst: str | None, rel: str) -> None:
        if src and dst:
            edges.append({"from": src, "to": dst, "relation": rel})

    c = node(
        "claim",
        str(claim.id),
        statement=claim.statement,
        status=claim.status,
        confidence=claim.confidence,
        source=claim.source,
    )
    for link in db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id)).all():
        ev = db.get(Evidence, link.evidence_id)
        e = node(
            "evidence",
            str(link.evidence_id),
            evidence_kind=ev.kind if ev else None,
            content_hash=ev.content_hash if ev else None,
            chain_hash=ev.chain_hash if ev else None,
        )
        edge(c, e, link.relation)
    for v in db.scalars(select(Verification).where(Verification.claim_id == claim.id)).all():
        vn = node(
            "verification", str(v.id), status=v.status, resulting_status=v.resulting_status, confidence=v.confidence
        )
        edge(c, vn, "verified_by")
    if claim.experiment_id:
        experiment = db.get(Experiment, claim.experiment_id)
        if experiment is not None:
            x = node("experiment", str(experiment.id), title=experiment.title, status=experiment.status)
            edge(c, x, "derived_from")
            if experiment.hypothesis_id:
                edge(x, node("hypothesis", str(experiment.hypothesis_id)), "tests")
            for ev_run in db.scalars(select(EvaluationRun).where(EvaluationRun.experiment_id == experiment.id)).all():
                en = node(
                    "evaluation",
                    str(ev_run.id),
                    evaluator=f"{ev_run.evaluator_key}@{ev_run.evaluator_version}",
                    fingerprint=ev_run.evaluator_fingerprint,
                    verdict=ev_run.verdict,
                    independent=ev_run.independent,
                )
                edge(c, en, "supported_by" if ev_run.passed else "evaluated_by")
                edge(en, x, "evaluates")
            version = (
                db.get(ExperimentVersion, experiment.current_version_id) if experiment.current_version_id else None
            )
            if version is not None and version.generated_by_run_id:
                ar = db.get(AgentRun, version.generated_by_run_id)
                if ar is not None:
                    edge(
                        x,
                        node(
                            "model_version",
                            f"{ar.provider}:{ar.model}",
                            prompt_ref=ar.prompt_ref,
                            prompt_sha256=ar.prompt_hash,
                            agent_run_id=str(ar.id),
                        ),
                        "designed_or_coded_by",
                    )
            for run in db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_id == experiment.id)).all():
                rn = node(
                    "experiment_run",
                    str(run.id),
                    run_kind=run.run_kind,
                    seed=run.seed,
                    status=run.status,
                    manifest_sha256=run.manifest_sha256,
                )
                edge(x, rn, "has_run")
                m = run.manifest or {}
                code = m.get("code") or {}
                edge(rn, node("code_snapshot", code.get("artifact_id"), sha256=code.get("sha256")), "ran_code")
                edge(
                    rn,
                    node("dataset_version", m.get("dataset_version_id"), checksum=m.get("dataset_checksum")),
                    "used_data",
                )
                env = m.get("environment") or {}
                edge(
                    rn,
                    node(
                        "environment",
                        env.get("environment_id"),
                        image=env.get("image"),
                        image_digest=env.get("image_digest"),
                    ),
                    "ran_in",
                )
                gen = m.get("generated_by") or {}
                if gen.get("model"):
                    edge(
                        rn,
                        node(
                            "model_version",
                            f"{gen.get('provider')}:{gen.get('model')}",
                            prompt_sha256=gen.get("prompt_sha256"),
                        ),
                        "code_generated_by",
                    )
                for out in m.get("outputs") or []:
                    edge(
                        rn,
                        node("raw_artifact", out.get("artifact_id"), name=out.get("name"), sha256=out.get("sha256")),
                        "produced",
                    )
                if m.get("logs_artifact_id"):
                    edge(rn, node("raw_artifact", m["logs_artifact_id"], name="logs"), "logged")
                if run.reproduction_of_run_id:
                    edge(rn, node("experiment_run", str(run.reproduction_of_run_id)), "reproduces")
    return {"claim_id": str(claim.id), "nodes": list(nodes.values()), "edges": edges, "schema": "aegis.lineage/1.0"}
