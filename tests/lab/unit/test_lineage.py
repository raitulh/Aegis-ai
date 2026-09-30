"""Claim lineage assembly: graph structure, completeness, missing links and determinism."""

from __future__ import annotations

import json
import uuid

import pytest

from engines.lab.lineage import (
    DEFAULT_REQUIRED_CHAIN,
    LINEAGE_SCHEMA_VERSION,
    ClaimRecord,
    build_lineage,
)

CLAIM_ID = "claim-1"


def full_inputs() -> dict:
    return {
        "claim": {"id": CLAIM_ID, "statement": "Method X improves accuracy over the baseline.", "status": "CANDIDATE"},
        "evidence": [
            {"claim_id": CLAIM_ID, "evidence_type": "experiment_run", "ref_id": "run-1", "relation": "supports"},
            {"claim_id": CLAIM_ID, "evidence_type": "evaluation_run", "ref_id": "eval-1", "relation": "supports"},
        ],
        "experiments": [{"id": "exp-1", "title": "Candidate X", "created_by_agent_run_id": "agent-1"}],
        "experiment_versions": [
            {
                "id": "ver-1",
                "experiment_id": "exp-1",
                "version": 1,
                "code_snapshot_id": "code-1",
                "environment_id": "env-1",
                "dataset_version_ids": ["data-1"],
            }
        ],
        "runs": [{"id": "run-1", "experiment_version_id": "ver-1", "seed": 0, "status": "SUCCEEDED"}],
        "code_snapshots": [{"id": "code-1", "content_hash": "a" * 64, "created_by_agent_run_id": "agent-2"}],
        "dataset_versions": [{"id": "data-1", "version": 3, "content_hash": "b" * 64}],
        "environments": [{"id": "env-1", "image": "python:3.12-slim", "image_digest": "sha256:" + "c" * 64}],
        "model_provenance": [
            {
                "agent_run_id": "agent-1",
                "role": "ExperimentDesignerAgent",
                "provider": "gemini",
                "model": "reasoning-model",
                "model_version": "2026-01",
                "prompt_key": "experiment.design",
                "prompt_version": 4,
                "prompt_hash": "d" * 64,
            },
            {
                "agent_run_id": "agent-2",
                "role": "CodingAgent",
                "provider": "gemini",
                "model": "reasoning-model",
                "model_version": "2026-01",
                "prompt_key": "coding.implement",
                "prompt_version": 2,
            },
        ],
        "artifacts": [{"id": "art-1", "name": "metrics.json", "kind": "metrics", "experiment_run_id": "run-1"}],
        "evaluations": [
            {
                "id": "eval-1",
                "experiment_run_id": "run-1",
                "evaluator_key": "metric",
                "evaluator_version": "1.0.0",
                "passed": True,
            }
        ],
        "verifications": [{"id": "verif-1", "claim_id": CLAIM_ID, "status": "COMPLETED", "verdict": "VERIFIED"}],
    }


def build(inputs: dict | None = None, **kwargs):
    data = inputs or full_inputs()
    claim = data.pop("claim")
    data.update(kwargs)
    return build_lineage(claim, **data)


def edge_set(graph) -> set[tuple[str, str, str]]:
    return {(e.source, e.relation, e.target) for e in graph.edges}


def test_complete_lineage():
    graph = build()
    assert graph.schema_version == LINEAGE_SCHEMA_VERSION == "lineage-1.0"
    assert graph.claim_id == CLAIM_ID
    assert graph.completeness.complete is True
    assert graph.completeness.present == list(DEFAULT_REQUIRED_CHAIN)
    assert graph.completeness.missing == []
    assert graph.completeness.missing_links == []


def test_graph_structure():
    edges = edge_set(build())
    expected = {
        ("Claim:claim-1", "supported_by", "ExperimentRun:run-1"),
        ("Claim:claim-1", "supported_by", "Evaluation:eval-1"),
        ("Claim:claim-1", "verified_by", "Verification:verif-1"),
        ("Experiment:exp-1", "has_version", "ExperimentVersion:ver-1"),
        ("ExperimentVersion:ver-1", "has_run", "ExperimentRun:run-1"),
        ("ExperimentRun:run-1", "executed_code", "CodeSnapshot:code-1"),
        ("ExperimentRun:run-1", "used_dataset", "DatasetVersion:data-1"),
        ("ExperimentRun:run-1", "ran_in", "Environment:env-1"),
        ("ExperimentRun:run-1", "produced", "RawArtifact:art-1"),
        ("ExperimentRun:run-1", "evaluated_by", "Evaluation:eval-1"),
        ("Experiment:exp-1", "designed_by", "AgentRun:agent-1"),
        ("CodeSnapshot:code-1", "authored_by", "AgentRun:agent-2"),
        ("AgentRun:agent-1", "used_model", "ModelVersion:gemini/reasoning-model@2026-01"),
        ("AgentRun:agent-1", "used_prompt", "PromptVersion:experiment.design@4"),
    }
    assert expected <= edges


