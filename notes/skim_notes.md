# Skim notes (Phase 1, step 4)

Skimmed in full-ish: **484, 544, 585, 604, 695** (header, Abstract, Motivation,
Rejected Ideas, PEP mentions). Corpus-wide counts below were taken with quick
greps / a throwaway script over all 30 downloaded files, to check that what I
saw in the five generalises.

## 1. Header fields

Every file starts with an RFC-822-style block, `Field: value`, ending at the
first blank line. Continuation lines are indented (seen in `Author:` and
`Post-History:`).

| Field | Present in | Use for the graph |
|---|---|---|
| `PEP`, `Title`, `Status`, `Type`, `Created` | all 30 | PEP node attributes |
| `Topic` | all 30 (`Typing`; 561 is `Packaging, Typing`) | confirms the slice |
| `Python-Version` | 29/30 (not 483, which is Informational) | **AVAILABLE_IN → PythonVersion** |
| `Superseded-By` | 563 only (`649, 749`) | **SUPERSEDES** (reverse direction) |
| `Replaces` | 649 (`563`), 742 (`724`) | **SUPERSEDES** |
| `Requires` | none of our 30 | keep the rule, expect 0 edges |
| `Resolution` | most accepted/rejected | URL only; useful as evidence for status |
| `Author`, `Sponsor`, `BDFL-Delegate`, `Discussions-To`, `Post-History` | most | not needed for reasoning; skip |

Takeaway: **header fields give us very few edges** (3 supersede/replace links
in 30 PEPs, zero `Requires`). Almost all of the graph's structure has to come
from references in the body text.

Statuses in the slice: 27 Final, 1 Superseded (563), 1 Rejected (677),
1 Withdrawn (724). Values are stable vocabulary, good for a status weight.

## 2. Section structure (RST)

- Headings are a text line followed by an underline of one repeated character
  at least as long as the title. `=` is always top level in our files; `-` is
  second level. `~` or `^` appear as third level in 7 PEPs (591, 593, 612,
  677, 692, 696, 698).
- In RST the level is set by **order of first appearance** of each underline
  char in that document, not by the char itself. Our files happen to be
  consistent (`=` then `-` then `~`/`^`), but the splitter should assign levels
  by first appearance to be safe.
- The standard top-level spine: Abstract → Motivation → Rationale → Specification
  → Backwards Compatibility → (Security / How to Teach) → Reference
  Implementation → Rejected Ideas → (Open Issues) → Acknowledgements → Footnotes
  → Copyright. Not every PEP has every section and names vary.
- **Rejected-ideas sections have ~20 spellings**: `Rejected Ideas` (9),
  `Rejected Alternatives` (8), lowercase variants, `Rejected/Postponed Ideas`,
  `Rejected/deferred Ideas`, `Rejected or out-of-scope ideas`,
  `Rationale and Rejected Ideas`, `Objections and responses` (604),
  `Alternatives`, `Alternative Syntax`… A keyword match on
  `reject | objection | alternative | postponed | deferred` in the heading
  catches all of them.
