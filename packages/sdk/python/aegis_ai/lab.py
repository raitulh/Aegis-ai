"""Scientist Lab resources: ``client.lab.missions``, ``client.lab.experiments``, ``client.lab.claims`` ...

Example
-------
    from aegis_ai import Aegis

    with Aegis(api_key="aegis_...", base_url="https://api.aegis.example") as client:
        mission = client.lab.missions.create(
            project_id=pid,
            title="Annealing vs random search",
            objective="Determine whether simulated annealing beats random search on 5-D Rastrigin.",
        )
        client.lab.missions.launch(mission["id"], idempotency_key="launch-2026-09-30")
        for event in client.lab.missions.stream_events(mission["id"]):
            print(event["id"], event["event_type"], event["message"])
        done = client.lab.missions.get(mission["id"])

API keys act as automation: they can create missions (up to autonomy L1) and launch them, but autonomy
changes, approvals, discovery review and strategy promotion require an interactive human user.
"""

from __future__ import annotations

import json as _json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis_ai.models import Page

if TYPE_CHECKING:
    from aegis_ai.client import _Transport

JsonList = list[dict[str, Any]]
StrList = list[str]  # module-scope alias: resource classes define ``list`` methods that shadow the builtin
TERMINAL_MISSION = frozenset({"completed", "failed", "cancelled", "archived"})


def _parse(lines: StrList) -> dict[str, Any] | None:
    if not lines:
        return None
    try:
        value = _json.loads("\n".join(lines))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _key(idempotency_key: str | None) -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key or f"sdk-{uuid.uuid4().hex}"}


class _LabResource:
    def __init__(self, transport: _Transport) -> None:
        self._t = transport

    def _page(self, path: str, params: dict[str, Any]) -> Page:
        return Page.from_response(self._t.request("GET", path, params=params))


