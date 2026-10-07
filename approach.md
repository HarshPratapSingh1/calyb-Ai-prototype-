# Approach

This document explains the choices behind `pep-knowledge-graph`: which PEPs
were used, how they were turned into a graph, how a new input is answered,
and what was left out on purpose. The bullet-point version with more numbers
is in `notes/approach_outline.md`; the iteration history is in
`notes/iterations.md`.

## 1. The data subset

The slice is Python's static typing system: the `typing` module, annotations,
generics, protocols and type checkers. Thirty PEPs are included
(`data/pep_index.json`), covering Python 3.5 to 3.14. Twenty-eight are the
well-known typing PEPs from 483 (*The Theory of Type Hints*) to 742
(*TypeIs*). Two were added deliberately because they were *not* accepted:
PEP 677 (*Callable Type Syntax*, rejected) and PEP 724 (*Stricter Type
Guards*, withdrawn in favour of 742). Without them, a rule such as "this was
rejected before in PEP X" would have nothing real to fire on.

Typing was a good slice for a graph for two reasons. First, its PEPs form
visible families — TypedDict (589 → 655 → 692 → 705), narrowing (647 → 724 →
742), generics (484 → 585 → 646 → 695 → 696), annotation evaluation (3107 →
484/526 → 563 → 649) — and the interesting questions are about how those
families evolved. Second, typing PEPs have unusually rich *Rejected Ideas*
sections, which is where the "negative" knowledge lives.

The raw sources are the `.rst` files from `github.com/python/peps` (the same
files peps.python.org renders). They are committed under `data/raw/peps/` so
that `build` is offline and reproducible; `python -m src.ingest --verify`
cross-checks every index entry against its PEP header.

## 2. The knowledge model

A skim of five PEPs (`notes/skim_notes.md`) shaped the model. Two facts
mattered most: header fields give almost no structure (three
`Replaces`/`Superseded-By` links in thirty PEPs, zero `Requires`), and the
most useful negative knowledge — the alternatives a PEP considered and turned
down — usually has no PEP number at all. So the body text has to carry the
graph, and rejected ideas have to be first-class entities.

**Entities.** *PEP* (from headers). *Construct* — an importable typing name
such as `TypedDict` or `ParamSpec`, found as code spans. *Syntax* — notation
rather than a name: `X | Y`, `list[int]`, `class C[T]`, `(int) -> str`, and
the never-adopted angle brackets. *Problem* — a recurring motivation such as
"Union[X, Y] is verbose" or "a TypedDict cannot mark individual keys as
required", matched by cue phrases in Abstract, Motivation and Rationale
sections. *RejectedIdea* — one per subsection (or bullet) of a rejected-ideas
section, with its disposition (rejected, postponed, objection) and the
sentence that gives the reason. *PythonVersion* from the header, and
*TypeChecker* from implementation sections.

Constructs, syntax forms and problems are curated tables in `src/extract.py`.
That was a deliberate choice: counting how often names appear does not work,
because example classes like `Movie` and `MyType` appear as often as real
constructs. Each table entry says how the thing shows up in PEP text and how a
person would refer to it in a question.

**Relationships.** Nine directed types, each carrying evidence — the PEP, the
section heading path, the rule that fired and the text snippet:
SUPERSEDES (headers), BUILDS_ON (any reference outside rejected sections,
weighted by the section it sits in and boosted by cue phrases such as
"introduced in" or "builds on"), INTRODUCES (one introducer per construct or
syntax, chosen by a priority ladder from explicit cross-references down to
earliest mention), EXTENDS (a later PEP that changes something another PEP
introduced), ADDRESSES_PROBLEM, REJECTED_ALTERNATIVE, CONCERNS (what a
rejected idea was about — the join that makes ideas reachable), AVAILABLE_IN
and IMPLEMENTED_BY.

The build produces 280 entities and 603 relationships. Every edge can be
traced back to the raw text through its evidence, and the rule names in
`knowledge_state.json` are the names used in `notes/design.md` and the code.

## 3. From raw text to graph

