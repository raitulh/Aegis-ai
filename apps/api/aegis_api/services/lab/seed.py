"""Demo seed for the Scientist Lab — clearly marked demo content, no fabricated results.

Creates (idempotently), all flagged ``is_demo`` and titled ``[DEMO]``:
* a project with an executable experiment template (random search vs. simulated annealing on 5-D Rastrigin,
  measured by the platform ``objective`` harness) whose code bundle is stored as a checksummed artifact;
* a strategy whose parameters are the annealing settings (for evolution runs);
* a deterministic *synthetic* classification dataset (hidden labels in a ``harness_only`` split);
* a mission in DRAFT.

No hypotheses, runs, metrics, claims or discoveries are seeded — those only exist once something actually runs.
"""

from __future__ import annotations

import io
import random
import uuid
from typing import Any

from sqlalchemy import select

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.models import Project
from aegis_api.models.lab import Dataset, Experiment, Mission, Strategy
from aegis_api.services.lab import datasets, experiments, workspaces
from aegis_api.services.lab.common import sha256_json
from aegis_api.services.lab.strategies import _add_version, parse_definition
from engines.lab.enums import MissionStatus

DEMO_PROJECT = "[DEMO] Optimization methods lab"

CANDIDATE_CODE = '''"""[DEMO] Random search vs. simulated annealing with restarts on 5-D Rastrigin.

Reads /workspace/input/config.json and writes /workspace/output/solution.json. The platform harness (not this
code) evaluates the solution, so this program never reports its own score.
"""

import json
import math
import pathlib
import random

cfg = json.loads(pathlib.Path("input/config.json").read_text())
params = cfg["parameters"]
rng = random.Random(int(cfg["seed"]))
dim = int(params.get("dim", 5))
budget = int(params.get("evaluations", 2000))
lo, hi = -5.12, 5.12


def rastrigin(x):
    return 10 * len(x) + sum(v * v - 10 * math.cos(2 * math.pi * v) for v in x)


best = [rng.uniform(lo, hi) for _ in range(dim)]
f_best = rastrigin(best)
if params.get("method", "random") == "random":
    for _ in range(budget - 1):
        x = [rng.uniform(lo, hi) for _ in range(dim)]
        fx = rastrigin(x)
        if fx < f_best:
            best, f_best = x, fx
else:
    step = float(params.get("step", 0.5))
    t0 = float(params.get("temperature", 10.0))
    restarts = max(1, int(params.get("restarts", 4)))
    per_restart = max(1, budget // restarts)
    for _ in range(restarts):
        x = [rng.uniform(lo, hi) for _ in range(dim)]
        fx = rastrigin(x)
        for i in range(per_restart):
            t = t0 * (1 - i / per_restart) + 1e-9
            y = [min(hi, max(lo, v + rng.gauss(0, step))) for v in x]
            fy = rastrigin(y)
            if fy < fx or rng.random() < math.exp(-(fy - fx) / t):
                x, fx = y, fy
            if fx < f_best:
                best, f_best = list(x), fx

out = pathlib.Path("output")
out.mkdir(exist_ok=True)
(out / "solution.json").write_text(json.dumps({"x": best}))
'''


def demo_spec(image: str) -> dict[str, Any]:
    return {
        "objective": "[DEMO] Does simulated annealing with restarts reach lower 5-D Rastrigin values than random search "
        "at an equal budget of 2000 evaluations?",
        "domain": "optimization",
        "baseline": {
            "name": "random search",
            "description": "Uniform random sampling within bounds, same evaluation budget",
            "parameters": {"method": "random", "evaluations": 2000, "dim": 5},
        },
        "method": "Each run uses its own seed; the harness re-evaluates the reported solution on Rastrigin.",
        "variables": [
            {"name": "method", "kind": "independent", "values": ["random", "anneal"]},
            {"name": "evaluations", "kind": "control", "values": [2000]},
            {"name": "objective_value", "kind": "dependent"},
        ],
        "controls": ["identical evaluation budget", "identical bounds", "identical harness"],
        "metrics": [
            {"name": "objective_value", "direction": "minimize", "primary": True},
            {"name": "in_bounds", "direction": "maximize"},
        ],
        "success_criteria": [
            {"metric": "objective_value", "comparator": "improves_over_baseline_by", "threshold": 0.1, "relative": True}
        ],
        "statistical_plan": {"test": "welch_t", "alpha": 0.05, "min_seeds": 5, "correction": "holm"},
        "seeds": [11, 23, 37, 41, 53],
        "ablations": [{"name": "no-restarts", "parameter_changes": {"restarts": 1}}],
        "environment": {"image": image},
        "resources": {"cpu": 1.0, "memory_mb": 256, "timeout_seconds": 120, "network": "none"},
        "reproducibility": {
            "min_reproductions": 1,
            "relative_tolerance": 0.5,
            "require_non_self_reported_metrics": True,
        },
        "harness": {"key": "objective", "config": {"function": "rastrigin", "dim": 5, "bounds": [-5.12, 5.12]}},
        "code": {"entrypoint": ["python", "code/main.py"]},
        "parameters": {
            "method": "anneal",
            "evaluations": 2000,
            "dim": 5,
            "step": 0.5,
            "temperature": 10.0,
            "restarts": 4,
        },
    }