class Missions(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, project_id: str | None = None, status: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/missions", {"page": page, "page_size": page_size, "project_id": project_id, "status": status}
        )

    def create(self, *, project_id: str, title: str, objective: str, **fields: Any) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/missions",
            json={"project_id": project_id, "title": title, "objective": objective, **fields},
        )

    def get(self, mission_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}")

    def update(self, mission_id: str, *, lock_version: int | None = None, **fields: Any) -> dict[str, Any]:
        """Edit a draft/planned/paused mission. Pass ``lock_version`` for optimistic concurrency (409 if stale)."""
        headers = {"If-Match": str(lock_version)} if lock_version is not None else None
        return self._t.request("PATCH", f"/api/v1/missions/{mission_id}", json=fields, headers=headers)

    def launch(self, mission_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/missions/{mission_id}/launch", headers=_key(idempotency_key))

    def pause(self, mission_id: str, reason: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/missions/{mission_id}/pause", json={"reason": reason})

    def resume(self, mission_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/missions/{mission_id}/resume")

    def cancel(self, mission_id: str, reason: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/missions/{mission_id}/cancel", json={"reason": reason})

    def versions(self, mission_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}/versions")

    def events(self, mission_id: str, *, after: int | None = None, limit: int = 200) -> JsonList:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}/events", params={"after": after, "limit": limit})

    def stream_events(self, mission_id: str, *, last_event_id: int | None = None) -> Iterator[dict[str, Any]]:
        """Server-Sent Events; resumes after ``last_event_id`` and stops when the mission is terminal."""
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        event_name, data = "", []
        for line in self._t.stream_lines(f"/api/v1/missions/{mission_id}/events/stream", headers=headers):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "":
                if event_name == "stream_end":
                    return
                payload = _parse(data)
                if payload is not None:
                    yield payload
                event_name, data = "", []

    def wait(self, mission_id: str, *, timeout: float = 3600.0, poll_seconds: float = 3.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            mission = self.get(mission_id)
            if mission["status"] in TERMINAL_MISSION or time.monotonic() >= deadline:
                return mission
            time.sleep(poll_seconds)

    def observability(self, mission_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}/observability")

    def evidence(self, mission_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}/evidence")

    def verify_evidence(self, mission_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/missions/{mission_id}/evidence/verify")

    def generate_report(self, mission_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/missions/{mission_id}/reports", headers=_key(idempotency_key))


class Hypotheses(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, project_id: str | None = None, mission_id: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/hypotheses",
            {"page": page, "page_size": page_size, "project_id": project_id, "mission_id": mission_id},
        )

    def create(
        self, *, project_id: str, statement: str, measurable_prediction: dict[str, Any], **fields: Any
    ) -> dict[str, Any]:
        body = {"project_id": project_id, "statement": statement, "measurable_prediction": measurable_prediction}
        return self._t.request("POST", "/api/v1/hypotheses", json={**body, **fields})

    def get(self, hypothesis_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/hypotheses/{hypothesis_id}")


class Experiments(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, project_id: str | None = None, mission_id: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/experiments",
            {"page": page, "page_size": page_size, "project_id": project_id, "mission_id": mission_id},
        )

    def contract(self) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/experiments/contract")

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self._t.request("POST", "/api/v1/experiments/validate", json=spec)

    def create(self, *, project_id: str, title: str, spec: dict[str, Any], **fields: Any) -> dict[str, Any]:
        return self._t.request(
            "POST", "/api/v1/experiments", json={"project_id": project_id, "title": title, "spec": spec, **fields}
        )

    def get(self, experiment_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/experiments/{experiment_id}")

    def attach_code(
        self, experiment_id: str, *, files: dict[str, str], entrypoint: StrList, notes: str = ""
    ) -> dict[str, Any]:
        body = {
            "files": [{"path": p, "content": c} for p, c in files.items()],
            "entrypoint": entrypoint,
            "notes": notes,
        }
        return self._t.request("POST", f"/api/v1/experiments/{experiment_id}/code", json=body)

    def execute(self, experiment_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/experiments/{experiment_id}/execute", headers=_key(idempotency_key))

    def runs(self, experiment_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/experiments/{experiment_id}/runs")

    def evaluations(self, experiment_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/experiments/{experiment_id}/evaluations")

    def comparisons(self, experiment_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/experiments/{experiment_id}/comparisons")

    def reproducibility_package(self, experiment_id: str, destination: str | Path | None = None) -> bytes:
        data: bytes = self._t.request("GET", f"/api/v1/experiments/{experiment_id}/reproducibility-package", raw=True)
        if destination is not None:
            Path(destination).write_bytes(data)
        return data

    def run_manifest(self, run_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/experiment-runs/{run_id}/manifest")

    def replay_run(self, run_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/experiment-runs/{run_id}/replay", headers=_key(idempotency_key))


class Claims(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, mission_id: str | None = None, status: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/claims", {"page": page, "page_size": page_size, "mission_id": mission_id, "status": status}
        )

    def get(self, claim_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/claims/{claim_id}")

    def lineage(self, claim_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/claims/{claim_id}/lineage")

    def verify(
        self, claim_id: str, *, criteria_overrides: dict[str, Any] | None = None, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/api/v1/claims/{claim_id}/verify",
            json={"criteria_overrides": criteria_overrides},
            headers=_key(idempotency_key),
        )


class Discoveries(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, mission_id: str | None = None, status: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/discoveries", {"page": page, "page_size": page_size, "mission_id": mission_id, "status": status}
        )

    def get(self, discovery_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/discoveries/{discovery_id}")


class Approvals(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, status: str | None = "pending", mission_id: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/approvals", {"page": page, "page_size": page_size, "status": status, "mission_id": mission_id}
        )

    def get(self, approval_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/approvals/{approval_id}")


class Research(_LabResource):
    def create(
        self,
        *,
        project_id: str,
        title: str,
        question: str,
        mode: str = "literature_pipeline",
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        body = {"project_id": project_id, "title": title, "question": question, "mode": mode, **fields}
        return self._t.request("POST", "/api/v1/research", json=body, headers=_key(idempotency_key))

    def get(self, task_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/research/{task_id}")

    def events(self, task_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/research/{task_id}/events")

    def report(self, task_id: str) -> Any:
        return self._t.request("GET", f"/api/v1/research/{task_id}/report")

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/research/{task_id}/cancel")


class Memory(_LabResource):
    def search(self, query: str, *, project_id: str | None = None, limit: int = 10, **filters: Any) -> JsonList:
        body = {"query": query, "project_id": project_id, "limit": limit, **filters}
        return self._t.request("POST", "/api/v1/memory/search", json=body)

    def propose(self, *, scope: str, content: str, **fields: Any) -> dict[str, Any]:
        """Automation-proposed durable memories (project/organization) wait for human review."""
        return self._t.request("POST", "/api/v1/memory", json={"scope": scope, "content": content, **fields})


class Knowledge(_LabResource):
    def search(self, query: str, *, project_id: str | None = None, limit: int = 10) -> Any:
        return self._t.request(
            "POST", "/api/v1/knowledge/search", json={"query": query, "project_id": project_id, "limit": limit}
        )

    def add_url(self, *, project_id: str, url: str, title: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "POST", "/api/v1/knowledge/urls", json={"project_id": project_id, "url": url, "title": title}
        )

    def upload(self, *, project_id: str, path: str | Path, title: str | None = None) -> dict[str, Any]:
        file = Path(path)
        with file.open("rb") as handle:
            return self._t.request(
                "POST",
                "/api/v1/knowledge/documents",
                data={"project_id": project_id, **({"title": title} if title else {})},
                files={"file": (file.name, handle)},
            )


class Artifacts(_LabResource):
    def list(
        self, *, page: int = 1, page_size: int = 25, project_id: str | None = None, mission_id: str | None = None
    ) -> Page:
        return self._page(
            "/api/v1/artifacts",
            {"page": page, "page_size": page_size, "project_id": project_id, "mission_id": mission_id},
        )

    def upload(self, *, project_id: str, path: str | Path, kind: str = "upload") -> dict[str, Any]:
        file = Path(path)
        with file.open("rb") as handle:
            return self._t.request(
                "POST",
                "/api/v1/artifacts",
                data={"project_id": project_id, "kind": kind},
                files={"file": (file.name, handle)},
            )

    def download(self, artifact_id: str, destination: str | Path | None = None, *, version: int | None = None) -> bytes:
        """Streams through the API; if the server answers with a presigned redirect, follow it yourself."""
        params = {"version": version} if version is not None else None
        data: bytes = self._t.request("GET", f"/api/v1/artifacts/{artifact_id}/download", params=params, raw=True)
        if destination is not None:
            Path(destination).write_bytes(data)
        return data


class Strategies(_LabResource):
    def list(self, *, page: int = 1, page_size: int = 25, project_id: str | None = None) -> Page:
        return self._page("/api/v1/strategies", {"page": page, "page_size": page_size, "project_id": project_id})

    def create(
        self, *, name: str, definition: dict[str, Any], project_id: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        body = {"name": name, "definition": definition, "project_id": project_id, "description": description}
        return self._t.request("POST", "/api/v1/strategies", json=body)

    def get(self, strategy_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/strategies/{strategy_id}")

    def new_version(
        self, strategy_id: str, *, definition: dict[str, Any], parent_version_id: str | None = None
    ) -> dict[str, Any]:
        body = {"definition": definition, "parent_version_id": parent_version_id}
        return self._t.request("POST", f"/api/v1/strategies/{strategy_id}/versions", json=body)

    def promotion_check(self, strategy_id: str, version_id: str) -> dict[str, Any]:
        return self._t.request(
            "GET", f"/api/v1/strategies/{strategy_id}/promotion-check", params={"version_id": version_id}
        )

    def evolve(
        self, *, strategy_id: str, mode: str = "benchmark", idempotency_key: str | None = None, **fields: Any
    ) -> dict[str, Any]:
        body = {"strategy_id": strategy_id, "mode": mode, **fields}
        return self._t.request("POST", "/api/v1/evolution-runs", json=body, headers=_key(idempotency_key))

    def evolution_run(self, run_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/evolution-runs/{run_id}")


class Reports(_LabResource):
    def list(self, *, page: int = 1, page_size: int = 25, mission_id: str | None = None) -> Page:
        return self._page("/api/v1/research-reports", {"page": page, "page_size": page_size, "mission_id": mission_id})

    def get(self, report_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/research-reports/{report_id}")


class Lab:
    """Namespace for the AI Scientist Evolution Lab API."""

    def __init__(self, transport: _Transport) -> None:
        self._t = transport
        self.missions = Missions(transport)
        self.hypotheses = Hypotheses(transport)
        self.experiments = Experiments(transport)
        self.claims = Claims(transport)
        self.discoveries = Discoveries(transport)
        self.approvals = Approvals(transport)
        self.research = Research(transport)
        self.memory = Memory(transport)
        self.knowledge = Knowledge(transport)
        self.artifacts = Artifacts(transport)
        self.strategies = Strategies(transport)
        self.reports = Reports(transport)

    def usage(self, *, mission_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/usage", params={"mission_id": mission_id, "project_id": project_id})

    def workflow_run(self, run_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/workflow-runs/{run_id}")
