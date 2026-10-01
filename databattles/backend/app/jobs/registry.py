"""Imports every module that registers job handlers so the worker knows all job kinds."""

from __future__ import annotations


def load_handlers() -> None:
    import app.email.sender  # noqa: F401  send_email
    import app.jobs.periodic  # noqa: F401  periodic maintenance jobs
    import app.modules.opensource.service  # noqa: F401  github_sync_repo
    import app.modules.submissions.scoring  # noqa: F401  score_submission
