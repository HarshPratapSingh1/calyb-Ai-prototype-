# pep-knowledge-graph

A knowledge graph built from 30 Python Enhancement Proposals about **static
typing** (the `typing` module, annotations, generics, protocols, type
checkers), plus a reasoning engine that takes a *new* input — a proposal or a
"why does Python do X?" question that is not itself a PEP — and returns a
structured, explainable JSON answer citing the PEPs it used.

Everything is hand-written Python 3.11: regular expressions, string handling
and explicit rule tables. No LLMs, no NLP or ML libraries, no configuration
files, no environment variables. The only dependency is `requests`, used to
download the PEP sources.

## What it does

```
data/raw/peps/*.rst  ──extract──▶  knowledge_state.json  ──reason──▶  answer.json
 (30 PEP sources)      (rules)     (280 entities, 603        (match → traverse →
                                    relationships, each       score → infer → output,
                                    with evidence)            with a full trace)
```

* **Entities** (7 types): PEP, Construct (`TypedDict`, `ParamSpec`, …), Syntax
  (`X | Y`, `class C[T]`, …), Problem (what a PEP set out to fix), RejectedIdea
  (one per rejected-ideas subsection or bullet, with the reason given),
  PythonVersion, TypeChecker.
* **Relationships** (9 types): SUPERSEDES, BUILDS_ON, INTRODUCES, EXTENDS,
  ADDRESSES_PROBLEM, REJECTED_ALTERNATIVE, CONCERNS, AVAILABLE_IN,
  IMPLEMENTED_BY. Every relationship carries `evidence`: the PEP, the section
  heading path, the extraction rule, and the text snippet it came from.
* **Reasoning**: an input is matched to entities, the graph is walked up to two
  hops, candidate PEPs are scored
  (`Σ match × edge weights × 0.6^hops × status weight × centrality`), and seven
  inference rules combine facts no single PEP states — "this was rejected
  before in PEP X because…", "PEP A postponed this and PEP B delivered it in
  3.11", "depends on feature Y from 3.Z", "PEP W is superseded by…".

## Repository layout

```
README.md                 this file
approach.md               design write-up
requirements.txt          requests only
data/pep_index.json       the 30 PEPs in the slice: number, title, status, type, version, why_included
data/raw/peps/            the downloaded PEP sources (committed, so build works offline)
knowledge_state.json      the built graph: meta, schema, stats, entities, relationships
examples/inputs/          six test inputs (NN_name.txt)
examples/outputs/         their answers (NN_name.json)
notes/                    skim_notes.md, design.md, iterations.md, approach_outline.md
src/ingest.py             download PEPs (skips existing files; --verify checks the index)
src/schema.py             entity and relationship types with docstrings
src/extract.py            header parser, RST section splitter, reference finder, curated tables, mapping rules
src/graph.py              in-memory graph: nodes, typed adjacency in both directions, walk, to/from JSON
src/reason.py             match → traverse → score → infer → output, with trace
src/cli.py                build / ask / examples commands
tests/                    unit tests (unittest, no extra dependencies)
```

## Install

Python 3.11 is required.

