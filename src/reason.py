"""The reasoning engine: new input -> explainable JSON answer.

Pipeline (each step appends to ``trace`` so every number in the answer can
be reproduced by hand):

    classify  -> "proposal" or "question"
    match     -> input text -> seed entities with a match strength m
    traverse  -> seeds -> candidate PEPs, at most 2 hops, keeping every path
    score     -> score(P) = sum(path strengths) x status weight x centrality
    infer     -> rules I1..I6 that combine several facts into conclusions
    output    -> the JSON schema from notes/design.md, section 3

Nothing here is learned: the alias tables come from src/extract.py and the
knowledge from knowledge_state.json. The reasoner never quotes text that is
not already stored as evidence on a graph edge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import extract
from .graph import Graph
from .schema import Relationship

HOP_DECAY = 0.6            # lambda in the score formula
MAX_HOPS = 2
MIN_SEED_STRENGTH = 0.4
MIN_SCORE = 0.15
MAX_RANKED = 6

# S(status, input_type): how much a PEP's status matters for this input type.
STATUS_WEIGHT = {
    "proposal": {"Final": 1.0, "Superseded": 0.7, "Rejected": 1.0, "Withdrawn": 0.9, "Active": 1.0,
                 "Accepted": 1.0, "Draft": 0.8, None: 0.3},
    "question": {"Final": 1.0, "Superseded": 0.8, "Rejected": 0.6, "Withdrawn": 0.6, "Active": 1.0,
                 "Accepted": 1.0, "Draft": 0.8, None: 0.3},
}

_QUESTION_START = re.compile(r"^\s*(why|how|what|when|which|is|does|can|do|are|was|were|should)\b", re.I)
_PROPOSAL_START = re.compile(
    r"^\s*(add|allow|introduce|support|make|let|propose|provide|i propose|we should|enable|permit|extend)\b", re.I)


@dataclass
class Seed:
    entity_id: str
    strength: float
    matched_on: str


@dataclass
class Path:
    seed: Seed
    nodes: list[str]                 # seed ... candidate
    edges: list[Relationship]
    hops: int
    strength: float

    def describe(self) -> list[str]:
        """['syntax:union-pipe', '<-INTRODUCES-', 'pep:604', '-BUILDS_ON->', 'pep:484']"""
        out = [self.nodes[0]]
        for prev, edge, nxt in zip(self.nodes, self.edges, self.nodes[1:]):
            out.append(f"-{edge.type}->" if edge.source == prev else f"<-{edge.type}-")
            out.append(nxt)
        return out


@dataclass
class Conclusion:
    rule: str
    statement: str
    confidence: str
    peps: list[int]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)   # internal, used for the verdict

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "statement": self.statement, "confidence": self.confidence,
                "peps": sorted(set(self.peps)), "evidence": self.evidence}


def _cut(text: str, n: int) -> str:
    """Shorten to about n chars at a word boundary."""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + "…"


def _stem(word: str) -> str:
    for suf in ("ies", "ing", "ly", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[:-len(suf)] + ("y" if suf == "ies" else "")
    return word


def tokens(text: str) -> set[str]:
    """Lowercase stemmed tokens plus joined bigrams ('typed dict' -> 'typeddict')."""
    words = [w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in extract.STOPWORDS]
    stems = {_stem(w) for w in words}
    stems |= {_stem(a + b) for a, b in zip(words, words[1:])}
    return stems


class Reasoner:
    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self.peps = {n.id: n for n in graph.nodes_of_type("PEP")}
        self.max_in = max((graph.in_degree(p, "BUILDS_ON") for p in self.peps), default=1) or 1
        self.ideas = graph.nodes_of_type("RejectedIdea")

    # ------------------------------------------------------------------ ask
    def ask(self, text: str) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        input_type = self.classify(text, trace)
        seeds, version_filter = self.match(text, trace)
        paths, reached_ideas = self.traverse(seeds, trace)
        ranked = self.score(paths, input_type, trace)
        conclusions = self.infer(text, input_type, seeds, ranked, reached_ideas, trace)
        return self.output(text, input_type, seeds, ranked, conclusions, trace, version_filter)

    # ------------------------------------------------------------- classify
    def classify(self, text: str, trace: list) -> str:
        if _QUESTION_START.search(text) or text.rstrip().endswith("?"):
            kind, rule = "question", "starts with a question word or ends with '?'"
        elif _PROPOSAL_START.search(text):
            kind, rule = "proposal", "starts with a proposal verb (add/allow/introduce/...)"
        else:
            kind, rule = "proposal", "default: statements are treated as proposals"
        trace.append({"step": "classify", "result": kind, "rule": rule})
        return kind

    # ---------------------------------------------------------------- match
    def match(self, text: str, trace: list) -> tuple[list[Seed], str | None]:
        low = text.lower()
        toks = tokens(text)
        found: dict[str, Seed] = {}

        def add(entity_id: str, strength: float, how: str) -> None:
            if not self.g.has(entity_id):
                return
            cur = found.get(entity_id)
            if cur is None or strength > cur.strength:
                found[entity_id] = Seed(entity_id, round(strength, 3), how)

        def phrase_in(phrase: str) -> bool:
            if phrase[-1].isalnum() and phrase[0].isalnum():
                return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", low) is not None
            return phrase in low

        # explicit PEP numbers
        for m in re.finditer(r"\bPEP[\s-]?(\d+)\b", text, re.I):
            add(f"pep:{m.group(1)}", 1.0, f"explicit mention '{m.group(0)}'")
        # syntax: regex on the raw input, then word aliases
        for sx in extract.SYNTAX:
            if sx.input_pattern and re.search(sx.input_pattern, text):
                add(f"syntax:{sx.key}", 1.0, f"pattern /{sx.input_pattern}/ on raw input")
            for w in sx.words:
                if phrase_in(w):
                    add(f"syntax:{sx.key}", 0.8, f"alias '{w}'")
        # constructs: canonical name (case-sensitive word or in backticks), aliases, weak words
        for cs in extract.CONSTRUCTS:
            if re.search(r"(?<![A-Za-z0-9_.])" + re.escape(cs.name) + r"(?![A-Za-z0-9_])", text):
                add(f"construct:{cs.key}", 1.0, f"canonical name '{cs.name}'")
            for w in cs.words:
                if phrase_in(w):
                    add(f"construct:{cs.key}", 0.9, f"alias '{w}'")
            for w in cs.weak:
                if phrase_in(w):
                    add(f"construct:{cs.key}", 0.6, f"weak alias '{w}'")
        # problems: input aliases, then the PEP-text cue regexes
        for pb in extract.PROBLEMS:
            for w in pb.words:
                if phrase_in(w):
                    add(f"problem:{pb.key}", 0.7, f"alias '{w}'")
            for cue in pb.cues:
                if re.search(cue, low):
                    add(f"problem:{pb.key}", 0.7, f"cue /{cue}/")
        # rejected ideas and PEP titles: keyword overlap
        for idea in self.ideas:
            kws = {_stem(k) for k in idea.attributes.get("keywords", [])}
            if len(kws) >= 2:
                overlap = len(kws & toks) / len(kws)
                if overlap >= 0.5 and len(kws & toks) >= 2:
                    add(idea.id, 0.8 * overlap, f"title keywords overlap {overlap:.2f} ({', '.join(sorted(kws & toks))})")
        for pid, pep in self.peps.items():
            kws = {_stem(w) for w in re.findall(r"[a-z0-9]+", pep.attributes.get("title", "").lower())
                   if w not in extract.STOPWORDS}
            if len(kws) >= 2:
                overlap = len(kws & toks) / len(kws)
                if overlap >= 0.5:
                    add(pid, 0.6 * overlap, f"title keywords overlap {overlap:.2f} ({', '.join(sorted(kws & toks))})")

        version = None
        vm = re.search(r"\b(3\.\d+)\b", text)
        if vm:
            version = vm.group(1)
            trace.append({"step": "match", "entity": f"py:{version}", "strength": 1.0,
                          "via": "version mentioned; used as a filter, not a seed"})

        seeds = sorted((s for s in found.values() if s.strength >= MIN_SEED_STRENGTH),
                       key=lambda s: -s.strength)
        for s in seeds:
            trace.append({"step": "match", "entity": s.entity_id, "strength": s.strength, "via": s.matched_on})
        if not seeds:
            trace.append({"step": "match", "result": "no entity matched", "strength": 0})
        return seeds, version

    # ------------------------------------------------------------- traverse
    def traverse(self, seeds: list[Seed], trace: list
                 ) -> tuple[dict[str, list[Path]], dict[str, tuple[float, Path]]]:
        """Seeds -> candidate PEPs with every path kept (max 2 hops)."""
        paths: dict[str, list[Path]] = {}
        reached_ideas: dict[str, tuple[float, Path]] = {}

        def record(path: Path) -> None:
            target = path.nodes[-1]
            trace.append({"step": "traverse", "from": path.nodes[-2] if len(path.nodes) > 1 else None,
                          "edge": path.edges[-1].type if path.edges else None,
                          "to": target, "hop": path.hops, "path_strength": round(path.strength, 3),
                          "path": " ".join(path.describe())})
            if target.startswith("pep:"):
                paths.setdefault(target, []).append(path)
            elif target.startswith("idea:"):
                cur = reached_ideas.get(target)
                if cur is None or path.strength > cur[0]:
                    reached_ideas[target] = (path.strength, path)

        def step(path: Path, other: str, edge: Relationship, weight: float | None = None) -> Path:
            w = edge.weight if weight is None else weight
            return Path(path.seed, path.nodes + [other], path.edges + [edge], path.hops + 1,
                        path.strength * w * HOP_DECAY)

        hop1: list[Path] = []
        for seed in seeds:
            sid = seed.entity_id
            root = Path(seed, [sid], [], 0, seed.strength)
            kind = sid.split(":")[0]
            if kind == "pep":
                paths.setdefault(sid, []).append(root)
                hop1.append(root)
                trace.append({"step": "traverse", "to": sid, "hop": 0, "path_strength": seed.strength,
                              "path": sid})
            elif kind in ("construct", "syntax"):
                for rel in ("INTRODUCES", "EXTENDS"):
                    for pep, e in self.g.neighbors(sid, rel, "in"):
                        p = step(root, pep, e); record(p); hop1.append(p)
                for idea, e in self.g.neighbors(sid, "CONCERNS", "in"):
                    p = step(root, idea, e); record(p)
                    for pep, e2 in self.g.neighbors(idea, "REJECTED_ALTERNATIVE", "in"):
                        record(step(p, pep, e2, 0.9))
            elif kind == "problem":
                for pep, e in self.g.neighbors(sid, "ADDRESSES_PROBLEM", "in"):
                    p = step(root, pep, e); record(p); hop1.append(p)
                for idea, e in self.g.neighbors(sid, "CONCERNS", "in"):
                    p = step(root, idea, e); record(p)
                    for pep, e2 in self.g.neighbors(idea, "REJECTED_ALTERNATIVE", "in"):
                        record(step(p, pep, e2, 0.9))
            elif kind == "idea":
                reached_ideas[sid] = (seed.strength, root)
                for pep, e in self.g.neighbors(sid, "REJECTED_ALTERNATIVE", "in"):
                    p = step(root, pep, e, 0.9); record(p); hop1.append(p)

        # PEPs cited inside a reached idea ("Change only PEP 484 ..." cites 563)
        for idea_id, (strength, p) in list(reached_ideas.items()):
            if p.hops >= MAX_HOPS:
                continue
            for other, e in self.g.neighbors(idea_id, "CONCERNS", "out"):
                if other.startswith("pep:"):
                    record(step(p, other, e))

        # hop 2: context PEPs around every hop-0/1 PEP
        for p in hop1:
            if p.hops >= MAX_HOPS:
                continue
            pep = p.nodes[-1]
            for other, e in self.g.neighbors(pep, "BUILDS_ON", "out"):
                record(step(p, other, e))
            for other, e in self.g.neighbors(pep, "SUPERSEDES", "both"):
                record(step(p, other, e, 0.8))
        return paths, reached_ideas

    # ---------------------------------------------------------------- score
    def score(self, paths: dict[str, list[Path]], input_type: str, trace: list) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        for pid, plist in paths.items():
            pep = self.peps.get(pid)
            if pep is None:
                continue
            status = pep.attributes.get("status")
            path_sum = sum(p.strength for p in plist)
            s_w = STATUS_WEIGHT[input_type].get(status, 0.3)
            if not pep.attributes.get("in_slice"):
                s_w = STATUS_WEIGHT[input_type][None]
            cent = 1 + 0.5 * self.g.in_degree(pid, "BUILDS_ON") / self.max_in
            score = path_sum * s_w * cent
            trace.append({"step": "score", "pep": pep.attributes["number"],
                          "formula": f"path_sum {path_sum:.3f} x status[{status}]={s_w} x centrality {cent:.2f}",
                          "score": round(score, 3)})
            if score < MIN_SCORE:
                continue
            ranked.append({
                "pep": pep.attributes["number"], "title": pep.attributes.get("title"),
                "status": status, "python_version": pep.attributes.get("python_version"),
                "in_slice": pep.attributes.get("in_slice", False),
                "score": round(score, 3),
                "score_breakdown": {
                    "paths": [{"route": " ".join(p.describe()), "strength": round(p.strength, 3)}
                              for p in sorted(plist, key=lambda p: -p.strength)[:5]],
                    "path_sum": round(path_sum, 3), "status_weight": s_w, "centrality": round(cent, 3)},
            })
        ranked.sort(key=lambda r: -r["score"])
        self._all_scored = {f"pep:{r['pep']}": r for r in ranked}   # every PEP above MIN_SCORE
        return ranked[:MAX_RANKED]

    # ---------------------------------------------------------------- infer
    def infer(self, text: str, input_type: str, seeds: list[Seed], ranked: list[dict[str, Any]],
              reached_ideas: dict[str, tuple[float, Path]], trace: list) -> list[Conclusion]:
        out: list[Conclusion] = []
        seed_by_id = {s.entity_id: s for s in seeds}
        ranked_ids = {f"pep:{r['pep']}": r for r in ranked}

        def ev(edge: Relationship) -> list[dict[str, Any]]:
            return [e.to_dict() for e in edge.evidence[:2]]

        def pep_num(pid: str) -> int:
            return int(pid.split(":")[1])

        def pep_attr(pid: str, key: str) -> Any:
            return self.peps[pid].attributes.get(key) if pid in self.peps else None

        def confidence(m: float, w: float) -> str:
            return "high" if m >= 0.8 and w >= 0.8 else "medium" if m >= 0.6 else "low"

        def fired(rule: str, inputs: list[str], did: bool) -> None:
            trace.append({"step": "infer", "rule": rule, "fired": did, "inputs": inputs})

        def introducer(target: str) -> tuple[str, Relationship] | None:
            lst = self.g.neighbors(target, "INTRODUCES", "in")
            return lst[0] if lst else None

        # Relevance of a reached RejectedIdea to *this* input. An idea reached
        # through a syntax, problem or direct keyword match is relevant. One
        # reached only through a construct name (every TypedDict idea mentions
        # ``TypedDict``) must also share a keyword with the input beyond that name.
        input_toks = tokens(text)

        def relevant(idea_id: str, path: Path) -> bool:
            kind = path.seed.entity_id.split(":")[0]
            if kind in ("syntax", "problem", "idea"):
                return True
            node = self.g.nodes[path.seed.entity_id]
            own = tokens(node.label + " " + " ".join(node.attributes.get("aliases", []))) | {
                _stem(w) for w in re.findall(r"[a-z0-9]+", node.label.lower())}
            kws = {_stem(k) for k in self.g.nodes[idea_id].attributes.get("keywords", [])}
            return bool((kws & input_toks) - own)

        relevant_ideas = {i: (st, p) for i, (st, p) in reached_ideas.items() if relevant(i, p)}
        trace.append({"step": "infer", "rule": "relevance-filter",
                      "kept": sorted(relevant_ideas), "dropped": sorted(set(reached_ideas) - set(relevant_ideas))})

        # ---- I1 PRIOR_REJECTION: a rejected idea or a Rejected/Withdrawn PEP was reached
        hits = 0
        for idea_id, (strength, path) in sorted(relevant_ideas.items(), key=lambda kv: -kv[1][0])[:4]:
            idea = self.g.nodes[idea_id]
            a = idea.attributes
            if strength < 0.3 or a.get("disposition") == "postponed":
                continue
            verb = {"rejected": "rejected", "objection": "raised and answered as an objection"}.get(
                a["disposition"], a["disposition"])
            # A direct keyword match on the idea's title is normalised (max 0.8).
            m_seed = min(1.0, path.seed.strength / 0.8) if path.seed.entity_id.startswith("idea:") else path.seed.strength
            out.append(Conclusion(
                "I1_PRIOR_REJECTION",
                f"A closely related idea, \"{a['title']}\", was {verb} in PEP {a['pep']}: {a['reason']}",
                confidence(m_seed, min((e.weight for e in path.edges), default=1.0)),
                [a["pep"]], ev(path.edges[-1]) if path.edges else [{"pep": a["pep"], "section": idea.sources[0]["section"]}],
                flags={"idea": idea_id}))
            hits += 1
        for pid, r in ranked_ids.items():
            if r["status"] in ("Rejected", "Withdrawn") and r["score"] >= 0.3:
                intro = [t for t, _ in self.g.neighbors(pid, "INTRODUCES", "out")]
                what = ", ".join(self.g.nodes[t].label for t in intro) or r["title"]
                sup = self.g.neighbors(pid, "SUPERSEDES", "in")
                tail = f" It was replaced by PEP {pep_num(sup[0][0])}." if sup else ""
                out.append(Conclusion(
                    "I1_PRIOR_REJECTION",
                    f"PEP {r['pep']} (\"{r['title']}\") proposed {what} and its status is {r['status']}.{tail}",
                    "high" if r["score"] >= 0.5 else "medium", [r["pep"]] + [pep_num(s[0]) for s in sup],
                    [{"pep": r["pep"], "section": "Header", "rule": "status", "snippet": f"Status: {r['status']}"}],
                    flags={"pep": r["pep"], "successor_final": any(pep_attr(s[0], "status") == "Final" for s in sup)}))
                hits += 1
        fired("I1_PRIOR_REJECTION", list(relevant_ideas)[:6] + [p for p in ranked_ids], hits > 0)

        # ---- I2 ALREADY_PROVIDED (proposal): a matched construct/syntax exists in a Final PEP
        covered: list[str] = []
        matched_features = [s for s in seeds if s.entity_id.split(":")[0] in ("construct", "syntax")
                            and s.strength >= 0.6]
        if input_type == "proposal":
            for s in matched_features:
                intro = introducer(s.entity_id)
                if intro is None:
                    continue
                pid, edge = intro
                if pep_attr(pid, "status") != "Final":
                    continue
                node = self.g.nodes[s.entity_id]
                ver = pep_attr(pid, "python_version")
                out.append(Conclusion(
                    "I2_ALREADY_PROVIDED",
                    f"{node.label} already exists: introduced by PEP {pep_num(pid)} "
                    f"(\"{pep_attr(pid, 'title')}\"), available since Python {ver}.",
                    confidence(s.strength, edge.weight), [pep_num(pid)], ev(edge)))
                covered.append(s.entity_id)
        fired("I2_ALREADY_PROVIDED", [s.entity_id for s in matched_features], bool(covered))

        # ---- I3 DEPENDS_ON: versions of the features involved -> minimum version
        deps: list[tuple[str, int, str]] = []
        for s in matched_features:
            intro = introducer(s.entity_id)
            if intro and pep_attr(intro[0], "status") == "Final" and pep_attr(intro[0], "python_version"):
                deps.append((self.g.nodes[s.entity_id].label, pep_num(intro[0]), pep_attr(intro[0], "python_version")))
        if ranked and input_type == "proposal":
            top = f"pep:{ranked[0]['pep']}"
            for other, e in self.g.neighbors(top, "BUILDS_ON", "out"):
                if e.weight >= 0.8 and pep_attr(other, "status") == "Final" and pep_attr(other, "python_version"):
                    deps.append((f"PEP {pep_num(other)} ({pep_attr(other, 'title')})", pep_num(other),
                                 pep_attr(other, "python_version")))
        minimum = None
        if deps:
            seen: set[int] = set()
            uniq = [d for d in deps if not (d[1] in seen or seen.add(d[1]))]
            minimum = max((d[2] for d in uniq), key=lambda v: tuple(int(x) for x in v.split(".")))
            out.append(Conclusion(
                "I3_DEPENDS_ON",
                "Builds on " + "; ".join(f"{label} (PEP {n}, Python {v})" for label, n, v in uniq)
                + f". Minimum Python version implied: {minimum}.",
                "high" if all(s.strength >= 0.8 for s in matched_features) else "medium",
                [d[1] for d in uniq],
                [{"pep": n, "section": "Header", "rule": "R-VER Python-Version", "snippet": f"Python-Version: {v}"}
                 for _, n, v in uniq]))
        fired("I3_DEPENDS_ON", [d[0] for d in deps], bool(deps))

        # ---- I4 SUPERSEDED_SOURCE: a cited PEP has been superseded
        sup_hits = 0
        # Not only the top 6: a PEP cited inside a relevant idea (563 in 604's
        # objections) matters even with a low score. Out-of-slice successors
        # (749) are mentioned only when no in-slice successor exists.
        for pid, r in getattr(self, "_all_scored", ranked_ids).items():
            if r["score"] < 0.2:
                continue
            newer_all = self.g.neighbors(pid, "SUPERSEDES", "in")
            in_slice = [(n, e) for n, e in newer_all if pep_attr(n, "in_slice")]
            for newer, e in (in_slice or newer_all):
                if newer not in self.peps:
                    continue
                out.append(Conclusion(
                    "I4_SUPERSEDED_SOURCE",
                    f"PEP {pep_num(pid)} (\"{pep_attr(pid, 'title')}\") is superseded by PEP {pep_num(newer)}"
                    + (f" (\"{pep_attr(newer, 'title')}\", Python {pep_attr(newer, 'python_version')})"
                       if pep_attr(newer, "in_slice") else " (outside this slice)")
                    + "; prefer the newer semantics.",
                    "high", [pep_num(pid), pep_num(newer)], ev(e)))
                sup_hits += 1
        fired("I4_SUPERSEDED_SOURCE", list(getattr(self, "_all_scored", ranked_ids)), sup_hits > 0)

        # ---- I5 DESIGN_RATIONALE (question): why the top feature looks the way it does
        if input_type == "question" and matched_features:
            # "Why does Python use <notation>" is about notation: prefer a Syntax
            # seed that has an introducer, then the strongest construct.
            def has_intro(s: Seed) -> bool:
                return introducer(s.entity_id) is not None
            syntax_seeds = [s for s in matched_features if s.entity_id.startswith("syntax:") and has_intro(s)]
            pool = syntax_seeds or matched_features
            best = max(pool, key=lambda s: (s.strength, self.g.in_degree(s.entity_id)))
            intro = introducer(best.entity_id)
            if intro:
                pid, edge = intro
                node = self.g.nodes[best.entity_id]
                problems = [(self.g.nodes[t].label, e) for t, e in self.g.neighbors(pid, "ADDRESSES_PROBLEM", "out")]
                matched_ids = {s.entity_id for s in seeds}
                ideas: list[tuple[str, Relationship]] = []
                context_peps = [pid] + [p for p in ranked_ids if p != pid]
                for cp in context_peps:
                    for idea_id, e in self.g.neighbors(cp, "REJECTED_ALTERNATIVE", "out"):
                        concerns = {t for t, _ in self.g.neighbors(idea_id, "CONCERNS", "out")}
                        if concerns & matched_ids:
                            ideas.append((idea_id, e))
                status = pep_attr(pid, "status")
                stmt = (f"{node.label} was {'proposed' if status in ('Rejected', 'Withdrawn') else 'introduced'} "
                        f"by PEP {pep_num(pid)} (\"{pep_attr(pid, 'title')}\", Python {pep_attr(pid, 'python_version')})")
                if problems:
                    stmt += " to address: " + "; ".join(p for p, _ in problems)
                stmt += "."
                if ideas:
                    stmt += " Alternatives considered and turned down along the way: " + "; ".join(
                        f"\"{self.g.nodes[i].attributes['title']}\" (PEP {self.g.nodes[i].attributes['pep']}: "
                        f"{_cut(self.g.nodes[i].attributes['reason'], 140)})" for i, _ in ideas[:3])
                evidence = ev(edge) + [x for _, e in problems[:2] for x in ev(e)] + [x for _, e in ideas[:3] for x in ev(e)]
                out.append(Conclusion("I5_DESIGN_RATIONALE", stmt, confidence(best.strength, edge.weight),
                                      [pep_num(pid)] + [self.g.nodes[i].attributes["pep"] for i, _ in ideas[:3]],
                                      evidence))
                fired("I5_DESIGN_RATIONALE", [best.entity_id, pid], True)
            else:
                fired("I5_DESIGN_RATIONALE", [best.entity_id], False)
        else:
            fired("I5_DESIGN_RATIONALE", [], False)

        # ---- I6 LATER_RESOLVED: an idea left out by PEP A was delivered by a later PEP B
        res_hits = 0
        # A *postponed* idea can be delivered later by anything; a *rejected*
        # idea only counts when it was a rejected solution to a Problem that a
        # later PEP solved another way. The idea's own PEP must not itself
        # address that problem (then it is the PEP's topic, not what it left out).
        for idea_id, (strength, path) in sorted(relevant_ideas.items(), key=lambda kv: -kv[1][0])[:6]:
            a = self.g.nodes[idea_id].attributes
            if strength < 0.25:
                continue
            own_problems = {t for t, _ in self.g.neighbors(f"pep:{a['pep']}", "ADDRESSES_PROBLEM", "out")}
            for target, e_con in self.g.neighbors(idea_id, "CONCERNS", "out"):
                kind = target.split(":")[0]
                later: list[tuple[str, Relationship]] = []
                if kind == "problem" and target not in own_problems:
                    later = [(b, e) for b, e in self.g.neighbors(target, "ADDRESSES_PROBLEM", "in") if e.weight >= 1.0]
                elif kind in ("construct", "syntax") and a.get("disposition") == "postponed":
                    later = self.g.neighbors(target, "INTRODUCES", "in")
                for bpid, e_b in later:
                    if pep_num(bpid) <= a["pep"] or pep_attr(bpid, "status") != "Final":
                        continue
                    out.append(Conclusion(
                        "I6_LATER_RESOLVED",
                        f"PEP {a['pep']} left this out (\"{a['title']}\": {_cut(a['reason'], 150)}); "
                        f"PEP {pep_num(bpid)} (\"{pep_attr(bpid, 'title')}\") later delivered it "
                        f"({self.g.nodes[target].label}) in Python {pep_attr(bpid, 'python_version')}.",
                        confidence(path.seed.strength, min(e_con.weight, e_b.weight)),
                        [a["pep"], pep_num(bpid)], ev(e_con) + ev(e_b),
                        flags={"idea": idea_id, "version": pep_attr(bpid, "python_version")}))
                    res_hits += 1
        fired("I6_LATER_RESOLVED", list(relevant_ideas)[:6], res_hits > 0)

        # ---- I7 EXTENDED_BY: later Final PEPs that extend a matched construct/syntax
        ext_hits = 0
        matched_problems = {s.entity_id for s in seeds if s.entity_id.startswith("problem:")}
        for s in matched_features:
            intro = introducer(s.entity_id)
            base = pep_num(intro[0]) if intro else 0
            exts = [(b, e) for b, e in self.g.neighbors(s.entity_id, "EXTENDS", "in")
                    if pep_attr(b, "status") == "Final" and pep_num(b) > base]
            if not exts:
                continue
            parts, peps, evidence, hits_problem, problem_version = [], [], [], False, None
            # Problems the introducer itself addresses are the construct's own
            # topic, so an extension gets no credit for them (705 mentions
            # TypedDict's "fixed set of keys" problem without being about it).
            base_problems = {t for t, _ in self.g.neighbors(intro[0], "ADDRESSES_PROBLEM", "out")} if intro else set()
            for b, e in sorted(exts, key=lambda t: pep_num(t[0])):
                added = [self.g.nodes[t].label for t, _ in self.g.neighbors(b, "INTRODUCES", "out")]
                addressed = {t for t, _ in self.g.neighbors(b, "ADDRESSES_PROBLEM", "out")} - base_problems
                note = f" adding {', '.join(added)}" if added else ""
                if addressed & matched_problems:
                    note += " [addresses the problem in the input]"
                    hits_problem = True
                    problem_version = pep_attr(b, "python_version")
                parts.append(f"PEP {pep_num(b)} (\"{pep_attr(b, 'title')}\", Python {pep_attr(b, 'python_version')}){note}")
                peps.append(pep_num(b))
                evidence += ev(e)
            out.append(Conclusion(
                "I7_EXTENDED_BY",
                f"{self.g.nodes[s.entity_id].label} was later extended by " + "; ".join(parts) + ".",
                confidence(s.strength, 0.7), peps, evidence,
                flags={"targets_matched_problem": hits_problem, "version": problem_version}))
            ext_hits += 1
        fired("I7_EXTENDED_BY", [s.entity_id for s in matched_features], ext_hits > 0)

        # De-duplicate identical statements and order them for the reader:
        # a question wants the rationale first, a proposal wants precedents first.
        rule_order = (["I5_DESIGN_RATIONALE", "I6_LATER_RESOLVED", "I1_PRIOR_REJECTION", "I7_EXTENDED_BY",
                       "I4_SUPERSEDED_SOURCE", "I3_DEPENDS_ON", "I2_ALREADY_PROVIDED"] if input_type == "question" else
                      ["I1_PRIOR_REJECTION", "I2_ALREADY_PROVIDED", "I7_EXTENDED_BY", "I6_LATER_RESOLVED",
                       "I3_DEPENDS_ON", "I4_SUPERSEDED_SOURCE", "I5_DESIGN_RATIONALE"])
        conf_order = {"high": 0, "medium": 1, "low": 2}
        uniq: dict[str, Conclusion] = {}
        for c in out:
            uniq.setdefault(c.statement, c)
        return sorted(uniq.values(), key=lambda c: (rule_order.index(c.rule), conf_order[c.confidence]))

    # --------------------------------------------------------------- output
    def output(self, text: str, input_type: str, seeds: list[Seed], ranked: list[dict[str, Any]],
               conclusions: list[Conclusion], trace: list, version_filter: str | None) -> dict[str, Any]:
        rules = {c.rule for c in conclusions}
        features = [s for s in seeds if s.entity_id.split(":")[0] in ("construct", "syntax") and s.strength >= 0.6]
        covered = {c.peps[0] for c in conclusions if c.rule == "I2_ALREADY_PROVIDED"}
        if not seeds:
            verdict = "insufficient_evidence"
        elif input_type == "proposal":
            # Precedence, most decisive first:
            #  (a) a Rejected/Withdrawn PEP with no Final successor -> previously_rejected
            #  (b) the feature exists (I2) or a Final PEP extended it for the
            #      input's problem (I7)                           -> already_exists / partially_covered
            #  (c) a rejected idea that was never delivered (I6)   -> previously_rejected
            #  (d) a postponed idea that was delivered later (I6)  -> partially_covered
            resolved_ideas = {c.flags.get("idea") for c in conclusions if c.rule == "I6_LATER_RESOLVED"}
            dead_pep = any(c.rule == "I1_PRIOR_REJECTION" and "pep" in c.flags and not c.flags["successor_final"]
                           for c in conclusions)
            live_idea = any(c.rule == "I1_PRIOR_REJECTION" and c.flags.get("idea")
                            and c.flags["idea"] not in resolved_ideas for c in conclusions)
            covered_n = sum(1 for c in conclusions if c.rule == "I2_ALREADY_PROVIDED")
            ext_covers = any(c.rule == "I7_EXTENDED_BY" and c.flags.get("targets_matched_problem") for c in conclusions)
            if dead_pep:
                verdict = "previously_rejected"
            elif covered_n or ext_covers:
                verdict = "already_exists" if (covered_n >= len(features) or ext_covers) else "partially_covered"
            elif live_idea:
                verdict = "previously_rejected"
            elif "I6_LATER_RESOLVED" in rules:
                verdict = "partially_covered"
            else:
                verdict = "no_precedent_found"
        else:
            verdict = "explained" if conclusions else "insufficient_evidence"

        # Minimum version: the features the input relies on (I3), raised to the
        # version of a PEP that delivered a postponed idea the input asks about
        # (I6). I7's extensions keep their version in the statement text only.
        key = lambda v: tuple(int(x) for x in v.split("."))
        versions: list[str] = []
        for c in conclusions:
            if c.rule == "I3_DEPENDS_ON":
                m = re.search(r"Minimum Python version implied: (\d+\.\d+)", c.statement)
                if m:
                    versions.append(m.group(1))
            if c.rule == "I6_LATER_RESOLVED" and c.flags.get("version"):
                versions.append(c.flags["version"])
        minimum = max(versions, key=key) if versions else None
        if version_filter and minimum:
            if key(version_filter) < key(minimum):
                conclusions.insert(0, Conclusion(
                    "I3_DEPENDS_ON",
                    f"The input targets Python {version_filter}, but the features involved need {minimum}.",
                    "high", [], []))

        summary_parts = [c.statement for c in conclusions[:4]]
        if not summary_parts:
            summary_parts = ["No entity in the typing slice matched this input well enough to reason about it."
                             if not seeds else "The input matched entities but no inference rule fired."]
        summary = " ".join(summary_parts)

        used: dict[int, set[str]] = {}
        sections: dict[int, set[str]] = {}
        for c in conclusions:
            for p in c.peps:
                used.setdefault(p, set()).add(c.rule)
            for e in c.evidence:
                if "pep" in e and e.get("section"):
                    sections.setdefault(e["pep"], set()).add(e["section"])
        for r in ranked:
            used.setdefault(r["pep"], set()).add("ranked")
        citations = []
        for n in sorted(used):
            pep = self.peps.get(f"pep:{n}")
            citations.append({
                "pep": n, "title": pep.attributes.get("title") if pep else None,
                "status": pep.attributes.get("status") if pep else None,
                "url": f"https://peps.python.org/pep-{n:04d}/",
                "used_for": sorted(used[n]), "sections": sorted(sections.get(n, [])),
            })

        trace.append({"step": "output", "verdict": verdict, "rule": "see Reasoner.output()"})
        return {
            "input": text,
            "input_type": input_type,
            "verdict": verdict,
            "summary": summary,
            "minimum_python_version": minimum,
            "matched_entities": [
                {"id": s.entity_id, "type": self.g.nodes[s.entity_id].type, "label": self.g.nodes[s.entity_id].label,
                 "strength": s.strength, "matched_on": s.matched_on} for s in seeds],
            "ranked_peps": ranked,
            "conclusions": [c.to_dict() for c in conclusions],
            "citations": citations,
            "trace": trace,
        }
