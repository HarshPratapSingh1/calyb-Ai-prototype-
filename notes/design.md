# Knowledge model and reasoning design (Phase 2)

Status: **proposal for review**. No pipeline code exists yet. Every rule below
is meant to be implemented with regexes, string handling and the hand-written
tables in `src/extract.py`.

Grounding: every example here was checked against the 30 downloaded PEPs (see
`notes/skim_notes.md`). The design has to work around two facts from the skim:

1. Header fields give only 3 edges (563→649/749, 649→563, 742→724). Most
   structure has to come from body references and named sections.
2. The most useful "negative" knowledge (rejected ideas) usually has no PEP
   number. It lives in rejected-ideas subsections and bullet lists.

---

## 1. Entity types (6)

IDs are lowercase, prefixed by type, and stable across builds. Every entity
gets a human-readable `label` and a `sources` list: the PEP number and section
where it was first seen.

### 1.1 `PEP`
A Python Enhancement Proposal.

| | |
|---|---|
| ID | `pep:484` |
| Attributes | `number`, `title`, `status`, `type`, `python_version`, `created`, `in_slice` (bool), `url` |
| Found in | **Header block**: `PEP`, `Title`, `Status`, `Type`, `Python-Version`, `Created`. Title RST markup is stripped (` ``X | Y`` ` → `X | Y`). |
| Stubs | A PEP that is referenced but not in the index (3107, 557, 560, 749, …) becomes a stub: `in_slice=false`, label `PEP 3107 (outside slice)`, no other attributes. That way edges to it are kept but nothing is invented. |

### 1.2 `Construct`
A named typing feature that you import or use: a class, special form,
qualifier or decorator.

| | |
|---|---|
| ID | `construct:typeddict`, `construct:paramspec`, `construct:override` |
| Attributes | `name` (canonical spelling), `aliases`, `kind` (`special_form`, `class`, `qualifier`, `decorator`, `function`, `alias`) |
| Found in | A **curated alias table** (`CONSTRUCTS` in `extract.py`, about 40 entries such as `TypedDict`, `Required`, `NotRequired`, `ReadOnly`, `TypeGuard`, `TypeIs`, `ParamSpec`, `Concatenate`, `TypeVarTuple`, `Unpack`, `Self`, `LiteralString`, `Literal`, `Final`/`final`, `ClassVar`, `Annotated`, `Protocol`, `runtime_checkable`, `TypeAlias`, `override`, `deprecated`, `dataclass_transform`, `Union`, `Optional`, `Callable`, `TypeVar`, `Generic`, `Any`, `NoReturn`, `NewType`, `List`/`Dict`/`Tuple`/`Set`/`FrozenSet` …). In PEP text an alias matches only when it appears **as code**: ` ``Name`` `, ` ``typing.Name`` ` or ` ``Name[...]`` `. |
| Why curated | Counting how often names appear doesn't work: example classes such as `Movie`, `MyType` and `Shape` rank as high as real constructs (skim notes §4). |

### 1.3 `Syntax`
A piece of language-level notation, not an importable name.

| | |
|---|---|
| ID | `syntax:union-pipe`, `syntax:builtin-generics`, `syntax:type-param-list`, `syntax:type-statement`, `syntax:arrow-callable`, `syntax:variable-annotation`, `syntax:future-annotations`, `syntax:star-unpack` |
| Attributes | `notation` (e.g. `X \| Y`), `aliases` (e.g. "pipe", "bar operator"), `patterns` (regexes) |
| Found in | A **curated table** (`SYNTAX` in `extract.py`) of about 10 entries. Each entry has regexes for its code form (e.g. `\b\w+\s*\|\s*\w+` inside a code span for `X \| Y`, `\b(list\|dict\|tuple\|set)\[` for builtin generics, `^\s*type\s+\w+\s*=` for the type statement, `\)\s*->\s*\w` inside a type position for arrow callables) and word aliases for matching user input. |

### 1.4 `Problem`
A recurring motivation: something users or tools struggled with.

