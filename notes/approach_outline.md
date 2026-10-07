# approach.md outline (bullet-point facts; polish into prose)

## 1. Data subset and why

- Slice: Python's static typing system — the `typing` module, annotations,
  generics, protocols, type checkers.
- 30 PEPs (`data/pep_index.json`): the 28 suggested candidates + 677 (Callable
  Type Syntax, **Rejected**) + 724 (Stricter Type Guards, **Withdrawn**).
  - Why the two extras: without a rejected/withdrawn PEP the "this was rejected
    before" rule has no real case. 677 is the precedent for input 03; 724 → 742
    is a supersession chain inside the slice.
- Statuses: 27 Final, 1 Superseded (563), 1 Rejected, 1 Withdrawn.
  Versions 3.5 → 3.14. All titles/statuses verified against peps.python.org
  (`python -m src.ingest --verify` = 0 mismatches).
- Source: raw `.rst` from github.com/python/peps (the files peps.python.org is
  rendered from). Committed under `data/raw/peps/` so `build` is offline and
  reproducible.
- Why typing: the PEPs form visible families (TypedDict 589→655→692→705;
  narrowing 647→724→742; generics 484→585→646→695→696; annotation evaluation
  3107→484/526→563→649) and have unusually rich *Rejected Ideas* sections —
  both are what a graph can exploit and a single document cannot.
- Out-of-slice PEPs that are cited (3107, 557, 560, 749, 411, …) become stub
  nodes (`in_slice: false`) so edges to them are kept without inventing data.

## 2. Entity and relationship types, with reasoning

### Entities (7)
- **PEP** — header fields. Attributes: number, title, status, type,
  python_version, created, in_slice, url.
- **Construct** (33) — importable typing names (`TypedDict`, `ParamSpec`,
  `Final`, …). Curated alias table; found as code spans ``` ``Name`` ```.
  Reason: frequency-based discovery fails (example class names like `Movie`
  rank as high as real constructs — skim notes §4).
- **Syntax** (11) — notation, not names: `X | Y`, `list[int]`, `class C[T]`,
  `type X = …`, `(int) -> str`, `x: int`, `from __future__ import annotations`,
  `*Ts`, `**kwargs: Unpack[TD]`, angle brackets (never adopted), square
  brackets. Found by regex in code spans/blocks, or by name in prose for the
  bracket forms.
- **Problem** (25) — recurring motivations, each with cue regexes matched only
  in Abstract/Motivation/Rationale and in rejected-idea text. Reason: "why"
  questions and proposals are about problems, and the same problem recurs
  across PEPs (589 postponed it, 655 solved it).
- **RejectedIdea** (150) — one per leaf subsection of a rejected-ideas section,
  or per bullet when there are no subsections (589). Attributes: disposition
  (rejected / postponed / objection), reason sentence, keywords. Reason: the
  most valuable negative knowledge has no PEP number.
- **PythonVersion** (10) — `Python-Version` header only (prose "Python 3.x"
  is mostly about backports).
- **TypeChecker** (3 seen: mypy, pyright, Pyre) — named in implementation
  sections. Context only; no reasoning rule uses it.

### Relationships (9), all with evidence {pep, section, rule, snippet}
- **SUPERSEDES** (3) — `Replaces:` / `Superseded-By:` headers only. 649→563,
  749→563, 742→724.
- **BUILDS_ON** (88) — any PEP reference outside rejected sections, weighted
  by the top-level section: Abstract/Motivation 1.0, Rationale 0.8, other 0.5,
  Backwards-compat/Implementation 0.4, acknowledgements/references ignored;
  cue boost to ≥0.8 for "introduced in / builds on / extends / as defined in".
  `Requires:` header would be 1.0 but occurs 0 times.
- **INTRODUCES** (43) — one introducer per construct/syntax, chosen by
  priority: (1) "``X`` introduced in :pep:`N`" cross-reference, (2) Title,
  (3) "This PEP introduces ``X``" in the Abstract, (4) section heading,
  (5) earliest mention as code. Ties → lowest PEP number. Only Standards Track
  PEPs; a PEP whose title names a syntax form does not introduce a construct.