All parsing is hand-written. A header parser reads `Field: value` lines with
indented continuations. A section splitter recognises RST headings (a line
followed by an underline of one repeated character, with nesting decided by
the order in which underline characters first appear) and keeps the heading
path of every section. A reference finder handles the four forms seen in the
corpus — `:pep:\`484\``, the labelled role `:pep:\`text <484#anchor>\``,
plain `PEP 484`, and the plural `PEPs 484, 526, and 544` — on whole sections
so roles that wrap across lines are caught, while skipping RST label lines and
self-references.

The mapping rules run per PEP, with one cross-PEP decision: which PEP
*introduced* each construct. Several wrong attributions in the first build
were each fixed with a general rule rather than a patch — only Standards Track
PEPs can introduce a feature; a PEP whose title names a syntax form is about
notation, not the construct; code blocks are stripped before any sentence
rule runs. The tradeoff throughout was precision and reviewability over
coverage: a question about a typing topic outside the tables returns
`insufficient_evidence` rather than a guess.

## 4. Answering a new input

`python -m src.cli ask "<text>"` runs five steps and records every one in a
`trace` array.

1. **Classify** the input as a question or a proposal (question words, a
   trailing `?`, or proposal verbs). This changes how PEP status is weighted —
   a rejected PEP is a strong precedent for a proposal but weak evidence for a
   "why" question — and which inference rules apply.
2. **Match** the text to entities: syntax patterns on the raw text, construct
   names and aliases, problem cues, and keyword overlap with rejected-idea and
   PEP titles. Each seed records its strength and the exact alias or pattern
   that matched.
3. **Traverse** at most two hops: from a construct or problem back to the PEPs
   that introduce, extend or address it, through CONCERNS to the rejected ideas
   that mention it, and from each PEP to what it builds on or supersedes. Every
   path is kept, not just the best one.
4. **Score** each candidate PEP as the sum of its path strengths (match
   strength × edge weights × 0.6 per hop), times a status weight, times a
   centrality factor from how many PEPs build on it. The breakdown is in the
   output.
5. **Infer** with seven rules that combine facts no single PEP states:
   prior rejection, already provided, dependencies and minimum version,
   superseded sources, design rationale with the alternatives turned down,
   "postponed in PEP A, delivered by PEP B", and the later PEPs that extended a
   feature. A relevance filter keeps an idea only if it was reached through a
   syntax form, a problem or a direct keyword match, or shares a keyword with
   the input beyond the construct's own name — otherwise every TypedDict idea
   would surface for every TypedDict question.

The sanity check from the design is input 06: *"Was it ever rejected to let a
TypedDict mark individual keys as required or optional, and since which
version can I do it?"* PEP 589 postponed exactly this ("No proposed syntax was
clear enough"); PEP 655 delivered `Required`/`NotRequired` in 3.11 but never
says 589 had postponed it. The path `problem:typeddict-partial-keys ←CONCERNS←
idea:589/… ` joined with `pep:655 →ADDRESSES_PROBLEM→` the same problem fires
the LATER_RESOLVED rule, and the minimum version comes out as 3.11 rather than
TypedDict's 3.8. The full trace is in `examples/outputs/06_typeddict_optional_keys.json`.

## 5. What was deliberately not built

No NLP library, embeddings or learned weights: a five-suffix stemmer, curated
aliases and explicit regexes, so every match can be named. No sentence-level
understanding of references: section position plus a cue boost is a robust,
explainable proxy. No author, date or discussion-thread entities, and no
reasoning over type checkers — they add nothing to the kinds of input the
engine is for. No graph library: the graph is three dictionaries and a
twenty-line breadth-first walk.

## 6. What to build next

The typing specification at typing.python.org now supersedes details in
several PEPs; a `SPECIFIED_IN` edge would keep answers current. The same
alternative is often rejected in more than one PEP (angle brackets in 484 and
695); a `SAME_IDEA_AS` edge would merge them and strengthen the prior-rejection
rule. Problem detection could be partly mined from Motivation headings instead
of curated cues. And the six examples should grow into an evaluation set with
expected verdicts and PEPs, run as tests, so rule changes cannot silently
regress the outputs. The parsers and rule shapes are slice-agnostic; only the
three tables are typing-specific, so a second slice would mostly mean writing
new tables.
