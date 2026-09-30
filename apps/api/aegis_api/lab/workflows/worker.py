"""Temporal worker process: ``python -m aegis_api.lab.workflows.worker --queues default,execution``.

* ``default`` — every workflow kind plus the default-queue activities (``settings.temporal_task_queue``);
* ``execution`` — sandbox-execution activities (``settings.temporal_execution_task_queue``); run these on
  hosts with access to the execution backend (Docker / Kubernetes).

With the local workflow engine (Temporal not configured) workflows run through the job dispatcher (API
process threads or Celery workers) and are recovered by the scheduler, so this process refuses to start.
SIGTERM/SIGINT drain in-flight activities (graceful shutdown) before exiting. Prometheus metrics are served
on ``METRICS_PORT`` (default 9104).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import socket
from collections.abc import Sequence

import structlog

log = structlog.get_logger("aegis.lab.workflows.worker")

DEFAULT_METRICS_PORT = 9104
QUEUES = ("default", "execution")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m aegis_api.lab.workflows.worker")
    parser.add_argument("--queues", default="default,execution", help="Comma-separated: default, execution")
    parser.add_argument("--max-activities", type=int, default=None, help="Concurrent activities per queue")
    parser.add_argument("--metrics-port", type=int, default=None, help="Prometheus port (env METRICS_PORT)")
    args = parser.parse_args(argv)
    queues = [q.strip() for q in str(args.queues).split(",") if q.strip()]
    unknown = [q for q in queues if q not in QUEUES]
    if not queues or unknown:
        parser.error(f"--queues must list one or more of {', '.join(QUEUES)}")
    args.queue_list = list(dict.fromkeys(queues))
    return args


async def run_workers(queues: list[str], *, max_activities: int | None = None) -> None:
    """Connect, build one worker per queue and run until SIGTERM/SIGINT."""
    from temporalio.worker import Worker

    from aegis_api.lab.workflows.temporal_engine import build_worker, connect_client

    client = await connect_client()
    identity = f"aegis-worker@{socket.gethostname()}:{os.getpid()}"
    workers: list[Worker] = []
    for queue in queues:
        worker = build_worker(client, queue, max_concurrent_activities=max_activities, identity=identity)
        if worker is not None:
            workers.append(worker)
    if not workers:
        raise RuntimeError("Nothing to run: no workflows or activities are registered for the requested queues")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    async with contextlib.AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        log.info("temporal_workers_running", queues=queues, identity=identity)
        await stop.wait()
        log.info("temporal_workers_stopping")
    log.info("temporal_workers_stopped")


def main(argv: Sequence[str] | None = None) -> int:
    from prometheus_client import start_http_server

    from aegis_api.config import get_settings
    from aegis_api.lab.observability.metrics import REGISTRY, register_runtime_collectors
    from aegis_api.lab.observability.telemetry import configure_telemetry, shutdown_telemetry
    from aegis_api.logging import configure_logging

    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if settings.effective_workflow_engine != "temporal":
        log.error(
            "temporal_not_configured",
            detail="WORKFLOW_ENGINE=local: workflows run via the job dispatcher; set TEMPORAL_ADDRESS to use workers",
        )
        return 2
    configure_telemetry(service_name=f"{settings.otel_service_name}-worker")
    register_runtime_collectors()
    # Reading the environment directly is limited to this process entrypoint (deployment knobs).
    metrics_port = args.metrics_port or int(os.environ.get("METRICS_PORT", str(DEFAULT_METRICS_PORT)))
    start_http_server(metrics_port, registry=REGISTRY)
    try:
        asyncio.run(run_workers(args.queue_list, max_activities=args.max_activities))
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_telemetry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
