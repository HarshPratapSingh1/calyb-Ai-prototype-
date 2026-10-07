"""Entity and relationship types of the knowledge graph.

Everything in the graph is one of the entity types below, joined by one of the
relationship types below. The one-line docstrings are copied verbatim into the
``schema`` section of knowledge_state.json so a reader of that file can see
what each type means without opening the code.

The design rationale (why these types, how each is extracted) is in
notes/design.md. The extraction rules themselves live in src/extract.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------
# Each entity type is a small dataclass. All of them share the four fields of
# Entity: id (unique, prefixed by type), type, label (human readable) and
# sources (where in the raw PEP text the entity was found).

@dataclass
class Entity:
    """Base class: every node has an id, a type, a label and its sources."""

    id: str
    type: str
    label: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ENTITY_TYPES: dict[str, str] = {
    "PEP": "A Python Enhancement Proposal in or referenced by the slice "
           "(number, title, status, type, python_version, in_slice).",
    "Construct": "A named typing feature you import or use: a class, special "
                 "form, qualifier, decorator or function (e.g. TypedDict, ParamSpec).",
    "Syntax": "A piece of language notation rather than an importable name "
              "(e.g. `X | Y`, `list[int]`, `class C[T]`, `(int) -> str`).",
    "Problem": "A recurring motivation: something users or tools struggled with "
               "that one or more PEPs set out to fix.",
    "RejectedIdea": "An alternative that a PEP considered and rejected, postponed "
                    "or answered an objection to; carries the reason given.",
    "PythonVersion": "A CPython feature release (from the Python-Version header).",
    "TypeChecker": "A static type checker or IDE named as implementing a PEP "
                   "(mypy, pyright, Pyre, pytype, PyCharm).",
}

# ID prefixes, so an id is self-describing: "pep:604", "construct:typeddict".
ID_PREFIX: dict[str, str] = {
    "PEP": "pep",
    "Construct": "construct",
    "Syntax": "syntax",
    "Problem": "problem",
    "RejectedIdea": "idea",
    "PythonVersion": "py",
    "TypeChecker": "checker",
}


def make_id(entity_type: str, key: str) -> str:
    """Build a stable id such as ``pep:604`` or ``construct:typeddict``."""
    return f"{ID_PREFIX[entity_type]}:{key}"


# ---------------------------------------------------------------------------
# Relationship types
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """Where a relationship came from: PEP, section, rule and a short snippet."""

    pep: int
    section: str
    rule: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Relationship:
    """A directed, typed edge with a weight in (0, 1] and at least one Evidence."""

    type: str
    source: str
    target: str
    weight: float
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "source": self.source,
            "target": self.target,
            "weight": round(self.weight, 3),
            "evidence": [e.to_dict() for e in self.evidence],
        }


# name -> (source types, target types, docstring)
RELATIONSHIP_TYPES: dict[str, dict[str, Any]] = {
    "SUPERSEDES": {
        "source": ["PEP"], "target": ["PEP"],
        "doc": "The source PEP replaced the target PEP (from the Replaces / "
               "Superseded-By header fields).",
    },
    "BUILDS_ON": {
        "source": ["PEP"], "target": ["PEP"],
        "doc": "The source PEP relies on or extends the target PEP: a reference "
               "outside rejected-ideas sections, weighted by the section it sits in.",
    },
    "INTRODUCES": {
        "source": ["PEP"], "target": ["Construct", "Syntax"],
        "doc": "The source PEP defined the construct or syntax (or proposed it, if "
               "the PEP was rejected or withdrawn; the reasoner checks the status).",
    },
    "EXTENDS": {
        "source": ["PEP"], "target": ["Construct", "Syntax"],
        "doc": "The source PEP changes or adds to a construct or syntax that a "
               "different PEP introduced (title or abstract mentions it as code).",
    },
    "ADDRESSES_PROBLEM": {
        "source": ["PEP"], "target": ["Problem"],
        "doc": "The source PEP was written to fix the problem (cue phrases in its "
               "Abstract, Motivation or Rationale).",
    },
    "REJECTED_ALTERNATIVE": {
        "source": ["PEP"], "target": ["RejectedIdea"],
        "doc": "The source PEP considered this alternative and rejected, postponed "
               "or objected to it (one idea per subsection or bullet of a "
               "rejected-ideas section).",
    },
    "CONCERNS": {
        "source": ["RejectedIdea"], "target": ["Construct", "Syntax", "Problem", "PEP"],
        "doc": "What a rejected idea was about: constructs, syntax, problems or PEPs "
               "mentioned in the idea's heading or text.",
    },
    "AVAILABLE_IN": {
        "source": ["PEP"], "target": ["PythonVersion"],
        "doc": "The PEP shipped in (or, if rejected/withdrawn, targeted) this "
               "Python version, from the Python-Version header.",
    },
    "IMPLEMENTED_BY": {
        "source": ["PEP"], "target": ["TypeChecker"],
        "doc": "A type checker is named in the PEP's implementation sections as "
               "implementing or prototyping it.",
    },
}


def schema_as_json() -> dict[str, Any]:
    """The schema block written into knowledge_state.json."""
    return {
        "entity_types": {name: {"doc": doc, "id_prefix": ID_PREFIX[name]}
                         for name, doc in ENTITY_TYPES.items()},
        "relationship_types": RELATIONSHIP_TYPES,
        "evidence_fields": {
            "pep": "PEP number the evidence was read from",
            "section": "heading path of the section ('Header' for header fields)",
            "rule": "name of the extraction rule in src/extract.py that fired",
            "snippet": "the text (<= ~200 chars) that triggered the rule",
        },
    }
