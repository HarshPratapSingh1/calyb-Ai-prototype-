# Iteration log (Phase 3, step 18)

Each entry: what was weak → what changed → effect. Numbers are from
`python -m src.cli build` and the six example inputs in `examples/inputs/`.

## Iteration 1: INTRODUCES picked the wrong PEP for 6 of 37 constructs

**Weak.** The first build attributed `Required`/`NotRequired` to PEP 589,
`ReadOnly` and `Annotated` to 655, `Callable` to 604 and `TypeAlias` to 613 via
the "cross-reference" rule. Inspecting the evidence showed two causes:
(1) a `::` code block with a `# PEP 589` comment was being glued onto the
preceding prose sentence, and (2) the rule accepted *any* code span in a
sentence that contained "introduced" and a PEP reference.

**Changed.** `prose_only()` strips literal blocks before sentence splitting.
The cross-reference rule now needs the construct right next to the phrase:
`` ``X`` … introduced in :pep:`N` `` or `` :pep:`N` introduced … ``X`` ``
(regexes `_INTRODUCED_A/_B`).

**Effect.** Cross-reference hits dropped from 9 to 5, all correct
(484→TypeVar, 589→TypedDict, 613→TypeAlias, 646→Unpack, 647→TypeGuard).

## Iteration 2: four more INTRODUCES misattributions, each fixed by a general rule

| Wrong edge | Cause | Rule added |
|---|---|---|
| 483 → Generic | 483 ("Theory of Type Hints") has a "Generic types" heading and the lowest number | only **Standards Track** PEPs can introduce a feature |
| 677 → Callable, 604 → Union | their titles name the construct, but they are PEPs about *notation* | a PEP whose title matches a **Syntax** entry cannot introduce a construct from its title/abstract |
| 586 → overload | 484's heading is "Function overloading"; `\boverload\b` missed it | heading match allows the suffixes `s / ing / ed` |
| 655 → Annotated | 593 never names `Annotated` in its title, abstract or any heading | priority-5 fallback: **earliest Standards Track mention as code**, weight 0.5 |