| | |
|---|---|
| ID | `problem:verbose-union-syntax`, `problem:duplicate-collection-hierarchy`, `problem:forward-references`, `problem:annotation-runtime-cost`, `problem:no-structural-typing`, `problem:typevar-scoping-confusion`, `problem:verbose-callable-types`, `problem:typeddict-partial-keys`, `problem:untyped-kwargs`, `problem:mutable-typeddict-items`, `problem:unsafe-narrowing`, `problem:decorator-signatures`, `problem:string-injection`, `problem:distributing-type-info`, `problem:accidental-override`, `problem:invisible-deprecations` … (about 18) |
| Attributes | `label` (one-line description), `cues` (regexes), `examples` |
| Found in | A **curated catalog** (`PROBLEMS`). Each entry's cue regexes are matched **only in Abstract, Motivation and Rationale sections** (where a match means "this PEP is about this problem") and in rejected-idea text. Examples: `problem:typeddict-partial-keys` ← `required\b.*\b(potentially[- ]missing\|optional)\|total=False\|each key is required`; `problem:verbose-union-syntax` ← `verbos\w*.*Union\|Union\[X, Y\]`. |

### 1.5 `RejectedIdea`
An alternative that a PEP considered and turned down or postponed.

| | |
|---|---|
| ID | `idea:695/angle-brackets`, `idea:589/per-key-required-flag` (PEP number + slug of the heading or the first words of the bullet) |
| Attributes | `title`, `pep`, `disposition` (`rejected` / `postponed` / `objection`, taken from the heading words), `reason` (the first sentence of the body that contains `reject\|because\|not clear\|confus\|inconsistent\|instead`, capped at about 200 chars), `keywords` (non-stopword tokens of the title) |
| Found in | Sections whose heading matches `reject\|objection\|alternative\|postponed\|deferred` (skim notes §2). **Each child subsection is one idea**, e.g. 695 → *Angle Brackets*, *Prefix Clause*. If the rejected section has **no subsections**, each top-level `* ` bullet becomes one idea instead (589's Rejected Alternatives is all bullets). |

### 1.6 `PythonVersion`

| | |
|---|---|
| ID | `py:3.10` |
| Attributes | `version`, `sort_key` (`(3, 10)`, so 3.10 sorts after 3.9) |
| Found in | `Python-Version` header only. Prose like "Python 3.7" mostly describes backports and adds noise (skim notes §4). |

### Not modelled as entities (deliberately)
- **Type checkers** (mypy, pyright, Pyre…). They're mentioned in 24 PEPs, but
  almost always in "reference implementation in mypy" form, which doesn't help
  answer proposals or "why" questions. We keep a count per PEP as an attribute
  (`checkers_mentioned`) and list it under *build next*.
- **Authors and dates**: no reasoning value for these inputs.

---

## 2. Relationship types (8)

Every edge has the shape:

```json
{"type": "INTRODUCES", "source": "pep:604", "target": "syntax:union-pipe",
 "weight": 1.0,
 "evidence": [{"pep": 604, "section": "Abstract",
               "rule": "intro-cue-in-abstract",
               "snippet": "This PEP proposes overloading the ``|`` operator on types to allow writing ``Union[X, Y]`` as ``X | Y``"}]}
```

If the same (type, source, target) is found more than once, the evidence is
merged into one edge and its weight is the **maximum** of the individual
weights.

| # | Type | Source → Target | Meaning |
|---|---|---|---|
| 1 | `SUPERSEDES` | PEP → PEP | the source replaced the target |
| 2 | `BUILDS_ON` | PEP → PEP | the source relies on or extends the target |
| 3 | `INTRODUCES` | PEP → Construct \| Syntax | the source defined it (or **proposed** it, if the source was rejected or withdrawn) |
| 4 | `EXTENDS` | PEP → Construct \| Syntax | the source changes or adds to something introduced elsewhere |
| 5 | `ADDRESSES_PROBLEM` | PEP → Problem | the source was written to fix this problem |
| 6 | `REJECTED_ALTERNATIVE` | PEP → RejectedIdea | the source considered this alternative and turned it down |
| 7 | `CONCERNS` | RejectedIdea → Construct \| Syntax \| Problem \| PEP | what the rejected idea was about |
| 8 | `AVAILABLE_IN` | PEP → PythonVersion | the source shipped in this version |

That's 8 types rather than about 6, for two reasons. `EXTENDS` is what makes
the TypedDict and narrowing families traversable. `CONCERNS` is what joins a
rejected idea to the later PEP that solved the same thing (see §5). `REQUIRES`
is not a separate type: there are 0 `Requires:` headers in the slice, so the
header rule is kept and folded into `BUILDS_ON` with weight 1.0.

### Extraction rules

**R-SUP (SUPERSEDES)**
- Header `Replaces: N[, M]` on PEP P → `P SUPERSEDES N`. Evidence: `header Replaces`.
- Header `Superseded-By: N[, M]` on PEP P → `N SUPERSEDES P`. Evidence: `header Superseded-By`.
- Expected edges: 649→563, 749(stub)→563, 742→724. Weight 1.0.
- No body-text rule. Phrases like "replaces" in prose are too ambiguous (they
  usually describe syntax, not PEPs).

**R-BLD (BUILDS_ON)**: every reference from PEP P to PEP Q (Q ≠ P) that is
**outside** rejected sections. Weight depends on the top-level section the
reference sits in:

| Section of the reference | Weight |
|---|---|
| header `Requires` | 1.0 |
| Abstract, Motivation | 1.0 |
| Rationale | 0.8 |
| Specification and its subsections, or any other non-boilerplate section | 0.5 |
| Backwards Compatibility, Implementation, How to Teach | 0.4 |
| Acknowledgements, References, Footnotes, Copyright | ignored |

Cue boost: if the same sentence matches
`introduced (in|by)|builds? on|based on|extends?|as defined in|specified in`,
the weight is raised to at least 0.8.

A reference is any of:
- `:pep:\`N\``
- `:pep:\`label <N#anchor>\`` (take N)
- `PEP N` / `PEP-N`
- `` `PEP N <url>`_ ``
- `PEPs N, M, and K` (a plural list; each number counts)

The finder runs on whole section text, not line by line, so roles that wrap
across lines are caught. It ignores RST label lines (`.. _...:`) and
self-references. Matches are de-duplicated by (target, position).

**R-INT (INTRODUCES)**. Each match records which rule fired:
1. *cross-reference*: anywhere in any PEP, a code span followed by
   `(was |were )?(first )?introduced (in|by) :pep:\`N\`` → `pep:N INTRODUCES <construct>`.
   This is the strongest signal (in the skim, 31 lines pair "introduc…" with a PEP reference).
2. *title*: the construct or syntax pattern appears in the PEP's Title.
3. *abstract-cue*: in the Abstract, a sentence matching
   `this pep (introduces|proposes|adds|defines|specifies)` that also contains
   the construct or syntax.

Conflicts: a construct gets **one** introducer. The PEP with rule 1 evidence
wins, then rule 2, then rule 3. If it's still tied, the lowest PEP number wins.
Every other PEP that matched becomes an `EXTENDS` candidate (below).

**R-EXT (EXTENDS)**: PEP P mentions construct or syntax X (as code) in its
Title or Abstract, P is not X's introducer, and X's introducer is a different
PEP. Example: 655's Abstract mentions ` ``TypedDict`` ` → `655 EXTENDS construct:typeddict`. Weight 0.7.

**R-PRB (ADDRESSES_PROBLEM)**: a problem cue matches in P's Abstract,
Motivation or Rationale → `P ADDRESSES_PROBLEM problem:x`. Weight 1.0 for
Abstract/Motivation and 0.7 for Rationale. One edge per (P, problem), with all
matching sentences kept as evidence.

**R-REJ (REJECTED_ALTERNATIVE)**: each RejectedIdea found in P (§1.5) →
`P REJECTED_ALTERNATIVE idea:P/slug`. Evidence is the heading path plus the
reason sentence.

**R-CON (CONCERNS)**: inside each rejected idea's text (heading + body):
- construct or syntax matches → `idea CONCERNS construct/syntax`
- problem cues → `idea CONCERNS problem`
- PEP references → `idea CONCERNS pep:N` (e.g. 604's objection *"Change only PEP 484 …"* → `pep:484`, and its body → `pep:563`)

**R-VER (AVAILABLE_IN)**: header `Python-Version: 3.x` → `P AVAILABLE_IN py:3.x`.
This is also recorded for Rejected/Withdrawn PEPs (meaning "targeted"), but the
reasoner ignores those when computing version requirements.

---

## 3. Reasoning procedure for a new input

Inputs are free text, e.g. *"Why does Python use `X | Y` instead of
`Union[X, Y]`?"*. There are five steps, and each one appends structured records
to `trace`.

### Step 0: Classify
- `question` if the text starts with `why|how|what|when|which|is|does|can`
  or ends with `?`.
- `proposal` if it starts with
  `add|allow|introduce|support|make|let|propose|provide|i propose|we should`.
- Otherwise `proposal`.

The input type changes some weights (status weight in step 3) and which rules
fire (step 4).

### Step 1: Match (text → seed entities)
Normalise: lowercase for words, keep the raw text for code patterns, tokenise
on non-alphanumerics, drop a hand-written stopword list, and do light suffix
stripping (`-s`, `-es`, `-ing`, `-ed`).

| Matcher | Rule | Strength *m* |
|---|---|---|
| Explicit PEP | `PEP\s?-?(\d+)` | 1.0 |
| Syntax pattern | regex from the `SYNTAX` table hits the raw input | 1.0 |
| Syntax alias | e.g. "pipe", "arrow", "angle brackets", "square brackets" | 0.8 |
| Construct, exact case or in backticks | `TypedDict`, `` `Final` `` | 1.0 |
| Construct, lowercase common word | "final", "literal", "self", "override", "protocol" | 0.6 |
| Problem cue | the `PROBLEMS` cue regexes | 0.7 |
| RejectedIdea | keyword overlap `\|in ∩ kw\| / \|kw\|` ≥ 0.5 | 0.8 × overlap |
| PEP title | keyword overlap ≥ 0.5 | 0.6 × overlap |
| Python version | `3\.\d+` | 1.0 (used as a filter, not a seed) |

Seeds are the matches with *m* ≥ 0.4.

### Step 2: Traverse (seeds → candidate PEPs), at most 2 hops

Hop 1 (from the seed back to the PEPs that own it):
- Construct/Syntax ← `INTRODUCES` (w 1.0) and ← `EXTENDS` (w 0.7)
- Problem ← `ADDRESSES_PROBLEM` (w 1.0 / 0.7)
- RejectedIdea ← `REJECTED_ALTERNATIVE` (w 0.9)
- Construct/Syntax/Problem ← `CONCERNS` ← RejectedIdea (w 0.9). This brings in
  rejected ideas the user didn't name directly.
- A PEP seed is itself a candidate at hop 0.

Hop 2 (PEP → context PEPs):
- `BUILDS_ON` outgoing (w = edge weight): what the PEP relies on
- `SUPERSEDES` both directions (w 0.8): what replaced it or what it replaced

`AVAILABLE_IN` is looked up for each candidate but doesn't count as a hop and
doesn't add to the score.

### Step 3: Score

For each candidate PEP *P*:

```
score(P) = [ Σ over paths p reaching P  m(seed_p) × Π_{e∈p} w(e) × λ^{hops(p)} ]
           × S(status(P), input_type)
           × C(P)

λ = 0.6                                  hop decay
C(P) = 1 + 0.5 × in_deg_BUILDS_ON(P) / max_in_deg_BUILDS_ON
                                         centrality, capped at 1.5
S:              proposal   question
  Final           1.0        1.0
  Superseded      0.7        0.8
  Rejected        1.0        0.6       a precedent matters most for proposals
  Withdrawn       0.9        0.6
  stub            0.3        0.3
```

The result keeps the top 6 PEPs with score ≥ 0.15. Each one includes a
`score_breakdown` listing its paths and the factors that make up the score.

### Step 4: Infer (rules that combine several facts)

Each rule produces a conclusion with a `statement` (filled from a template),
`confidence` (`high` if every fact used comes from a seed with *m* ≥ 0.8 and
evidence weight ≥ 0.8, `medium` if *m* ≥ 0.6, otherwise `low`), the list of
`peps` used, and an `evidence` list.

| Rule | Fires when | Statement template |
|---|---|---|
| **I1 PRIOR_REJECTION** | a RejectedIdea or a Rejected/Withdrawn PEP is reached with path strength ≥ 0.4 | "A similar idea (*{idea}*) was considered in PEP {N} and rejected: {reason}." |
| **I2 ALREADY_PROVIDED** | proposal only: a matched Construct/Syntax has an introducer with status Final | "This is already covered by {construct} from PEP {N}, available since Python {v}." |
| **I3 DEPENDS_ON** | for the matched constructs and syntax (and the top PEP's strong `BUILDS_ON` targets): take each introducer's `AVAILABLE_IN` | "Relies on {X} (PEP {N}, Python {v}). Minimum version: {max v}." The output's `minimum_python_version` is set from this. |
| **I4 SUPERSEDED_SOURCE** | a cited PEP has an incoming `SUPERSEDES` edge | "PEP {old} is superseded by PEP {new} (Python {v}); prefer {new}'s semantics." |
| **I5 DESIGN_RATIONALE** | question only: the top-scoring Syntax/Construct has an introducer P | "{X} was introduced in PEP {P} (Python {v}) to address *{problem}*. Alternatives rejected along the way: {ideas from P and from PEPs P builds on}." |
| **I6 LATER_RESOLVED** | a RejectedIdea from PEP A (`disposition` rejected or postponed) `CONCERNS` a Problem or Construct that a **later** PEP B `ADDRESSES` or `INTRODUCES`/`EXTENDS` | "PEP {A} left this out ({reason}); PEP {B} later added it in Python {v}." |

Rules I1 + I2 together also give the proposal verdict: `previously_rejected`,
`already_exists`, `partially_covered` (I2 fires only for some matched
elements) or `no_precedent_found`.

### Step 5: Output (JSON schema)

```jsonc
{
  "input": "string",
  "input_type": "proposal | question",
  "verdict": "already_exists | previously_rejected | partially_covered | no_precedent_found | explained | insufficient_evidence",
  "summary": "2–4 template sentences assembled from the highest-confidence conclusions",
  "minimum_python_version": "3.10 | null",
  "matched_entities": [
    {"id": "syntax:union-pipe", "type": "Syntax", "label": "X | Y union syntax",
     "strength": 1.0, "matched_on": "pattern '\\w+ \\| \\w+' on 'X | Y'"}],
  "ranked_peps": [
    {"pep": 604, "title": "...", "status": "Final", "python_version": "3.10",
     "score": 1.62,
     "score_breakdown": {"paths": [["syntax:union-pipe", "<-INTRODUCES-", "pep:604"]],
                         "path_sum": 1.0, "status_weight": 1.0, "centrality": 1.12}}],
  "conclusions": [
    {"rule": "I5_DESIGN_RATIONALE", "statement": "...", "confidence": "high",
     "peps": [604, 484], "evidence": [{"pep": 604, "section": "Motivation", "snippet": "..."}]}],
  "citations": [
    {"pep": 604, "title": "...", "url": "https://peps.python.org/pep-0604/",
     "used_for": ["I5_DESIGN_RATIONALE"], "sections": ["Abstract", "Motivation"]}],
  "trace": [
    {"step": "classify", "result": "question", "rule": "starts with 'why'"},
    {"step": "match", "entity": "syntax:union-pipe", "strength": 1.0, "via": "..."},
    {"step": "traverse", "from": "syntax:union-pipe", "edge": "INTRODUCES", "direction": "in", "to": "pep:604", "hop": 1, "path_strength": 1.0},
    {"step": "score", "pep": 604, "formula": "(1.0) × S=1.0 × C=1.12", "score": 1.12},
    {"step": "infer", "rule": "I5_DESIGN_RATIONALE", "fired": true, "inputs": ["..."]}
  ]
}
```

Every citation entry links to an evidence record that already exists in the
graph. The reasoner never quotes text that isn't in an edge's evidence.

---

## 4. Test inputs (none are PEPs)

| # | File | Type | Text | What a good answer contains (written before any code) |
|---|---|---|---|---|
| 01 | `01_typed_dict_literal.txt` | proposal | *Add a built-in syntax for typed dict literals, so I can write `{"name": str, "age": int}` directly as a type instead of declaring a TypedDict class.* | TypedDict (589, 3.8) as the existing mechanism. 589's *Alternative Syntax* (the functional form) as partial coverage. The family 655/692/705 as related work. `partially_covered` or `already_exists`, not `no_precedent_found`. |
| 02 | `02_union_pipe.txt` | question | *Why does Python use `X \| Y` instead of `Union[X, Y]`?* | 604 introduces `X \| Y` (3.10) to fix verbose `Union` (604 Motivation). `Union` came from 484. 604's objections (*Add a new operator…*, *Change only PEP 484…*). I4: the `from __future__ import annotations` route it mentions (563) is superseded by 649. |
| 03 | `03_arrow_callable.txt` | proposal | *Allow writing callable types with an arrow, like `(int, str) -> bool`, instead of `Callable[[int, str], bool]`.* | I1: PEP 677 proposed exactly this and was **Rejected**. `Callable` from 484 is the existing form. 612 (`ParamSpec`/`Concatenate`) as what any new syntax must also cover. Verdict `previously_rejected`. |
| 04 | `04_angle_brackets.txt` | question | *Why does Python use square brackets for generics instead of angle brackets like Java or C++?* | Two independent rejections combined: 484 *Which brackets for generic type parameters?* (parser ambiguity with `a < b > c`) and 695 *Angle Brackets* (the scanner doesn't pair `<>`, inconsistent with `list[int]`). 695's `class C[T]` syntax (3.12) as where the choice was re-confirmed. |

A spare input for Phase 3 iterations (so we don't tune only to these four):
*"Propose a TypeGuard variant that also narrows the type in the else branch"*.
Expected: 742 `TypeIs` already provides it (3.13), 724 tried a stricter
`TypeGuard` and was withdrawn, and both build on 647.

---

## 5. Sanity check: a question no single PEP answers

**Question:** *"Was it ever rejected to let a TypedDict mark individual keys as
required or optional, and if so, can I do it now and since which Python
version?"*

What each PEP says on its own:
- **589** (3.8), Rejected Alternatives, bullet: *"There is no way to
  individually specify whether each key is required or not. No proposed syntax
  was clear enough, and we expect that there is limited need for this."* It
  can't know what came later.
- **655** (3.11), Motivation: *"It is not uncommon to want to define a
  TypedDict with some keys that are required and others that are
  potentially-missing … This PEP introduces two new type qualifiers,
  `typing.Required` and `typing.NotRequired`."* It cites 589 only for
  notation and totality (lines 21, 23, 68, 169, 181). **It never says that 589
  rejected or postponed the idea.**

So "postponed in 3.8 for lack of a clear syntax, then solved in 3.11 by
`Required`/`NotRequired`" is a conclusion that only exists in the graph.

**Path:**

```
idea:589/no-way-to-individually-specify-required      (RejectedIdea, disposition=postponed)
   ──CONCERNS──────────▶ problem:typeddict-partial-keys     cue "each key is required" in the bullet
   ◀─ADDRESSES_PROBLEM── pep:655                            cue "required … potentially-missing" in 655 Motivation
   ──INTRODUCES────────▶ construct:required, construct:notrequired   abstract-cue rule
   ──AVAILABLE_IN──────▶ py:3.11                            header
   ──EXTENDS───────────▶ construct:typeddict ◀─INTRODUCES── pep:589 ──AVAILABLE_IN──▶ py:3.8
```

The rule that fires is **I6 LATER_RESOLVED**: idea from A=589 CONCERNS
problem P, which is ADDRESSED by B=655, and 655 > 589 →
*"PEP 589 left this out ('No proposed syntax was clear enough'); PEP 655 later
added it via `Required`/`NotRequired` in Python 3.11."* **I3 DEPENDS_ON** adds
"needs TypedDict (589, 3.8); minimum 3.11".

This only works because of two design choices made for exactly this reason:
`RejectedIdea` is an entity, and `CONCERNS` connects ideas to problems.
Without them the graph only has `655 BUILDS_ON 589`, which can't tell "built
on" apart from "finally did what was postponed".

The same pattern occurs at least twice more in the corpus, which is a good sign
the rule generalises:
- 589's bullet on `**kwargs` → `problem:untyped-kwargs` ← 692 (3.12)
- 604's objection citing 563 → I4: 563 is superseded by 649 (3.14)

---

## 6. Open points for you to decide

1. **8 relationship types instead of about 6.** I'd keep `EXTENDS` and
   `CONCERNS` (reasons in §2). The alternative is to fold `EXTENDS` into
   `BUILDS_ON`-to-construct, but that weakens the family traversal.
2. **No TypeChecker entity.** Is that OK with you, given the slice description
   names "type checkers"? It's cheap to add (about 5 aliases plus a
   `CHECKED_BY` edge from the Reference Implementation section), but it doesn't
   help any of the 4 test inputs.
3. **One `INTRODUCES` type that also covers "proposed"** for Rejected and
   Withdrawn PEPs, with the status read at reasoning time. The alternative is a
   separate `PROPOSES` type, which would mean 9 types.
4. **Curated tables (constructs, syntax, problems)** are the main hand-written
   knowledge. They live in one place in `extract.py`, and every entry will
   carry a comment saying which PEP text it was derived from.
