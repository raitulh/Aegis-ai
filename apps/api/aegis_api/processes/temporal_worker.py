"""Temporal worker for the lab workflows and the generic ``lab_activity``.

python -m aegis_api.processes.temporal_worker
"""

from __future__ import annotations

import asyncio

from aegis_api.processes.common import setup
from aegis_api.workflows.temporal import run_worker


def main() -> None:
    setup("temporal-worker")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