A side effect of the Syntax-title rule was that 593 ("Flexible function and
variable annotations") was treated as a syntax PEP because the
`variable-annotation` syntax entry's title phrase was "Variable Annotations".
The phrase was narrowed to "Syntax for Variable Annotations".

**Effect.** 43 INTRODUCES edges, all reviewed by hand against the PEP texts:
every construct and syntax in the curated tables now has the right introducer
(angle brackets have none, by design).

## Iteration 3: rejected-idea reasons were unreadable

**Weak.** Reasons for PEP 604's objections started with "PROS: - This syntax
can be more readable…", 695's *Angle Brackets* started with "(Refer to the
table at the end of Appendix A…)", and 655's reason was cut at "i.e.".

**Changed.** `_reason_sentence()`: a PROS/CONS objection yields its CONS; a
sentence that only announces reasons ("…rejected it for two reasons.") is
joined with the next one; leading parentheticals are dropped; the sentence
splitter protects `i.e.`, `e.g.`, `vs.`, `etc.`. Idea titles lose their "1."
numbering.

**Effect.** All reasons quoted in the six example outputs are now complete
sentences that state the objection.

## Iteration 4: proposals were marked `previously_rejected` by unrelated ideas

**Weak.** Input 01 (typed dict literals) came back `previously_rejected`
because *every* idea in PEP 589's rejected section mentions ``TypedDict``, so
all of them were reached through `construct:typeddict`. "TypedDict isn't
extensible" has nothing to do with literal syntax.

**Changed.** A relevance filter before inference (`relevant()` in
`Reasoner.infer`, logged in the trace as `relevance-filter`): an idea reached
through a Syntax, Problem or direct keyword match is relevant; one reached only
through a construct name must share at least one input keyword beyond that
name. Rules I1 and I6 only see relevant ideas.

**Effect.** Input 01: `already_exists` (TypedDict, PEP 589, 3.8) with the
TypedDict family listed; input 04: the unrelated "forward declarations" and
"ParametersOf" ideas disappear.

## Iteration 5: I6 LATER_RESOLVED fired on contradictions

**Weak.** "PEP 589 left out runtime type checking; PEP 705 later delivered it"
and "PEP 484 rejected angle brackets; PEP 695 later delivered inline type
parameter lists". Both chains were real graph paths but wrong conclusions: a
*rejected* idea is not something that was "left out", and 705 only shares
589's own main problem.

**Changed.** I6 fires for a *postponed* idea with any target, or for a
*rejected* idea only through a Problem (the idea was one rejected solution to a
problem a later PEP solved differently). The later PEP must address that
problem in its Abstract/Motivation (weight 1.0), and the idea's own PEP must
not address the same problem.

**Effect.** I6 now fires exactly for the designed sanity-check (589 → 655,
input 06) and for 724 → 742 (input 05); the two false positives are gone.

## Iteration 6: the `square-brackets` syntax matched every `List[T]`

**Weak.** The syntax entity's code pattern matched any subscripted generic, so
a third of all rejected ideas "concerned" square brackets.

**Changed.** `SyntaxSpec.prose`: bracket forms are only recognised when a text
discusses them by name ("square brackets", "angle brackets"); the code pattern
for square brackets was disabled.

**Effect.** Input 04 now lists exactly the two bracket discussions (484 *Which
brackets for generic type parameters?* and 695 *Angle Brackets*).

## Iteration 7: the verdict ignored successors, and I4 missed PEP 563

**Weak.** Input 05 was `previously_rejected` because PEP 724 is Withdrawn,
even though PEP 742 replaced it and delivers the feature. Input 02 never
mentioned that PEP 563, cited inside 604's objection, is superseded, because
563 was ranked 7th and I4 only looked at the top 6.

**Changed.** (a) Traversal follows `CONCERNS → PEP` from reached ideas, so a
PEP cited inside an idea gets a path. (b) I4 looks at every PEP above the score
floor (0.2), preferring in-slice successors. (c) New rule I7 EXTENDED_BY lists
Final PEPs that extend a matched construct and flags the ones that address a
matched problem. (d) Verdict precedence: a dead Rejected/Withdrawn PEP wins;
then I2/I7 coverage; then a live rejected idea; then I6.

**Effect.** Input 05: `already_exists` ("TypeGuard was later extended by PEP
742 adding TypeIs"); input 02 reports 563 → 649.

## Iteration 8: minimum version came from the wrong PEP

**Weak.** Input 06 ("…can I do it now and since which Python version?")
reported 3.8 (TypedDict) although the answer is 3.11 (`Required`).

**Changed.** `minimum_python_version` is the maximum of the I3 dependencies and
the version of a PEP that I6 identifies as delivering a postponed idea. A first
attempt also used I7's extensions, which pushed input 01 to 3.13 because PEP
705 mentions TypedDict's own "fixed set of keys" problem; extensions now only
get credit for problems the introducer does not address itself, and their
version stays in the I7 statement text.

**Effect.** Input 06: 3.11; input 01 stays at 3.8; input 05 reports 3.10 for
TypeGuard and names TypeIs (3.13) in the I7 statement.

## Known limitations left as is

- `I3_DEPENDS_ON` for a *proposal* lists what the top-ranked PEP builds on
  (e.g. 677 builds on 585/604/612). That is context, not a strict dependency of
  the user's proposal; the statement says "Builds on" for that reason.
- Keyword matching of rejected-idea titles needs two shared keywords, so very
  short titles ("Do nothing", "Alternatives") can only be reached through
  the entities they concern.
- The 26 problem entries and ~45 construct/syntax entries are hand-curated;
  an input about a typing topic outside them (e.g. `Never`, `TypeVar`
  variance keywords) degrades to `insufficient_evidence` rather than guessing.

## Iteration 9 (after the fresh-clone run): I5 quoted a passing mention

**Weak.** Input 04's rationale listed PEP 646's "Readability. class
Array(Generic[DType, Unpack[Shape]]) …" as an alternative that was turned
down, only because that idea mentions ``Generic`` and `Generic` was a matched
construct.

**Changed.** I5 quotes an idea only if it concerns the entity being explained
or a matched syntax form / problem; a matched construct mentioned in passing
no longer qualifies.

**Effect.** Input 04 quotes exactly the two bracket discussions (484, 695).
Inputs 02 and 06 unchanged.