```bash
git clone <this repo> pep-knowledge-graph
cd pep-knowledge-graph
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Build

```bash
python -m src.cli build
```

Reads `data/pep_index.json` and `data/raw/peps/`, runs the extraction rules and
writes `knowledge_state.json` (about 0.5 MB). The raw PEP files are committed,
so no network access is needed. To re-download them (or fetch new ones after
editing the index) run `python -m src.ingest`; `python -m src.ingest --verify`
checks the index against the PEP headers.

## Ask

```bash
python -m src.cli ask "Why does Python use \`X | Y\` instead of \`Union[X, Y]\`?"
python -m src.cli ask --file examples/inputs/03_arrow_callable.txt --pretty
python -m src.cli examples            # re-run every examples/inputs/*.txt
```

Abridged output for the first command (the real one has 6 ranked PEPs,
8 conclusions and a 99-step trace):

```json
{
  "input": "Why does Python use `X | Y` instead of `Union[X, Y]`?",
  "input_type": "question",
  "verdict": "explained",
  "minimum_python_version": "3.10",
  "summary": "X | Y union syntax was introduced by PEP 604 (\"Allow writing union types as X | Y\", Python 3.10) to address: Union[X, Y] is verbose and hurts adoption. Alternatives considered and turned down along the way: \"Add a new operator for Union[type1, type2]?\" (PEP 604: Cons: Adding this operator introduces a dependency between typing and builtins; …",
  "matched_entities": [
    {"id": "syntax:union-pipe", "type": "Syntax", "label": "X | Y union syntax", "strength": 1.0,
     "matched_on": "pattern /[A-Za-z_\\]]\\s*\\|\\s*[A-Za-z_]/ on raw input"},
    {"id": "construct:union", "type": "Construct", "label": "Union", "strength": 1.0,
     "matched_on": "canonical name 'Union'"}
  ],
  "ranked_peps": [
    {"pep": 604, "title": "Allow writing union types as X | Y", "status": "Final",
     "python_version": "3.10", "score": 3.281, "score_breakdown": {"paths": ["..."], "...": "..."}}
  ],
  "conclusions": [
    {"rule": "I5_DESIGN_RATIONALE", "confidence": "high", "peps": [604],
     "statement": "X | Y union syntax was introduced by PEP 604 ... to address: Union[X, Y] is verbose ...",
     "evidence": [{"pep": 604, "section": "Header", "rule": "R-INT title",
                   "snippet": "Title: Allow writing union types as X | Y"}]},
    {"rule": "I4_SUPERSEDED_SOURCE", "confidence": "high", "peps": [563, 649],
     "statement": "PEP 563 (\"Postponed Evaluation of Annotations\") is superseded by PEP 649 ..."}
  ],
  "citations": [
    {"pep": 604, "title": "Allow writing union types as X | Y", "status": "Final",
     "url": "https://peps.python.org/pep-0604/", "used_for": ["I1_PRIOR_REJECTION", "I5_DESIGN_RATIONALE", "ranked"],
     "sections": ["Header", "Motivation", "Objections and responses > 1. Add a new operator ..."]}
  ],
  "trace": [
    {"step": "classify", "result": "question", "rule": "starts with a question word or ends with '?'"},
    {"step": "match", "entity": "syntax:union-pipe", "strength": 1.0, "via": "pattern ... on raw input"},
    {"step": "traverse", "from": "syntax:union-pipe", "edge": "INTRODUCES", "to": "pep:604", "hop": 1,
     "path_strength": 0.6, "path": "syntax:union-pipe <-INTRODUCES- pep:604"},
    {"step": "score", "pep": 604, "formula": "path_sum 3.188 x status[Final]=1.0 x centrality 1.03", "score": 3.281},
    {"step": "infer", "rule": "I5_DESIGN_RATIONALE", "fired": true, "inputs": ["syntax:union-pipe", "pep:604"]}
  ]
}
```

### Output fields

| Field | Meaning |
|---|---|
| `input_type` | `proposal` or `question` (changes status weights and which rules fire) |
| `verdict` | proposals: `already_exists`, `previously_rejected`, `partially_covered`, `no_precedent_found`; questions: `explained`; either: `insufficient_evidence` |
| `summary` | the first conclusions, in reading order |
| `minimum_python_version` | the newest version among the features the input relies on |
| `matched_entities` | seeds with match strength and the exact alias / pattern that matched |
| `ranked_peps` | top PEPs with `score_breakdown` (every path, status weight, centrality) |
| `conclusions` | rule name, statement, confidence, PEPs, evidence snippets |
| `citations` | every PEP used, its URL, which rules used it and which sections |
| `trace` | every match, traversal step, score calculation and inference attempt |

## Tests

```bash
python -m unittest discover tests
```

30 tests: header parser, section splitter, reference finder, graph walk and
serialisation, plus three reasoner smoke tests that run when
`knowledge_state.json` exists.

## Environment variables

None. There are no API keys, config files or network calls at build or ask
time.

## Where to look

* `knowledge_state.json` — the knowledge itself. `meta` (sources, build time,
  slice), `schema` (every type with its docstring), `stats` (counts, most
  connected nodes), `entities` (grouped by type, human-readable labels),
  `relationships` (with evidence). No raw PEP text blobs.
* `examples/inputs/` and `examples/outputs/` — six worked inputs: two "why"
  questions, three proposals, and the question from the design's sanity check
  that no single PEP answers on its own (`06_typeddict_optional_keys`).
* `notes/design.md` — the knowledge model and the reasoning procedure.
* `notes/iterations.md` — what was weak in early outputs, what changed, effect.