DEMO_STRATEGY = {
    "kind": "optimization",
    "description": "[DEMO] Simulated-annealing settings for the Rastrigin template experiment",
    "parameter_space": [
        {"name": "method", "kind": "categorical", "choices": ["anneal"], "mutable": False},
        {"name": "evaluations", "kind": "int", "low": 2000, "high": 2000, "mutable": False},
        {"name": "dim", "kind": "int", "low": 5, "high": 5, "mutable": False},
        {"name": "step", "kind": "float", "low": 0.05, "high": 2.0},
        {"name": "temperature", "kind": "float", "low": 0.5, "high": 50.0, "log": True},
        {"name": "restarts", "kind": "int", "low": 1, "high": 10},
    ],
    "parameters": {"method": "anneal", "evaluations": 2000, "dim": 5, "step": 0.5, "temperature": 10.0, "restarts": 4},
    "behavior": {},
    "governance": {"tools": [], "network": "none"},
}


def _synthetic_classification(seed: int = 7, n: int = 300) -> dict[str, bytes]:
    rng = random.Random(seed)
    train, test, labels = io.StringIO(), io.StringIO(), io.StringIO()
    train.write("id,x1,x2,label\n")
    test.write("id,x1,x2\n")
    labels.write("id,label\n")
    for i in range(n):
        label = "a" if rng.random() < 0.5 else "b"
        cx = 1.0 if label == "a" else -1.0
        x1, x2 = rng.gauss(cx, 1.0), rng.gauss(-cx, 1.0)
        if i < int(n * 0.7):
            train.write(f"{i},{x1:.5f},{x2:.5f},{label}\n")
        else:
            test.write(f"{i},{x1:.5f},{x2:.5f}\n")
            labels.write(f"{i},{label}\n")
    return {
        "train.csv": train.getvalue().encode(),
        "test_features.csv": test.getvalue().encode(),
        "labels.csv": labels.getvalue().encode(),
    }


