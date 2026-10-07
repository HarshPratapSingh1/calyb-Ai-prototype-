"""Tests for src/graph.py (walk, neighbors, degrees, JSON round trip) and a
smoke test of the reasoner on the built knowledge_state.json if it exists."""

import json
import unittest
from pathlib import Path

from src.graph import Graph
from src.schema import Entity, Evidence, Relationship


def node(i: str, t: str = "PEP") -> Entity:
    return Entity(i, t, i)


def edge(t: str, s: str, d: str, w: float = 1.0) -> Relationship:
    return Relationship(t, s, d, w, [Evidence(1, "Abstract", "test", f"{s}->{d}")])


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.g = Graph()
        for n in ("a", "b", "c", "d", "x"):
            self.g.add_node(node(n))
        self.g.add_edge(edge("BUILDS_ON", "a", "b"))
        self.g.add_edge(edge("BUILDS_ON", "b", "c", 0.5))
        self.g.add_edge(edge("BUILDS_ON", "c", "d"))
        self.g.add_edge(edge("SUPERSEDES", "x", "b"))

    def test_neighbors_directions(self):
        self.assertEqual([n for n, _ in self.g.neighbors("b", "BUILDS_ON", "out")], ["c"])
        self.assertEqual([n for n, _ in self.g.neighbors("b", "BUILDS_ON", "in")], ["a"])
        self.assertEqual(sorted(n for n, _ in self.g.neighbors("b", "BUILDS_ON", "both")), ["a", "c"])
        self.assertEqual(self.g.neighbors("b", "AVAILABLE_IN", "both"), [])

    def test_walk_respects_hops_and_reports_first_depth(self):
        reached = {n: h for n, h, _ in self.g.walk("a", ["BUILDS_ON"], hops=2)}
        self.assertEqual(reached, {"b": 1, "c": 2})
        reached = {n: h for n, h, _ in self.g.walk("a", ["BUILDS_ON"], hops=3)}
        self.assertEqual(reached["d"], 3)

    def test_walk_returns_path_edges(self):
        paths = {n: p for n, _, p in self.g.walk("a", ["BUILDS_ON"], hops=2)}
        self.assertEqual([e.target for e in paths["c"]], ["b", "c"])

    def test_walk_multiple_rel_types_and_inbound(self):
        reached = {n for n, _, _ in self.g.walk("b", ["BUILDS_ON", "SUPERSEDES"], hops=1, direction="in")}
        self.assertEqual(reached, {"a", "x"})

    def test_degrees(self):
        self.assertEqual(self.g.in_degree("b", "BUILDS_ON"), 1)
        self.assertEqual(self.g.in_degree("b"), 2)
        self.assertEqual(self.g.out_degree("a"), 1)

    def test_duplicate_edge_merges_evidence_and_keeps_max_weight(self):
        self.g.add_edge(Relationship("BUILDS_ON", "b", "c", 0.9, [Evidence(2, "Motivation", "t", "again")]))
        edges = [e for e in self.g.edges if e.source == "b" and e.target == "c"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].weight, 0.9)
        self.assertEqual(len(edges[0].evidence), 2)

    def test_edge_to_unknown_node_fails(self):
        with self.assertRaises(KeyError):
            self.g.add_edge(edge("BUILDS_ON", "a", "nope"))

    def test_json_round_trip(self):
        data = json.loads(json.dumps(self.g.to_json()))
        g2 = Graph.from_json(data)
        self.assertEqual(set(g2.nodes), set(self.g.nodes))
        self.assertEqual(len(g2.edges), len(self.g.edges))
        self.assertEqual([n for n, _ in g2.neighbors("b", "BUILDS_ON", "in")], ["a"])


STATE = Path(__file__).resolve().parent.parent / "knowledge_state.json"


@unittest.skipUnless(STATE.exists(), "run `python -m src.cli build` first")
class ReasonerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.reason import Reasoner
        cls.r = Reasoner(Graph.from_json(json.loads(STATE.read_text(encoding="utf-8"))))

    def test_union_question_cites_pep_604(self):
        out = self.r.ask("Why does Python use `X | Y` instead of `Union[X, Y]`?")
        self.assertEqual(out["input_type"], "question")
        self.assertEqual(out["ranked_peps"][0]["pep"], 604)
        self.assertEqual(out["minimum_python_version"], "3.10")
        self.assertTrue(any(c["rule"] == "I5_DESIGN_RATIONALE" for c in out["conclusions"]))
        self.assertTrue(all(step["step"] for step in out["trace"]))

    def test_arrow_callable_is_previously_rejected(self):
        out = self.r.ask("Allow writing callable types as (int, str) -> bool.")
        self.assertEqual(out["verdict"], "previously_rejected")
        self.assertIn(677, [c["pep"] for c in out["citations"]])

    def test_unrelated_input_is_insufficient(self):
        out = self.r.ask("Make the GIL optional.")
        self.assertEqual(out["verdict"], "insufficient_evidence")
        self.assertEqual(out["ranked_peps"], [])


if __name__ == "__main__":
    unittest.main()
