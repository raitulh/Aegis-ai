"""Knowledge graph (PostgreSQL adjacency tables; the query surface is small enough that a graph database is
not required — see docs/scientific-memory.md for the extraction boundary).

Nodes are keyed per organization by (node_type, key) so repeated extraction is idempotent; edges by
(source, target, relation). Every edge records who created it (system | agent | user) and supporting evidence.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.models.lab import GraphEdge, GraphNode

NODE_TYPES = frozenset(
    {
        "paper",
        "document",
        "concept",
        "method",
        "dataset",
        "metric",
        "hypothesis",
        "experiment",
        "run",
        "result",
        "failure",
        "strategy",
        "claim",
        "discovery",
        "evidence",
        "entity",
        "mission",
    }
)
RELATIONS = frozenset(
    {
        "supports",
        "contradicts",
        "extends",
        "uses",
        "tests",
        "generated_by",
        "failed_because_of",
        "evolved_from",
        "reproduces",
        "verified_by",
        "derived_from",
        "cites",
        "mentions",
        "part_of",
        "evaluated_by",
    }
)
MAX_DEPTH = 3
MAX_NODES = 500


def _key(value: str) -> str:
    return " ".join(value.strip().lower().split())[:255]


def upsert_node(
    db: Session,
    *,
    organization_id: uuid.UUID,
    node_type: str,
    key: str,
    label: str,
    project_id: uuid.UUID | None = None,
    ref_type: str | None = None,
    ref_id: str | uuid.UUID | None = None,
    properties: dict[str, Any] | None = None,
) -> GraphNode:
    if node_type not in NODE_TYPES:
        raise ValidationFailed(f"unknown node type '{node_type}'")
    ins = insert(GraphNode).values(
        id=uuid.uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        node_type=node_type,
        key=_key(key),
        label=label[:300],
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id else None,
        properties=properties or {},
    )
    stmt = ins.on_conflict_do_update(
        constraint="uq_lab_graph_nodes_type_key",
        set_={"label": ins.excluded.label, "updated_at": func.now()},
    ).returning(GraphNode.id)
    node_id = db.execute(stmt).scalar_one()
    node = db.get(GraphNode, node_id)
    assert node is not None
    return node


def add_edge(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source: GraphNode,
    target: GraphNode,
    relation: str,
    created_by: str = "system",
    confidence: float = 1.0,
    evidence_ids: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    if relation not in RELATIONS:
        raise ValidationFailed(f"unknown relation '{relation}'")
    if source.organization_id != organization_id or target.organization_id != organization_id:
        raise ValidationFailed("graph edges cannot cross organizations")
    db.execute(
        insert(GraphEdge)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            source_id=source.id,
            target_id=target.id,
            relation=relation,
            confidence=max(0.0, min(1.0, float(confidence))),
            created_by=created_by,
            evidence_ids=list(evidence_ids or []),
            properties=properties or {},
        )
        .on_conflict_do_nothing(constraint="uq_lab_graph_edges_triple")
    )


def link_refs(
    db: Session,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID | None,
    source: tuple[str, str, str],  # (node_type, ref_id, label)
    target: tuple[str, str, str],
    relation: str,
    created_by: str = "system",
    confidence: float = 1.0,
    evidence_ids: list[str] | None = None,
) -> None:
    """Convenience: upsert two platform-object nodes (keyed by their ids) and connect them."""
    s = upsert_node(
        db,
        organization_id=organization_id,
        project_id=project_id,
        node_type=source[0],
        key=f"{source[0]}:{source[1]}",
        label=source[2],
        ref_type=source[0],
        ref_id=source[1],
    )
    t = upsert_node(
        db,
        organization_id=organization_id,
        project_id=project_id,
        node_type=target[0],
        key=f"{target[0]}:{target[1]}",
        label=target[2],
        ref_type=target[0],
        ref_id=target[1],
    )
    add_edge(
        db,
        organization_id=organization_id,
        source=s,
        target=t,
        relation=relation,
        created_by=created_by,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )


def neighborhood(
    db: Session,
    organization_id: uuid.UUID,
    node_id: uuid.UUID,
    *,
    depth: int = 1,
    relations: list[str] | None = None,
    project_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    depth = max(1, min(depth, MAX_DEPTH))
    seen: set[uuid.UUID] = {node_id}
    frontier = {node_id}
    edges: dict[uuid.UUID, GraphEdge] = {}
    for _ in range(depth):
        if not frontier or len(seen) >= MAX_NODES:
            break
        stmt = select(GraphEdge).where(
            GraphEdge.organization_id == organization_id,
            or_(GraphEdge.source_id.in_(frontier), GraphEdge.target_id.in_(frontier)),
        )
        if relations:
            stmt = stmt.where(GraphEdge.relation.in_(relations))
        found = db.scalars(stmt.limit(MAX_NODES)).all()
        nxt: set[uuid.UUID] = set()
        for e in found:
            edges[e.id] = e
            for n in (e.source_id, e.target_id):
                if n not in seen:
                    nxt.add(n)
        seen |= nxt
        frontier = nxt
    nodes = db.scalars(
        select(GraphNode).where(GraphNode.organization_id == organization_id, GraphNode.id.in_(seen))
    ).all()
    visible = {n.id for n in nodes if project_ids is None or n.project_id is None or n.project_id in set(project_ids)}
    return {
        "nodes": [node_dict(n) for n in nodes if n.id in visible],
        "edges": [edge_dict(e) for e in edges.values() if e.source_id in visible and e.target_id in visible],
    }


def find_nodes(
    db: Session,
    organization_id: uuid.UUID,
    *,
    query: str | None = None,
    node_type: str | None = None,
    ref: tuple[str, str] | None = None,
    project_ids: list[uuid.UUID] | None = None,
    limit: int = 50,
) -> list[GraphNode]:
    stmt = select(GraphNode).where(GraphNode.organization_id == organization_id)
    if node_type:
        stmt = stmt.where(GraphNode.node_type == node_type)
    if ref:
        stmt = stmt.where(GraphNode.ref_type == ref[0], GraphNode.ref_id == ref[1])
    if query:
        stmt = stmt.where(GraphNode.label.ilike(f"%{query.replace('%', '').replace('_', '')[:100]}%"))
    if project_ids is not None:
        stmt = stmt.where(or_(GraphNode.project_id.is_(None), GraphNode.project_id.in_(project_ids)))
    return list(db.scalars(stmt.order_by(GraphNode.updated_at.desc()).limit(max(1, min(limit, 200)))).all())


def node_dict(n: GraphNode) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "type": n.node_type,
        "key": n.key,
        "label": n.label,
        "ref_type": n.ref_type,
        "ref_id": n.ref_id,
        "project_id": str(n.project_id) if n.project_id else None,
        "properties": n.properties,
    }


def edge_dict(e: GraphEdge) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "source": str(e.source_id),
        "target": str(e.target_id),
        "relation": e.relation,
        "confidence": e.confidence,
        "created_by": e.created_by,
        "evidence_ids": e.evidence_ids,
    }