def seed_lab_demo(organization_id: uuid.UUID, owner_user_id: uuid.UUID | None) -> dict[str, Any]:
    settings = get_settings()
    out: dict[str, Any] = {}
    with session_scope(organization_id) as db:
        ws = workspaces.ensure_default_workspace(db, organization_id, owner_user_id)
        project = db.scalar(
            select(Project).where(Project.organization_id == organization_id, Project.name == DEMO_PROJECT)
        )
        if project is None:
            project = Project(
                organization_id=organization_id,
                workspace_id=ws.id,
                name=DEMO_PROJECT,
                slug="demo-optimization-lab",
                description="Demo content. Everything here is illustrative until you run it.",
                domain="optimization",
                visibility="organization",
                budget={"max_llm_tokens": 2_000_000, "max_compute_seconds": 7200},
                verification_criteria={},
                settings={},
                is_demo=True,
                created_by_id=owner_user_id,
            )
            db.add(project)
            db.flush()
        project_id = project.id
        out["project_id"] = str(project_id)
    # experiment template with code
    with session_scope(organization_id) as db:
        existing = db.scalar(
            select(Experiment).where(Experiment.project_id == project_id, Experiment.is_demo.is_(True))
        )
        if existing is not None:
            out["experiment_template_id"] = str(existing.id)
        else:
            spec, validation = experiments.validate_spec(
                db, organization_id, demo_spec(settings.execution_default_image)
            )
            experiment = Experiment(
                organization_id=organization_id,
                project_id=project_id,
                title="[DEMO] Annealing vs random search on 5-D Rastrigin",
                status="draft",
                is_demo=True,
                created_by_id=owner_user_id,
            )
            db.add(experiment)
            db.flush()
            experiments._add_version(
                db,
                experiment,
                spec.model_dump(mode="json") if spec else demo_spec(settings.execution_default_image),
                validation,
                change_note="demo template",
                created_by_id=owner_user_id,
            )
            out["experiment_template_id"] = str(experiment.id)
            out["template_valid"] = bool(validation.get("valid"))
    if "template_valid" in out:
        from aegis_api.services.lab.common import SYSTEM_ACTOR

        experiments.attach_code(
            organization_id,
            experiment_id=uuid.UUID(out["experiment_template_id"]),
            bundle={
                "files": [{"path": "main.py", "content": CANDIDATE_CODE}],
                "entrypoint": ["python", "code/main.py"],
                "notes": "demo",
            },
            agent_run_id=None,
            actor=SYSTEM_ACTOR,
        )
    # strategy
    with session_scope(organization_id) as db:
        strategy = db.scalar(
            select(Strategy).where(Strategy.organization_id == organization_id, Strategy.is_demo.is_(True))
        )
        if strategy is None:
            definition = parse_definition(DEMO_STRATEGY)
            strategy = Strategy(
                organization_id=organization_id,
                project_id=project_id,
                name="[DEMO] Annealing settings",
                kind=definition.kind.value,
                description=DEMO_STRATEGY["description"],
                status="active",
                is_demo=True,
                created_by_id=owner_user_id,
            )
            db.add(strategy)
            db.flush()
            _add_version(db, strategy, definition, parent=None, origin="human", created_by_id=owner_user_id)
        out["strategy_id"] = str(strategy.id)
    # synthetic dataset
    with session_scope(organization_id) as db:
        dataset = db.scalar(select(Dataset).where(Dataset.project_id == project_id, Dataset.is_demo.is_(True)))
        if dataset is None:
            dataset = Dataset(
                organization_id=organization_id,
                project_id=project_id,
                name="[DEMO] Synthetic two-class dataset",
                description="Deterministically generated Gaussian blobs (seed 7). Synthetic — not real-world data.",
                license="CC0-1.0",
                source="generated by the Aegis demo seed",
                is_demo=True,
                created_by_id=owner_user_id,
            )
            db.add(dataset)
            db.flush()
        dataset_id = dataset.id
        has_version = dataset.current_version_id is not None
    if not has_version:
        from aegis_api.infrastructure.storage import get_storage, object_key

        files = []
        roles = {"train.csv": "train", "test_features.csv": "test", "labels.csv": "harness_only"}
        for name, data in _synthetic_classification().items():
            key = object_key(organization_id, "datasets", str(dataset_id), sha256_json(name + data.decode())[:32])
            stored = get_storage().put_bytes(key, data, content_type="text/csv")
            files.append(
                {
                    "path": name,
                    "role": roles[name],
                    "key": key,
                    "sha256": stored.sha256,
                    "size": stored.size,
                    "content_type": "text/csv",
                }
            )
        profile = datasets.profile_files(organization_id, files)
        datasets.register_version(
            organization_id,
            dataset_id=dataset_id,
            files=files,
            profile=profile,
            parent_version_id=None,
            transformations=[{"op": "generate", "generator": "gaussian_blobs", "seed": 7, "n": 300, "split": 0.7}],
            license="CC0-1.0",
            source="generated",
            created_by_id=owner_user_id,
        )
    out["dataset_id"] = str(dataset_id)
    # mission (draft)
    with session_scope(organization_id) as db:
        mission = db.scalar(select(Mission).where(Mission.project_id == project_id, Mission.is_demo.is_(True)))
        if mission is None:
            mission = Mission(
                organization_id=organization_id,
                workspace_id=ws.id,
                project_id=project_id,
                title="[DEMO] Find annealing settings that beat random search on 5-D Rastrigin",
                objective="Identify simulated-annealing settings that reduce the mean 5-D Rastrigin value reached "
                "with 2000 evaluations by at least 10% relative to random search, verified by reproduction.",
                domain="optimization",
                constraints=["2000 objective evaluations per run", "no network", "standard library only"],
                success_criteria=[
                    {
                        "description": "≥10% relative improvement over random search, verified",
                        "metric": "objective_value",
                    }
                ],
                budget={"max_llm_tokens": 1_000_000},
                compute_budget={"max_compute_seconds": 3600},
                allowed_tools=["paper_search", "memory_search", "dataset_search"],
                risk_level="low",
                autonomy_level="L1_RESEARCH_AUTOMATION",
                config={
                    "experiment_template_id": out["experiment_template_id"],
                    "strategy_id": out["strategy_id"],
                    "hypotheses_per_cycle": 3,
                    "research_mode": "literature_pipeline",
                },
                max_cycles=1,
                status=MissionStatus.DRAFT,
                is_demo=True,
                created_by_id=owner_user_id,
            )
            db.add(mission)
            db.flush()
        out["mission_id"] = str(mission.id)
    return out
