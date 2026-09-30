"""Knowledge graph: a :class:`GraphService` protocol and its PostgreSQL implementation.

Nodes are the 12 scientific entity types of the platform spec; edges carry one of the 11 spec relations, a
confidence and provenance. The PostgreSQL backend stores them in ``graph_nodes`` / ``graph_edges`` (RLS: tenant
isolation) and traverses with recursive CTEs:

* ``neighbors`` walks level by level with ``UNION`` over ``(node, depth)`` rows, so every level holds each
  node at most once — the walk terminates (depth ≤ 3) and never enumerates paths, even on cyclic graphs;
* ``path`` returns the shortest path (breadth-first: the recursive CTE produces rows level by level and the
  outer query stops at the first hit), with a cycle check on the path array and the same depth bound.

Every hop only enters nodes the actor may see (organization-level nodes and nodes of visible projects), so a
traversal cannot reveal restricted projects' nodes or tunnel through them. A dedicated graph database can
replace the backend behind the protocol later.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy import ARRAY, Uuid, and_, bindparam, case, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.errors import FeatureDisabled
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.models import GraphEdge, GraphNode

NODE_TYPES: tuple[str, ...] = (
    "Paper",
    "Author",
    "Claim",
    "Method",
    "Dataset",
    "Model",
    "Hypothesis",
    "Experiment",
    "Result",
    "Failure",
    "Strategy",
    "Discovery",
)
RELATIONS: tuple[str, ...] = (
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
)
NodeType = Literal[
    "Paper",
    "Author",
    "Claim",
    "Method",
    "Dataset",
    "Model",
    "Hypothesis",
    "Experiment",
    "Result",
    "Failure",
    "Strategy",
    "Discovery",
]
Relation = Literal[
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
]
Direction = Literal["out", "in", "both"]
MAX_DEPTH = 3
MAX_KEY_CHARS = 300
MAX_NATURAL_KEY_CHARS = 250
MAX_LABEL_CHARS = 1000
MAX_PROPERTIES_BYTES = 16 * 1024
DEFAULT_NEIGHBOR_LIMIT = 200
MAX_NEIGHBOR_LIMIT = 1000
MAX_EDGES = 2000


@dataclass
class Neighborhood:
    """Nodes (with their hop distance from the start set) and the edges among them."""

    nodes: list[tuple[GraphNode, int]] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


@dataclass
class GraphPath:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @property
    def length(self) -> int:
        return len(self.edges)


class GraphService(Protocol):
    """Storage-agnostic knowledge-graph operations (all scoped to one actor's visibility)."""

    def upsert_node(
        self,
        node_type: str,
        key: str,
        label: str,
        *,
        ref_type: str | None = None,
        ref_id: uuid.UUID | str | None = None,
        project_id: uuid.UUID | str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode: ...

    def link(
        self,
        src: GraphNode | uuid.UUID | str,
        dst: GraphNode | uuid.UUID | str,
        relation: str,
        *,
        confidence: float = 1.0,
        properties: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> GraphEdge: ...

    def get_node(self, node_id: uuid.UUID | str) -> GraphNode: ...

    def neighbors(
        self,
        node_id: uuid.UUID | str,
        relation: str | None = None,
        direction: Direction = "both",
        depth: int = 1,
        *,
        limit: int = DEFAULT_NEIGHBOR_LIMIT,
    ) -> Neighborhood: ...

    def subgraph(
        self, node_ids: Sequence[uuid.UUID | str], depth: int = 1, *, limit: int = DEFAULT_NEIGHBOR_LIMIT
    ) -> Neighborhood: ...

    def path(self, a: uuid.UUID | str, b: uuid.UUID | str, max_depth: int = MAX_DEPTH) -> GraphPath | None: ...


def validate_relation(relation: str) -> str:
    if relation not in RELATIONS:
        raise ValidationFailed(
            f"Unknown relation '{relation}'. Allowed: {', '.join(RELATIONS)}", code="invalid_graph_relation"
        )
    return relation


def validate_node_type(node_type: str) -> str:
    if node_type not in NODE_TYPES:
        raise ValidationFailed(
            f"Unknown node type '{node_type}'. Allowed: {', '.join(NODE_TYPES)}", code="invalid_graph_node_type"
        )
    return node_type


def _json_properties(properties: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if not properties:
        return {}
    if not isinstance(properties, dict):
        raise ValidationFailed(f"{label} must be a JSON object")
    try:
        encoded = json.dumps(properties, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{label} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_PROPERTIES_BYTES:
        raise ValidationFailed(f"{label} exceed {MAX_PROPERTIES_BYTES} bytes")
    loaded: dict[str, Any] = json.loads(encoded)
    return loaded


def _uuid(value: GraphNode | uuid.UUID | str, label: str = "Graph node") -> uuid.UUID:
    if isinstance(value, GraphNode):
        return value.id
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{label} not found") from exc


def storage_key(key: str, project_id: uuid.UUID | None) -> str:
    """Node identity within the organization: project-scoped nodes are keyed per project, so a restricted
    project's graph never merges with (or becomes visible through) another project's nodes."""
    natural = " ".join((key or "").split())
    if not natural:
        raise ValidationFailed("A graph node key is required")
    if len(natural) > MAX_NATURAL_KEY_CHARS:
        natural = "sha256:" + hashlib.sha256(natural.encode("utf-8")).hexdigest()
    stored = f"p:{project_id}:{natural}" if project_id is not None else natural
    return stored[:MAX_KEY_CHARS]


class PostgresGraphBackend:
    """:class:`GraphService` on PostgreSQL (RLS-scoped session of the calling actor)."""

    def __init__(self, db: Session, actor: Actor) -> None:
        self.db = db
        self.actor = actor
        self._visible: list[uuid.UUID] | None = None
        self._visible_loaded = False

    # -- visibility ----------------------------------------------------------------------------
    @property
    def visible_projects(self) -> list[uuid.UUID] | None:
        if not self._visible_loaded:
            self._visible = visible_project_ids(self.db, self.actor)
            self._visible_loaded = True
        return self._visible

    def node_visible_clause(self, project_col: Any) -> ColumnElement[bool]:
        visible = self.visible_projects
        if visible is None:
            return literal(True)
        return or_(project_col.is_(None), project_col.in_(visible))

    def _can_see(self, node: GraphNode) -> bool:
        if node.organization_id != self.actor.organization_id:
            return False
        visible = self.visible_projects
        return node.project_id is None or visible is None or node.project_id in visible

    def get_node(self, node_id: uuid.UUID | str) -> GraphNode:
        node = get_owned(self.db, GraphNode, node_id, self.actor, label="Graph node")
        if not self._can_see(node):
            raise NotFound("Graph node not found")
        return node

    # -- writes --------------------------------------------------------------------------------
    def upsert_node(
        self,
        node_type: str,
        key: str,
        label: str,
        *,
        ref_type: str | None = None,
        ref_id: uuid.UUID | str | None = None,
        project_id: uuid.UUID | str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        validate_node_type(node_type)
        project_uuid = load_project(self.db, self.actor, project_id).id if project_id is not None else None
        clean_label = " ".join((label or "").split())[:MAX_LABEL_CHARS] or key[:MAX_LABEL_CHARS]
        props = _json_properties(properties, "Node properties")
        ref_uuid = _uuid(ref_id, "Reference") if ref_id is not None else None
        values = {
            "id": uuid.uuid4(),
            "organization_id": self.actor.organization_id,
            "project_id": project_uuid,
            "node_type": node_type,
            "key": storage_key(key, project_uuid),
            "label": clean_label,
            "ref_type": (ref_type or None) and ref_type[:32],
            "ref_id": ref_uuid,
            "properties": props,
        }
        insert_stmt = insert(GraphNode).values(**values)
        stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_graph_nodes_type_key",
            set_={
                "label": insert_stmt.excluded.label,
                "properties": GraphNode.properties.op("||")(insert_stmt.excluded.properties),
                "ref_type": func.coalesce(insert_stmt.excluded.ref_type, GraphNode.ref_type),
                "ref_id": func.coalesce(insert_stmt.excluded.ref_id, GraphNode.ref_id),
                "updated_at": func.now(),
            },
        ).returning(GraphNode)
        node = self.db.scalars(stmt, execution_options={"populate_existing": True}).one()
        return node

    def link(
        self,
        src: GraphNode | uuid.UUID | str,
        dst: GraphNode | uuid.UUID | str,
        relation: str,
        *,
        confidence: float = 1.0,
        properties: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> GraphEdge:
        validate_relation(relation)
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValidationFailed("confidence must be between 0 and 1")
        source = src if isinstance(src, GraphNode) and self._can_see(src) else self.get_node(_uuid(src))
        target = dst if isinstance(dst, GraphNode) and self._can_see(dst) else self.get_node(_uuid(dst))
        if source.id == target.id:
            raise ValidationFailed("A graph edge must connect two different nodes")
        edge_insert = insert(GraphEdge).values(
            id=uuid.uuid4(),
            organization_id=self.actor.organization_id,
            src_id=source.id,
            dst_id=target.id,
            relation=relation,
            confidence=float(confidence),
            properties=_json_properties(properties, "Edge properties"),
            provenance=_json_properties(provenance, "Edge provenance"),
        )
        stmt = edge_insert.on_conflict_do_update(
            constraint="uq_graph_edges_triple",
            set_={
                "confidence": edge_insert.excluded.confidence,
                "properties": GraphEdge.properties.op("||")(edge_insert.excluded.properties),
                "provenance": GraphEdge.provenance.op("||")(edge_insert.excluded.provenance),
            },
        ).returning(GraphEdge)
        edge = self.db.scalars(stmt, execution_options={"populate_existing": True}).one()
        return edge

    # -- traversal -----------------------------------------------------------------------------
    @staticmethod
    def _check_depth(depth: int, *, minimum: int = 1) -> int:
        if isinstance(depth, bool) or not minimum <= int(depth) <= MAX_DEPTH:
            raise ValidationFailed(f"depth must be between {minimum} and {MAX_DEPTH}")
        return int(depth)

    def _walk(
        self,
        starts: list[uuid.UUID],
        *,
        relation: str | None,
        direction: Direction,
        depth: int,
        limit: int,
    ) -> list[tuple[uuid.UUID, int]]:
        nodes = GraphNode.__table__.alias("n")
        edges = GraphEdge.__table__.alias("e")
        anchor = select(
            GraphNode.id.label("node_id"),
            literal(0).label("depth"),
        ).where(GraphNode.id.in_(starts))
        walk = anchor.cte("walk", recursive=True)
        w = walk.alias("w")
        if direction == "out":
            join_on: ColumnElement[bool] = edges.c.src_id == w.c.node_id
            next_id: Any = edges.c.dst_id
        elif direction == "in":
            join_on = edges.c.dst_id == w.c.node_id
            next_id = edges.c.src_id
        else:
            join_on = or_(edges.c.src_id == w.c.node_id, edges.c.dst_id == w.c.node_id)
            next_id = case((edges.c.src_id == w.c.node_id, edges.c.dst_id), else_=edges.c.src_id)
        conditions: list[ColumnElement[bool]] = [
            w.c.depth < depth,
            nodes.c.organization_id == self.actor.organization_id,
            self.node_visible_clause(nodes.c.project_id),
        ]
        if relation is not None:
            conditions.append(edges.c.relation == relation)
        step = (
            select(next_id.label("node_id"), (w.c.depth + 1).label("depth"))
            .select_from(w.join(edges, join_on).join(nodes, nodes.c.id == next_id))
            .where(and_(*conditions))
        )
        walk = walk.union(step)
        min_depth = func.min(walk.c.depth)
        stmt = (
            select(walk.c.node_id, min_depth).group_by(walk.c.node_id).order_by(min_depth, walk.c.node_id).limit(limit)
        )
        return [(row[0], int(row[1])) for row in self.db.execute(stmt).all()]

    def _edges_among(self, node_ids: list[uuid.UUID], relation: str | None) -> list[GraphEdge]:
        if len(node_ids) < 2:
            return []
        stmt = select(GraphEdge).where(GraphEdge.src_id.in_(node_ids), GraphEdge.dst_id.in_(node_ids))
        if relation is not None:
            stmt = stmt.where(GraphEdge.relation == relation)
        return list(self.db.scalars(stmt.order_by(GraphEdge.created_at, GraphEdge.id).limit(MAX_EDGES)))

    def _materialize(self, found: list[tuple[uuid.UUID, int]], relation: str | None) -> Neighborhood:
        ids = [node_id for node_id, _ in found]
        rows = {n.id: n for n in self.db.scalars(select(GraphNode).where(GraphNode.id.in_(ids)))} if ids else {}
        nodes = [(rows[node_id], hops) for node_id, hops in found if node_id in rows and self._can_see(rows[node_id])]
        return Neighborhood(nodes=nodes, edges=self._edges_among([n.id for n, _ in nodes], relation))

    def neighbors(
        self,
        node_id: uuid.UUID | str,
        relation: str | None = None,
        direction: Direction = "both",
        depth: int = 1,
        *,
        limit: int = DEFAULT_NEIGHBOR_LIMIT,
    ) -> Neighborhood:
        start = self.get_node(node_id)
        if relation is not None:
            validate_relation(relation)
        if direction not in ("out", "in", "both"):
            raise ValidationFailed("direction must be one of out, in, both")
        depth = self._check_depth(depth)
        limit = max(1, min(int(limit), MAX_NEIGHBOR_LIMIT))
        found = self._walk([start.id], relation=relation, direction=direction, depth=depth, limit=limit + 1)
        return self._materialize(found[: limit + 1], relation)

    def subgraph(
        self, node_ids: Sequence[uuid.UUID | str], depth: int = 1, *, limit: int = DEFAULT_NEIGHBOR_LIMIT
    ) -> Neighborhood:
        starts = [self.get_node(n).id for n in dict.fromkeys(str(n) for n in node_ids)]
        if not starts:
            return Neighborhood()
        depth = self._check_depth(depth, minimum=0)
        limit = max(1, min(int(limit), MAX_NEIGHBOR_LIMIT))
        found = self._walk(starts, relation=None, direction="both", depth=depth, limit=limit)
        return self._materialize(found, None)

    def path(self, a: uuid.UUID | str, b: uuid.UUID | str, max_depth: int = MAX_DEPTH) -> GraphPath | None:
        start, end = self.get_node(a), self.get_node(b)
        max_depth = self._check_depth(max_depth)
        if start.id == end.id:
            return GraphPath(nodes=[start], edges=[])
        visible = self.visible_projects
        visibility = "" if visible is None else "AND (n.project_id IS NULL OR n.project_id = ANY(:pids))"
        sql = text(
            f"""
            WITH RECURSIVE walk(node_id, depth, path, edge_path) AS (
                SELECT CAST(:a AS uuid), 0, ARRAY[CAST(:a AS uuid)], ARRAY[]::uuid[]
              UNION ALL
                SELECT n.id, w.depth + 1, w.path || n.id, w.edge_path || e.id
                FROM walk w
                JOIN graph_edges e ON e.src_id = w.node_id OR e.dst_id = w.node_id
                JOIN graph_nodes n
                  ON n.id = CASE WHEN e.src_id = w.node_id THEN e.dst_id ELSE e.src_id END
                WHERE w.depth < :max_depth
                  AND w.node_id <> CAST(:b AS uuid)
                  AND NOT (n.id = ANY(w.path))
                  AND n.organization_id = CAST(:org AS uuid)
                  {visibility}
            )
            SELECT path, edge_path FROM walk WHERE node_id = CAST(:b AS uuid) LIMIT 1
            """  # noqa: S608 - only a fixed visibility fragment is interpolated; values are bound
        )
        params: dict[str, Any] = {"a": start.id, "b": end.id, "max_depth": max_depth, "org": self.actor.organization_id}
        if visible is not None:
            sql = sql.bindparams(bindparam("pids", type_=ARRAY(Uuid)))
            params["pids"] = list(visible)
        row = self.db.execute(sql, params).first()
        if row is None:
            return None
        node_ids, edge_ids = list(row[0]), list(row[1])
        node_rows = {n.id: n for n in self.db.scalars(select(GraphNode).where(GraphNode.id.in_(node_ids)))}
        edge_rows = {e.id: e for e in self.db.scalars(select(GraphEdge).where(GraphEdge.id.in_(edge_ids)))}
        return GraphPath(nodes=[node_rows[i] for i in node_ids], edges=[edge_rows[i] for i in edge_ids])


def graph_enabled(db: Session, organization_id: uuid.UUID) -> bool:
    return feature_enabled(db, organization_id, "graph_memory")


def get_graph(db: Session, actor: Actor, *, required: bool = True) -> PostgresGraphBackend | None:
    """The graph service for ``actor`` (feature flag ``graph_memory``). With ``required=False`` a disabled
    feature returns ``None`` (optional enrichment); otherwise it raises :class:`FeatureDisabled`."""
    if not graph_enabled(db, actor.organization_id):
        if required:
            raise FeatureDisabled("The 'graph_memory' feature is disabled for this organization")
        return None
    return PostgresGraphBackend(db, actor)