def test_nodes_are_typed_labeled_and_carry_attributes():
    graph = build()
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["Claim:claim-1"].label.startswith("Method X improves")
    assert nodes["Environment:env-1"].label.startswith("python:3.12-slim@sha256:")
    assert nodes["ModelVersion:gemini/reasoning-model@2026-01"].attrs == {
        "provider": "gemini",
        "model": "reasoning-model",
        "model_version": "2026-01",
    }
    assert nodes["PromptVersion:experiment.design@4"].attrs["prompt_hash"] == "d" * 64
    assert nodes["AgentRun:agent-1"].attrs == {"role": "ExperimentDesignerAgent"}
    assert nodes["ExperimentRun:run-1"].attrs["seed"] == 0
    assert all(node.id == f"{node.type}:{node.id.split(':', 1)[1]}" for node in graph.nodes)


def test_output_ordering_is_deterministic_and_input_order_independent():
    first = build()
    reversed_inputs = full_inputs()
    for key, value in reversed_inputs.items():
        if isinstance(value, list):
            reversed_inputs[key] = list(reversed(value))
    second = build(reversed_inputs)
    assert first == second
    assert first.digest == second.digest and len(first.digest) == 64
    types = [n.type for n in first.nodes]
    assert types[: len(DEFAULT_REQUIRED_CHAIN)] == list(DEFAULT_REQUIRED_CHAIN)
    assert [(e.source, e.relation, e.target) for e in first.edges] == sorted(edge_set(first))


def test_graph_is_json_serializable():
    payload = json.loads(json.dumps(build().model_dump()))
    assert payload["completeness"]["complete"] is True


def test_missing_code_snapshot_is_reported():
    inputs = full_inputs()
    inputs["code_snapshots"] = []
    graph = build(inputs)
    assert graph.completeness.complete is False
    assert "CodeSnapshot" in graph.completeness.missing
    links = [(m.node, m.relation, m.target_type, m.target_id, m.reason) for m in graph.completeness.missing_links]
    assert ("ExperimentRun:run-1", "executed_code", "CodeSnapshot", "code-1", "not_provided") in links
    assert ("ExperimentRun:run-1", "requires", "CodeSnapshot", None, "absent") in links


def test_run_without_environment_reference_is_absent_link():
    inputs = full_inputs()
    inputs["experiment_versions"][0]["environment_id"] = None
    inputs["environments"] = []
    graph = build(inputs)
    assert "Environment" in graph.completeness.missing
    assert any(m.target_type == "Environment" and m.reason == "absent" for m in graph.completeness.missing_links)


def test_agent_run_without_model_provenance():
    inputs = full_inputs()
    inputs["model_provenance"] = []
    graph = build(inputs)
    assert "ModelVersion" in graph.completeness.missing
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["AgentRun:agent-1"].attrs == {"model_provenance": "missing"}
    assert any(
        m.node == "AgentRun:agent-1" and m.target_type == "ModelVersion" for m in graph.completeness.missing_links
    )


def test_required_chain_can_be_relaxed():
    inputs = full_inputs()
    inputs["model_provenance"] = []
    chain = [t for t in DEFAULT_REQUIRED_CHAIN if t != "ModelVersion"]
    graph = build(inputs, required_chain=chain)
    assert graph.completeness.required_chain == chain
    assert graph.completeness.complete is True


def test_claim_without_evidence_is_incomplete():
    graph = build_lineage({"id": "lonely", "statement": "unsupported"})
    assert graph.completeness.present == ["Claim"]
    assert graph.completeness.missing == list(DEFAULT_REQUIRED_CHAIN[1:])
    assert graph.completeness.complete is False


def test_unconnected_records_do_not_count_as_present():
    inputs = full_inputs()
    inputs["evidence"] = []
    inputs["verifications"] = []
    graph = build(inputs)
    assert graph.completeness.present == ["Claim"]
    assert any(n.id == "ExperimentRun:run-1" for n in graph.nodes)


def test_dangling_evidence_reference():
    inputs = full_inputs()
    inputs["evidence"].append({"claim_id": CLAIM_ID, "evidence_type": "artifact_version", "ref_id": "ghost"})
    graph = build(inputs)
    assert any(
        (m.node, m.target_type, m.target_id, m.reason) == ("Claim:claim-1", "RawArtifact", "ghost", "not_provided")
        for m in graph.completeness.missing_links
    )
    assert graph.completeness.complete is False


def test_evidence_for_other_claims_and_other_verifications_are_ignored():
    inputs = full_inputs()
    inputs["evidence"].append({"claim_id": "other", "evidence_type": "artifact_version", "ref_id": "ghost"})
    inputs["verifications"].append({"id": "verif-2", "claim_id": "other"})
    graph = build(inputs)
    assert graph.completeness.complete is True
    assert ("Claim:claim-1", "verified_by", "Verification:verif-2") not in edge_set(graph)