- Inside a rejected section, **each second-level subsection is one named
  rejected idea**, usually with a reason sentence ("This option was rejected
  because…"). Examples: 695 → *Prefix Clause*, *Angle Brackets*, *Bounds
  Syntax*, *Explicit Variance*; 585 → *Do nothing*, *Generics erasure*;
  544 → *Make every class a protocol by default*; 604 → *Add a new operator for
  Union?*, *Extend isinstance() to accept Union?*. These are valuable and have
  **no PEP number**, so they need their own entity type, not just PEP→PEP edges.
- Noise to skip: RST labels like `.. _PEP 544 rationale:` (a false
  self-reference), code blocks after `::`, `.. code-block::` directives,
  footnote targets.

## 3. How PEPs reference each other

Three textual forms seen (counts across all 30 files, self-references excluded
in the script):

| Form | Count | Example |
|---|---|---|
| `:pep:\`484\`` (Sphinx role) | 276 | `Type hints introduced in :pep:\`484\`` |
| `:pep:\`label <484#anchor>\`` | part of the above | `:pep:\`spec of PEP 484 <484#storing-and-distributing-stub-files>\`` — number is inside `<>`, label text may itself contain "PEP 484" |
| plain `PEP 484` | 42 | mostly inside label text and prose in older PEPs |
| `` `PEP 484 <url>`_ `` | 0 | the requested RST-link form does not occur in this corpus; keep the rule anyway |
| `PEPs 484, 526, 544, 560, and 563` | 1 (585 abstract) | plural list — the basic `PEP[\s-]?(\d+)` regex misses all five numbers (the `s` breaks it); needs its own rule |

Gotchas for the reference finder:
- A `:pep:` role can **wrap across a line break** (604: ``:pep:`expose`` /
  continuation). Run the regex over the section text, not line by line.
- The label form contains the number twice (`PEP 484` in the label and `484`
  in the target) → de-duplicate by (target, character span).
- Ignore self-references and RST label lines.

Where references live (top-level section of each mention, all 30 PEPs):
Specification and its subsections ≈ 126, **Rejected ideas ≈ 39**,
Rationale 31, Motivation 27, **Abstract 25**, Backwards compatibility 19,
Implementation 11, rest < 5.

Most-referenced targets: **484 (79)**, 563 (49), 3107 (16), 526 (16),
589 (11), 649 (10), 646 (9), 647 (9), 724 (8), 483 (7), 557 (7), 591 (7),
612 (7). Several heavily cited PEPs are **outside the slice** (3107 function
annotations, 557 dataclasses, 560 `__class_getitem__`, 749) → represent them as
lightweight stub nodes so edges aren't lost.

Cue words on the same line as a reference are sparse: "introduc(ed)" 31,
"deprecat" 6, "similar to / like" 5, "supersed/replac" 3, "reject/withdraw" 3,
"extend" 1. The dominant phrasing is **"`X` introduced in :pep:`N`"**, which is
an excellent signal for *construct X ← INTRODUCED BY PEP N*. Other cues will
need a sentence window rather than a single line.

## 4. Recurring concepts

- **Constructs** (``typing`` names in double backticks) dominate: Literal,
  LiteralString, Callable, TypeVar, Union, Any, TypeVarTuple, Tuple, TypedDict,
  TypeIs, TypeGuard, Self, ParamSpec, List, Generic, Annotated, Unpack,
  Required/NotRequired, Final, ReadOnly, ClassVar, Optional, Protocol, Dict,
  Concatenate, TypeAlias.
  Frequency alone is noisy (example names like `Movie`, `MyType`, `Shape`,
  `Array` rank high), so constructs need a **curated alias list**, not
  automatic discovery.
- **Syntax forms**: `X | Y` (604), `list[int]` (585), `x: int` (526),
  `class C[T]` / `def f[T]` / `type X = …` (695), `*Ts` (646),
  `(int) -> str` (677, rejected), `from __future__ import annotations` (563).
- **Problems / motivations** repeat across PEPs: verbose/duplicated generics
  (`typing.List` vs `list`, 585), import overhead and forward references
  (563, 649), confusing TypeVar scoping and variance (695), lack of structural
  typing (544), unsafe narrowing (647 → 724 → 742), precise `**kwargs` (692),
  TypedDict optional/read-only keys (655, 705).
- **Families / lineages** are clearly visible and will make good multi-hop
  paths: TypedDict 589 → 655 → 692 → 705; narrowing 647 → 724 (withdrawn) →
  742; annotation evaluation 3107 → 484/526 → 563 (superseded) → 649;
  generics 484 → 585 → 646 → 695 → 696.
- **Type checkers** are named often: mypy (24 PEPs), pyright (12), Pyre (7),
  pytype (4), PyCharm (3). Mostly in Reference Implementation / Specification.
- **Python versions**: `Python-Version` header is the reliable source; prose
  "Python 3.x" mentions are mostly about backports or compatibility and would
  add noise.

## 5. Which section holds which kind of link (summary for the design)

| Section | Kind of knowledge |
|---|---|
| Header | status, version (AVAILABLE_IN), Replaces / Superseded-By (SUPERSEDES) |
| Abstract | what the PEP **introduces** and which PEPs it **builds on** (e.g. 544: "Type hints introduced in PEP 484… However PEP 484 only specifies nominal subtyping") |
| Motivation / Rationale | the **problem** addressed; references here mean "builds on / fixes a limitation of" |
| Specification | construct definitions; references are mostly "uses / interacts with" (weaker) |
| Rejected Ideas / Objections | **rejected alternatives** (named subsections + reason sentence) and PEPs considered as alternatives |
| Backwards Compatibility | deprecations, interaction with 563/649 |
| Reference Implementation | type checkers that implement it |

## 6. Implications for Phase 2

1. Header edges are scarce → body references, weighted by section, must carry
   most relationships; evidence = (PEP, section, snippet).
2. Need non-PEP entities for constructs, syntax, problems, checkers, versions
   and **rejected ideas**, otherwise "was this rejected before?" can only be
   answered for 677/724.
3. Construct and problem detection = curated alias tables (explicit rules),
   plus the "introduced in :pep:`N`" pattern.
4. Out-of-slice PEPs (3107, 557, 560, 749, …) become stub nodes.
