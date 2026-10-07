"""A small in-memory directed graph with typed edges.

Nodes live in a dict keyed by id. Edges live in a list, and two adjacency
indexes (outgoing and incoming) are keyed by relationship type so that
``neighbors(node, "BUILDS_ON", "out")`` is a dict lookup, not a scan.

No third-party graph library is used on purpose: the graph is small (a few
hundred nodes) and keeping it in plain dicts makes to_json/from_json trivial
and the traversal code easy to read.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .schema import Entity, Relationship, Evidence


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Entity] = {}
        self.edges: list[Relationship] = []
        # _out[rel_type][source_id] -> list of edge indexes; _in likewise by target.
        self._out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        self._in: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        # (type, source, target) -> edge index, used to merge duplicate edges.
        self._key: dict[tuple[str, str, str], int] = {}

    # -- building ----------------------------------------------------------

    def add_node(self, entity: Entity) -> Entity:
        """Insert a node, or merge sources/attributes into an existing one."""
        existing = self.nodes.get(entity.id)
        if existing is None:
            self.nodes[entity.id] = entity
            return entity
        for src in entity.sources:
            if src not in existing.sources:
                existing.sources.append(src)
        for k, v in entity.attributes.items():
            existing.attributes.setdefault(k, v)
        return existing

    def add_edge(self, rel: Relationship) -> Relationship:
        """Insert an edge. A duplicate (type, source, target) merges its evidence
        and keeps the maximum weight, so repeated mentions never create
        parallel edges."""
        if rel.source not in self.nodes or rel.target not in self.nodes:
            raise KeyError(f"edge {rel.type} {rel.source}->{rel.target}: unknown node")
        key = (rel.type, rel.source, rel.target)
        idx = self._key.get(key)
        if idx is not None:
            existing = self.edges[idx]
            existing.weight = max(existing.weight, rel.weight)
            for ev in rel.evidence:
                if ev not in existing.evidence:
                    existing.evidence.append(ev)
            return existing
        idx = len(self.edges)
        self.edges.append(rel)
        self._key[key] = idx
        self._out[rel.type][rel.source].append(idx)
        self._in[rel.type][rel.target].append(idx)
        return rel

    # -- querying ----------------------------------------------------------

    def has(self, node_id: str) -> bool:
        return node_id in self.nodes

    def neighbors(self, node_id: str, rel_type: str, direction: str = "out"
                  ) -> list[tuple[str, Relationship]]:
        """Edges of one type touching the node.

        Returns (other_node_id, edge) pairs. direction is "out" (node is the
        source), "in" (node is the target) or "both".
        """
        result: list[tuple[str, Relationship]] = []
        if direction in ("out", "both"):
            for idx in self._out[rel_type].get(node_id, []):
                result.append((self.edges[idx].target, self.edges[idx]))
        if direction in ("in", "both"):
            for idx in self._in[rel_type].get(node_id, []):
                result.append((self.edges[idx].source, self.edges[idx]))
        return result

    def walk(self, start: str, rel_types: Iterable[str], hops: int,
             direction: str = "out") -> list[tuple[str, int, list[Relationship]]]:
        """Breadth-first walk over the given relationship types.

        Returns (node_id, hop_count, path_edges) for every node reached within
        ``hops`` hops, excluding the start node. A node is reported once, at the
        first depth it is reached.
        """
        rel_types = list(rel_types)
        seen = {start}
        frontier: list[tuple[str, list[Relationship]]] = [(start, [])]
        out: list[tuple[str, int, list[Relationship]]] = []
        for depth in range(1, hops + 1):
            nxt: list[tuple[str, list[Relationship]]] = []
            for node, path in frontier:
                for rel_type in rel_types:
                    for other, edge in self.neighbors(node, rel_type, direction):
                        if other in seen:
                            continue
                        seen.add(other)
                        new_path = path + [edge]
                        out.append((other, depth, new_path))
                        nxt.append((other, new_path))
            frontier = nxt
        return out

    def in_degree(self, node_id: str, rel_type: str | None = None) -> int:
        if rel_type is not None:
            return len(self._in[rel_type].get(node_id, []))
        return sum(len(v.get(node_id, [])) for v in self._in.values())

    def out_degree(self, node_id: str, rel_type: str | None = None) -> int:
        if rel_type is not None:
            return len(self._out[rel_type].get(node_id, []))
        return sum(len(v.get(node_id, [])) for v in self._out.values())

    def nodes_of_type(self, entity_type: str) -> list[Entity]:
        return [n for n in self.nodes.values() if n.type == entity_type]

    # -- serialisation -----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Entities grouped by type, plus the edge list. Labels and evidence
        only: no raw PEP text is stored."""
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in sorted(self.nodes.values(), key=lambda n: n.id):
            by_type[node.type].append(node.to_dict())
        edges = sorted(self.edges, key=lambda e: (e.type, e.source, e.target))
        return {"entities": dict(by_type), "relationships": [e.to_dict() for e in edges]}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Graph":
        g = cls()
        for nodes in data["entities"].values():
            for n in nodes:
                g.add_node(Entity(id=n["id"], type=n["type"], label=n["label"],
                                  sources=n.get("sources", []),
                                  attributes=n.get("attributes", {})))
        for e in data["relationships"]:
            g.add_edge(Relationship(
                type=e["type"], source=e["source"], target=e["target"],
                weight=float(e["weight"]),
                evidence=[Evidence(**ev) for ev in e.get("evidence", [])]))
        return g
