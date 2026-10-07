"""Command-line entry point.

    python -m src.cli build                      # raw PEPs -> knowledge_state.json
    python -m src.cli ask "Why does Python use X | Y instead of Union[X, Y]?"
    python -m src.cli ask --file examples/inputs/02_union_pipe.txt [--pretty]
    python -m src.cli examples                   # re-run every examples/inputs/*.txt

`build` is also where the one cross-PEP decision is made: which PEP
*introduced* each construct or syntax (see resolve_introduces()). Everything
else is extracted PEP by PEP in src/extract.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import extract
from .graph import Graph
from .schema import Entity, Relationship, make_id, schema_as_json

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "pep_index.json"
RAW_DIR = REPO_ROOT / "data" / "raw" / "peps"
STATE_PATH = REPO_ROOT / "knowledge_state.json"
EXAMPLES_IN = REPO_ROOT / "examples" / "inputs"
EXAMPLES_OUT = REPO_ROOT / "examples" / "outputs"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _raw_path(number: int) -> Path | None:
    for ext in ("rst", "txt"):
        p = RAW_DIR / f"pep-{number:04d}.{ext}"
        if p.exists():
            return p
    return None


def resolve_introduces(graph: Graph, candidates: list[tuple[int, str, int, extract.Evidence]]
                       ) -> dict[str, int]:
    """Pick ONE introducer per construct/syntax (rule R-INT, conflicts section).

    candidates: (pep number, target id, priority, evidence). Lower priority
    number wins (1 = cross-reference "introduced in PEP N", 2 = title,
    3 = abstract cue, 4 = section heading). Ties go to the lowest PEP number,
    on the grounds that the earliest PEP to describe a thing defined it.
    Returns target id -> winning PEP number.
    """
    by_target: dict[str, list[tuple[int, int, extract.Evidence]]] = {}
    for pep, target, prio, ev in candidates:
        # A cross-reference names the introducer inside the snippet
        # ("[pep:484] ..."); the PEP that *contains* the sentence is not it.
        if ev.rule == "R-INT cross-reference":
            pep = int(ev.snippet.split("]")[0].split(":")[1])
        by_target.setdefault(target, []).append((prio, pep, ev))
    winners: dict[str, int] = {}
    for target, lst in by_target.items():
        lst.sort(key=lambda t: (t[0], t[1]))
        best_prio, best_pep, _ = lst[0]
        winners[target] = best_pep
        # Keep the evidence of the winning PEP, at most one item per rule.
        evidence: list[extract.Evidence] = []
        seen_rules: set[str] = set()
        for prio, pep, ev in lst:
            if pep == best_pep and ev.rule not in seen_rules:
                evidence.append(ev)
                seen_rules.add(ev.rule)
        weight = {1: 1.0, 2: 1.0, 3: 0.9, 4: 0.7, 5: 0.5}[best_prio]
        graph.add_edge(Relationship("INTRODUCES", make_id("PEP", str(best_pep)), target, weight, evidence))
    return winners


def build(verbose: bool = True) -> Graph:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    numbers = [int(e["number"]) for e in index["peps"]]
    graph = Graph()

    # Construct / Syntax nodes exist for every curated entry, even if no PEP
    # ends up introducing them (e.g. angle brackets), so CONCERNS edges have
    # somewhere to point.
    for cs in extract.CONSTRUCTS:
        graph.add_node(Entity(make_id("Construct", cs.key), "Construct", cs.name,
                              attributes={"name": cs.name, "kind": cs.kind,
                                          "aliases": list(cs.words + cs.weak)}))
    for sx in extract.SYNTAX:
        graph.add_node(Entity(make_id("Syntax", sx.key), "Syntax", sx.label,
                              attributes={"notation": sx.notation, "aliases": list(sx.words)}))

    extractions: dict[int, extract.Extraction] = {}
    headers: dict[int, dict[str, str]] = {}
    for n in numbers:
        path = _raw_path(n)
        if path is None:
            sys.exit(f"ERROR: data/raw/peps/pep-{n:04d}.rst is missing. Run: python -m src.ingest")
        text = path.read_text(encoding="utf-8")
        headers[n] = extract.parse_header(text)
        graph.add_node(extract.pep_entity(n, headers[n], in_slice=True))
        extractions[n] = extract.extract_pep(n, text)
        if verbose:
            ex = extractions[n]
            print(f"  PEP {n:>4}: {len(ex.entities):>3} entities, {len(ex.relationships):>3} relationships, "
                  f"{len(ex.introduces_candidates):>2} introduce-candidates")

    # Stub nodes for PEPs referenced but outside the slice (3107, 557, ...).
    for ex in extractions.values():
        for rel in ex.relationships:
            for node_id in (rel.source, rel.target):
                if node_id.startswith("pep:") and not graph.has(node_id):
                    num = int(node_id.split(":")[1])
                    graph.add_node(extract.pep_entity(num, {"Title": f"PEP {num}"}, in_slice=False))

    for ex in extractions.values():
        for ent in ex.entities:
            graph.add_node(ent)
    for ex in extractions.values():
        for rel in ex.relationships:
            graph.add_edge(rel)

    winners = resolve_introduces(graph, [(n, t, p, ev) for n, ex in extractions.items()
                                         for t, p, ev in ex.introduces_candidates])
    # EXTENDS: a PEP that mentions X in its title/abstract but did not introduce it.
    for n, ex in extractions.items():
        for target, ev in ex.extends_candidates:
            if target in winners and winners[target] != n:
                graph.add_edge(Relationship("EXTENDS", make_id("PEP", str(n)), target, 0.7, [ev]))

    # Rejected/withdrawn PEPs "introduce" nothing that exists: relabel for readers.
    for edge in graph.edges:
        if edge.type == "INTRODUCES":
            status = graph.nodes[edge.source].attributes.get("status")
            if status in ("Rejected", "Withdrawn"):
                edge.evidence.append(extract.Evidence(
                    int(edge.source.split(":")[1]), "Header", "R-INT status-note",
                    f"Status: {status} — the PEP proposed this; it was never adopted"))
    return graph


def stats(graph: Graph) -> dict:
    per_type = {t: len(graph.nodes_of_type(t)) for t in sorted({n.type for n in graph.nodes.values()})}
    per_rel: dict[str, int] = {}
    for e in graph.edges:
        per_rel[e.type] = per_rel.get(e.type, 0) + 1
    connected = sorted(graph.nodes.values(),
                       key=lambda n: -(graph.in_degree(n.id) + graph.out_degree(n.id)))[:10]
    most_built_on = sorted(graph.nodes_of_type("PEP"), key=lambda n: -graph.in_degree(n.id, "BUILDS_ON"))[:8]
    return {
        "entities_by_type": per_type,
        "relationships_by_type": dict(sorted(per_rel.items())),
        "total_entities": len(graph.nodes),
        "total_relationships": len(graph.edges),
        "most_connected_nodes": [
            {"id": n.id, "label": n.label, "degree": graph.in_degree(n.id) + graph.out_degree(n.id)}
            for n in connected],
        "most_built_on_peps": [
            {"id": n.id, "label": n.label, "builds_on_in_degree": graph.in_degree(n.id, "BUILDS_ON")}
            for n in most_built_on],
    }


def cmd_build(args: argparse.Namespace) -> int:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    print(f"Building knowledge graph from {len(index['peps'])} PEPs in {RAW_DIR.relative_to(REPO_ROOT)}/")
    graph = build(verbose=not args.quiet)
    state = {
        "meta": {
            "project": "pep-knowledge-graph",
            "slice": index["slice"],
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {
                "pep_index": "data/pep_index.json",
                "raw_text": "data/raw/peps/pep-NNNN.rst (from https://github.com/python/peps)",
                "peps_in_slice": [e["number"] for e in index["peps"]],
            },
            "how_to_read": (
                "entities are grouped by type; every relationship carries evidence "
                "(pep, section, rule, snippet) pointing at the raw text it was extracted from; "
                "rule names refer to src/extract.py and notes/design.md"),
        },
        "schema": schema_as_json(),
        "stats": stats(graph),
    }
    state.update(graph.to_json())
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    s = state["stats"]
    print(f"Wrote {STATE_PATH.relative_to(REPO_ROOT)}: {s['total_entities']} entities, "
          f"{s['total_relationships']} relationships")
    print("  entities:     ", s["entities_by_type"])
    print("  relationships:", s["relationships_by_type"])
    return 0


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

def load_graph() -> Graph:
    if not STATE_PATH.exists():
        sys.exit("ERROR: knowledge_state.json not found. Run: python -m src.cli build")
    return Graph.from_json(json.loads(STATE_PATH.read_text(encoding="utf-8")))


def cmd_ask(args: argparse.Namespace) -> int:
    from .reason import Reasoner  # imported here so `build` has no dependency on it

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = " ".join(args.text).strip()
    else:
        sys.exit("ERROR: give the input as text or with --file PATH")
    result = Reasoner(load_graph()).ask(text)
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


def cmd_examples(args: argparse.Namespace) -> int:
    from .reason import Reasoner

    reasoner = Reasoner(load_graph())
    EXAMPLES_OUT.mkdir(parents=True, exist_ok=True)
    for path in sorted(EXAMPLES_IN.glob("*.txt")):
        result = reasoner.ask(path.read_text(encoding="utf-8").strip())
        out = EXAMPLES_OUT / (path.stem + ".json")
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  {path.name:<32} -> {out.relative_to(REPO_ROOT)}  verdict={result['verdict']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="extract entities/relationships and write knowledge_state.json")
    p_build.add_argument("--quiet", action="store_true", help="only print the summary")
    p_build.set_defaults(func=cmd_build)

    p_ask = sub.add_parser("ask", help="answer a proposal or question using the graph")
    p_ask.add_argument("text", nargs="*", help="the input text")
    p_ask.add_argument("--file", help="read the input from a file instead")
    p_ask.add_argument("--pretty", action="store_true", help="indent the JSON output")
    p_ask.set_defaults(func=cmd_ask)

    p_ex = sub.add_parser("examples", help="run every examples/inputs/*.txt and save the outputs")
    p_ex.set_defaults(func=cmd_examples)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