- **EXTENDS** (17) — title/abstract mentions a construct another PEP
  introduced (655/692/705 → TypedDict, 742 → TypeGuard, 696 → TypeVar…).
- **ADDRESSES_PROBLEM** (32) — problem cue in Abstract/Motivation (1.0) or
  Rationale (0.7).
- **REJECTED_ALTERNATIVE** (150) — PEP → each of its RejectedIdeas.
- **CONCERNS** (212) — RejectedIdea → constructs/syntax/problems/PEPs
  mentioned in it. The join that makes ideas reachable from the input.
- **AVAILABLE_IN** (29) — `Python-Version` header.
- **IMPLEMENTED_BY** (29) — checker named in implementation sections.

## 3. How raw data was mapped; tradeoffs

- Pipeline per PEP (`src/extract.py`): `parse_header` → `split_sections`
  (RST title + underline, levels by first-appearance of the underline char,
  heading path kept) → `find_references` (four forms incl. `:pep:` roles that
  wrap lines, label form `:pep:\`text <484#x>\``, plural `PEPs 484, 526 and
  544`; runs on section text not lines; skips `.. _label:` lines and
  self-references) → mapping rules R-SUP, R-VER, R-BLD, R-INT, R-EXT, R-PRB,
  R-REJ, R-CON, R-IMP.
- Only one cross-PEP decision: `resolve_introduces()` in `cli.build`.
- Evidence rule names in `knowledge_state.json` match the names in
  `notes/design.md` and the code, so any edge can be traced to its rule.
- Tradeoffs:
  - Curated tables (≈45 construct/syntax entries, 25 problems) vs discovery:
    chose curation for precision and reviewability; the cost is coverage —
    topics outside the tables return `insufficient_evidence`.
  - Section-weighted references vs parsing each sentence's meaning: section
    position is a robust, explainable proxy; the cue boost recovers the most
    common explicit phrasings.
  - Rejected ideas from subsections/bullets: ~20 heading spellings handled by
    one regex; a few sections ("Alternative Syntax" in 589) are deliberately
    not treated as rejected.
  - Header fields give only 3 edges, so the body-text rules carry the graph.
  - Prose code blocks are stripped before sentence rules (a `# PEP 589` comment
    once made 589 "introduce" `Required`).

## 4. Handling a new input, step by step (worked example: input 06)

Input: *"Was it ever rejected to let a TypedDict mark individual keys as
required or optional, and if so, can I do it now and since which Python
version?"*

1. **Classify** → `question` (`starts with a question word or ends with '?'`).
2. **Match** (trace):
   - `construct:typeddict` 1.0 — canonical name `TypedDict`
   - `problem:typeddict-partial-keys` 0.7 — alias "required or optional"
   - `idea:655/marking-required-or-potentially-missing-keys-with` 0.4 —
     title keyword overlap 0.50
   - `idea:705/preventing-unspecified-keys-in-typeddicts` 0.4
3. **Traverse** (≤2 hops, λ=0.6):
   - `problem:typeddict-partial-keys <-CONCERNS- idea:589/there-is-no-way-to-individually-specify`
     hop 1, strength 0.7×0.8×0.6 = **0.336**
   - `… <-REJECTED_ALTERNATIVE- pep:589` hop 2, 0.181
   - `construct:typeddict <-INTRODUCES- pep:589` 0.6; `<-EXTENDS- pep:655`
     0.42; `problem … <-ADDRESSES_PROBLEM- pep:655` 0.42; plus BUILDS_ON
     context from 589/655 (484, 526, …).
4. **Score**: `pep:589` = path_sum 3.456 × status[Final] 1.0 × centrality 1.09
   = **3.761**; `pep:655` = 1.960 × 1.0 × 1.06 = **2.076**; then 705, 692,
   484, 591.