def test_contradicting_and_context_evidence_relations():
    inputs = full_inputs()
    inputs["evidence"] += [
        {"claim_id": CLAIM_ID, "evidence_type": "research_source", "ref_id": "src-1", "relation": "context"},
        {
            "claim_id": CLAIM_ID,
            "evidence_type": "memory",
            "ref_id": "mem-1",
            "relation": "contradicts",
            "evidence_id": "ev-9",
        },
    ]
    graph = build(inputs)
    edges = edge_set(graph)
    assert ("Claim:claim-1", "contextualized_by", "ResearchSource:src-1") in edges
    assert ("Claim:claim-1", "contradicted_by", "Memory:mem-1") in edges
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["Memory:mem-1"].attrs["evidence_id"] == "ev-9"


def test_unknown_evidence_type_is_a_missing_link():
    inputs = full_inputs()
    inputs["evidence"].append({"claim_id": CLAIM_ID, "evidence_type": "hunch", "ref_id": "x"})
    graph = build(inputs)
    assert any(m.target_type == "hunch" for m in graph.completeness.missing_links)


def test_run_level_references_override_version_defaults():
    inputs = full_inputs()
    inputs["runs"][0].update({"environment_id": "env-2", "dataset_version_ids": ["data-2"]})
    inputs["environments"].append({"id": "env-2", "image": "python:3.12"})
    inputs["dataset_versions"].append({"id": "data-2", "version": 4, "parent_version_id": "data-1"})
    edges = edge_set(build(inputs))
    assert ("ExperimentRun:run-1", "ran_in", "Environment:env-2") in edges
    assert ("ExperimentRun:run-1", "used_dataset", "DatasetVersion:data-2") in edges
    assert ("ExperimentRun:run-1", "ran_in", "Environment:env-1") not in edges
    assert ("DatasetVersion:data-2", "derived_from", "DatasetVersion:data-1") in edges


def test_reproduction_links():
    inputs = full_inputs()
    inputs["runs"].append(
        {
            "id": "run-2",
            "experiment_version_id": "ver-1",
            "reproduction_of_run_id": "run-1",
            "artifact_version_ids": ["art-2"],
        }
    )
    inputs["artifacts"].append({"id": "art-2", "name": "metrics.json"})
    inputs["evaluations"].append({"id": "eval-2", "experiment_run_id": "run-2", "evaluator_key": "reproduction"})
    inputs["reproductions"] = [
        {
            "id": "rep-1",
            "experiment_id": "exp-1",
            "original_run_ids": ["run-1"],
            "reproduction_run_ids": ["run-2"],
            "verdict": "reproduced",
        }
    ]
    graph = build(inputs)
    edges = edge_set(graph)
    assert ("Reproduction:rep-1", "reproduces", "ExperimentRun:run-1") in edges
    assert ("Reproduction:rep-1", "reproduction_run", "ExperimentRun:run-2") in edges
    assert ("ExperimentRun:run-2", "reproduction_of", "ExperimentRun:run-1") in edges
    assert ("ExperimentRun:run-2", "produced", "RawArtifact:art-2") in edges
    assert graph.completeness.complete is True


def test_run_without_version_links_to_experiment():
    inputs = full_inputs()
    inputs["runs"][0] = {
        "id": "run-1",
        "experiment_id": "exp-1",
        "code_snapshot_id": "code-1",
        "environment_id": "env-1",
        "dataset_version_ids": ["data-1"],
    }
    inputs["experiment_versions"] = []
    graph = build(inputs)
    assert ("ExperimentRun:run-1", "run_of", "Experiment:exp-1") in edge_set(graph)
    assert graph.completeness.complete is True


def test_uuid_and_int_identifiers_are_accepted():
    claim_id = uuid.uuid4()
    run_id = uuid.uuid4()
    graph = build_lineage(
        ClaimRecord(id=claim_id, statement="s"),
        evidence=[{"claim_id": claim_id, "evidence_type": "experiment_run", "ref_id": run_id}],
        runs=[{"id": run_id, "seed": 1}],
        model_provenance=[{"agent_run_id": 7, "provider": "p", "model": "m", "prompt_version": 3}],
    )
    assert graph.claim_id == str(claim_id)
    assert f"ExperimentRun:{run_id}" in {n.id for n in graph.nodes}
    assert "AgentRun:7" in {n.id for n in graph.nodes}
    assert "ModelVersion:p/m@unversioned" in {n.id for n in graph.nodes}


def test_invalid_evidence_relation_is_rejected():
    with pytest.raises(ValueError):
        build_lineage(
            {"id": "c"},
            evidence=[{"claim_id": "c", "evidence_type": "experiment_run", "ref_id": "r", "relation": "maybe"}],
        )


def test_digest_changes_when_content_changes():
    base = build()
    inputs = full_inputs()
    inputs["environments"][0]["image_digest"] = "sha256:" + "f" * 64
    assert build(inputs).digest != base.digest
