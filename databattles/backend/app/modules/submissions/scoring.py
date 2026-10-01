"""Background scoring pipeline (runs in the worker, never in the API process).

queued → validating → scoring → scored
                    ↘ rejected (deterministic validation errors — never retried)
          ↘ failed (infrastructure errors after bounded retries)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.metrics import metrics
from app.core.time import utcnow
from app.evaluation.registry import EVALUATORS, config_hash
from app.evaluation.sandbox import SandboxError, get_sandbox
from app.jobs.queue import TransientJobError, job_handler
from app.models.competition import Competition
from app.models.enums import NotificationKind, SubmissionStatus
from app.models.submission import EvaluationAsset, Submission
from app.modules.notifications.service import notify
from app.storage import get_storage

logger = logging.getLogger("databattles.scoring")


def _copy_to(key: str, dest: str) -> None:
    storage = get_storage()
    local = storage.local_path(key)
    if local:
        shutil.copyfile(local, dest)
        return
    with storage.open(key) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


def _finish(db: Session, sub: Submission, status: str, *, code: str | None = None, message: str | None = None,
            details: list[dict[str, Any]] | None = None) -> None:
    sub.status = status
    sub.error_code = code
    sub.error_message = (message or "")[:500] or None
    sub.error_details = details or []
    sub.completed_at = utcnow()
    metrics.incr(f"submissions_{status}")


def _notify_result(db: Session, sub: Submission, comp: Competition) -> None:
    if sub.user_id is None:
        return
    if sub.status == SubmissionStatus.scored:
        metric = (comp.evaluation or {}).get("metric", "score")
        title = f"Submission scored: {metric} {sub.public_score:.5f}" if sub.public_score is not None else "Submission scored"
        notify(db, sub.user_id, NotificationKind.submission_scored, title, body=comp.title,
               link=f"/competitions/{comp.slug}/submissions", dedupe_key=f"scored:{sub.id}", group_key=f"scored:{comp.id}")
    else:
        notify(db, sub.user_id, NotificationKind.submission_rejected,
               "Submission rejected" if sub.status == SubmissionStatus.rejected else "Submission could not be scored",
               body=(sub.error_message or comp.title)[:200], link=f"/competitions/{comp.slug}/submissions",
               dedupe_key=f"result:{sub.id}", email_template="submission_result",
               email_context={"competition": comp.title, "status": sub.status, "detail": sub.error_message or ""})


@job_handler("score_submission")
def score_submission(db: Session, payload: dict[str, Any]) -> None:
    sub = db.scalar(select(Submission).where(Submission.id == uuid.UUID(payload["submission_id"])).with_for_update())
    if sub is None or sub.status not in (SubmissionStatus.queued, SubmissionStatus.validating, SubmissionStatus.scoring):
        return  # idempotent: already handled or canceled
    comp = db.get(Competition, sub.competition_id)
    assert comp is not None
    if sub.invalidated_at is not None:
        _finish(db, sub, SubmissionStatus.canceled, code="invalidated", message="Invalidated by organizers.")
        return
    asset = db.scalar(select(EvaluationAsset).where(EvaluationAsset.competition_id == comp.id, EvaluationAsset.is_active.is_(True)))
    if asset is None:
        _finish(db, sub, SubmissionStatus.failed, code="evaluation_not_configured",
                message="Scoring is not configured for this competition yet. The organizers have been notified.")
        _notify_result(db, sub, comp)
        return
    config = dict(comp.evaluation or {})
    spec = EVALUATORS[config.get("evaluator", "csv_prediction")]
    sub.status = SubmissionStatus.validating
    sub.started_at = sub.started_at or utcnow()
    sub.attempts += 1
    db.commit()

    attempt, max_attempts = payload.get("_attempt", 1), payload.get("_max_attempts", 3)
    if settings.EVALUATOR_WORKDIR_ROOT:
        os.makedirs(settings.EVALUATOR_WORKDIR_ROOT, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="dbeval-", dir=settings.EVALUATOR_WORKDIR_ROOT)
    try:
        os.chmod(workdir, 0o755)  # noqa: S103 — sandbox user must read inputs; dir is private to this job
        _copy_to(sub.storage_key, os.path.join(workdir, "submission.csv"))
        _copy_to(asset.storage_key, os.path.join(workdir, "ground_truth.csv"))
        with open(os.path.join(workdir, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({"config": config, "max_rows": settings.EVALUATOR_MAX_ROWS}, fh)
        sandbox = get_sandbox()

        result = sandbox.run(workdir, "validate")
        if result.get("fatal"):
            fatal = result["fatal"]
            logger.error("evaluation_config_error", extra={"submission_id": str(sub.id), "code": fatal.get("code")})
            _finish(db, sub, SubmissionStatus.failed, code=fatal.get("code", "evaluation_error"),
                    message="Scoring failed because of a competition configuration problem. The organizers have been notified.")
            _notify_result(db, sub, comp)
            return
        if not result.get("ok"):
            errors = result.get("errors", [])
            sub.row_count = result.get("row_count")
            first = errors[0]["message"] if errors else "The file does not match the required format."
            _finish(db, sub, SubmissionStatus.rejected, code="invalid_submission", message=first, details=errors)
            _notify_result(db, sub, comp)
            return

        sub.status = SubmissionStatus.scoring
        sub.row_count = result.get("row_count")
        db.commit()

        scored = sandbox.run(workdir, "score")
        if scored.get("fatal"):
            fatal = scored["fatal"]
            _finish(db, sub, SubmissionStatus.failed, code=fatal.get("code", "evaluation_error"), message=fatal.get("message"))
            _notify_result(db, sub, comp)
            return
        sub.public_score = scored["public"]
        sub.private_score = scored["private"]
        sub.secondary_scores = scored.get("secondary", {})
        sub.evaluator_version = spec.version
        sub.config_hash = config_hash(config, asset.sha256)
        sub.config_version = comp.config_version
        _finish(db, sub, SubmissionStatus.scored)
        if comp.evaluation_locked_at is None:
            comp.evaluation_locked_at = utcnow()
        _notify_result(db, sub, comp)
        from app.modules.credentials.badges import evaluate_user_badges

        if sub.user_id:
            evaluate_user_badges(db, sub.user_id, trigger="submission_scored")
    except SandboxError as exc:
        logger.warning("sandbox_error", extra={"submission_id": str(sub.id), "code": exc.code, "attempt": attempt})
        if attempt >= max_attempts:
            _finish(db, sub, SubmissionStatus.failed, code=f"evaluation_{exc.code}",
                    message="Scoring failed after several attempts. Check that the file is a reasonable size and try again.")
            _notify_result(db, sub, comp)
            return
        sub.status = SubmissionStatus.queued
        db.commit()
        raise TransientJobError(f"sandbox {exc.code}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