5. **Infer**:
   - relevance-filter keeps 4 ideas (incl. the 589 bullet, which shares
     "individual" with the input) and drops 7 generic TypedDict ideas.
   - **I5** DESIGN_RATIONALE: TypedDict ← PEP 589 (3.8), problem "dict value
     types depend on the key"; alternatives from 589.
   - **I6** LATER_RESOLVED (the sanity-check inference): *"PEP 589 left this
     out ("There is no way to individually specify whether each key is required
     or not.": No proposed syntax was clear enough…); PEP 655 later delivered it
     (a TypedDict cannot mark individual keys as required or optional) in
     Python 3.11."* Evidence: 589 *Rejected Alternatives > bullet* (cue "each
     key is required"), 655 *Abstract* and *Motivation* (cue "required and
     others … potentially-missing"). Confidence medium (seed 0.7).
   - **I1** ×3: 655 rejected "Special syntax around the key of a TypedDict
     item" (grammar change bar) and "… with an operator"; 705 rejected
     "Preventing unspecified keys".
   - **I7** EXTENDED_BY: TypedDict extended by 655 (Required/NotRequired,
     3.11, addresses the input's problem), 692 (3.12), 705 (ReadOnly, 3.13).
   - **I3**: builds on TypedDict (589, 3.8).
6. **Output**: verdict `explained`; `minimum_python_version` **3.11** (raised
   from I3's 3.8 by I6's delivering PEP); citations 484, 526, 589, 591, 655,
   692, 705 with sections; 78-step trace.
- Why no single PEP answers it: 589 cannot know 655 exists; 655 cites 589 for
  notation and totality but never says 589 postponed the idea.

### The other five examples in one line each
- 01 typed dict literals → `already_exists`: TypedDict (589, 3.8); family
  655/692/705 listed; the generic 589 rejections are filtered as irrelevant.
- 02 why `X | Y` → `explained`: 604 (3.10) to fix verbose `Union`; three
  objections from 604 with their CONS; 563 cited in an objection → superseded
  by 649.
- 03 arrow callables → `previously_rejected`: PEP 677 Rejected; four 677
  alternatives with reasons; `Callable` exists since 484.
- 04 square vs angle brackets → `explained`: two independent rejections
  joined — 484 "Which brackets…" (parser ambiguity) and 695 "Angle Brackets"
  (scanner doesn't pair `<>`, inconsistent with `list[int]`).
- 05 TypeGuard narrowing the else branch → `already_exists`: 724 Withdrawn and
  replaced by 742; 742 extends TypeGuard with `TypeIs` and addresses the
  narrowing problem.

## 5. What we deliberately didn't build

- No stemming/NLP library, no embeddings: a 5-suffix stemmer and curated
  aliases; precision over recall, every match names its alias/pattern.
- No sentence-level semantics for references: section weights + cue boost.
- No author/date/discussion-thread entities: no reasoning value for the inputs.
- No automatic construct discovery (see skim notes: example names drown real
  ones).
- No type-checker reasoning (IMPLEMENTED_BY is context only).
- No networkx: the graph is three dicts; `walk` is a 20-line BFS.
- No attempt to answer inputs outside the tables — `insufficient_evidence`
  rather than a guess.

## 6. What to build next and why

- **Versioned specification data**: the typing spec (typing.python.org) now
  supersedes several PEPs' details; a `SPECIFIED_IN` edge would keep answers
  current when a PEP's text is stale.
- **Problem discovery from Motivation headings**: many PEPs put the problem in
  a heading ("Points of Confusion"); mining those would reduce the curated
  problem list.
- **Idea-to-idea similarity**: the same alternative is rejected in several
  PEPs (angle brackets in 484 and 695); a `SAME_IDEA_AS` edge would merge them
  and strengthen I1.
- **Negative-case detection for proposals**: today "Rejected" status beats
  everything; a rejected PEP whose idea was later accepted in another form
  (e.g. 677's motivation partly met by 695's `type` aliases) should soften the
  verdict.
- **More slices** with the same code: the parsers and rule shapes are
  slice-agnostic; only the three tables are typing-specific.
- **Evaluation set**: 20–30 inputs with expected verdict/PEPs, run in CI, so
  rule changes can't silently regress outputs (the six examples are the seed).
