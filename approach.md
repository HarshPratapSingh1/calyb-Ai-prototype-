# Approach

## Picking the slice

I went with Python's static typing system: the `typing` module, annotations,
generics, protocols and the type checkers that consume all of it. Partly
that's because I use these features every day and already had a feel for
which PEPs talk to each other. But the bigger reason came out of the first
hour of reading: typing PEPs are unusually chatty about their own history.
PEP 655 opens by explaining what PEP 589 couldn't do. PEP 742 spends a whole
section on why PEP 724 was withdrawn. PEP 695 has an appendix comparing
generic syntax across eight languages. That kind of cross-talk is exactly what
a graph is good at, and it's the thing you lose if you only read one document.

The index has 30 PEPs. 28 are the obvious typing PEPs, from 483 (the theory
behind type hints) up to 742 (TypeIs). I added two that were never accepted:
677, the arrow syntax for callables, which the Steering Council rejected, and
724, the stricter TypeGuard proposal that was withdrawn once 742 came along.
I wanted them in there because one of the rules I had in mind from the start
was "this idea was already tried and rejected", and that rule is useless if
every PEP in the corpus was accepted. As it turned out, 677 is the whole
answer to one of my test inputs.

The raw files are the `.rst` sources from the python/peps repo, which is what
peps.python.org is built from. I committed them so `build` works offline and
gives the same result on every machine. `python -m src.ingest --verify`
checks every title, status, type and version in the index against the real
header; it comes back with zero mismatches.

## What I learned from skimming

Before designing anything I read 484, 544, 585, 604 and 695 properly and ran
some rough greps over all thirty. Two findings shaped everything after.

First, the header fields are nearly empty as a source of structure. Across 30
PEPs there are exactly three supersede/replace links and not a single
`Requires:` line. I'd assumed headers would give me a skeleton to hang the
rest on. They don't. The body text has to do the work.

Second, the most interesting knowledge usually has no PEP number attached.
When 695 explains why it didn't use angle brackets, or 589 says "there is no
way to individually specify whether each key is required or not... no
proposed syntax was clear enough", those are the things I'd want a reasoning
engine to surface, and they live in subsections and bullet points of
rejected-ideas sections. There are about twenty different spellings of that
section heading in the corpus ("Rejected Ideas", "Rejected Alternatives",
"Objections and responses", "Rejected/Postponed Proposals"...), but one regex
catches them all.

A smaller thing that cost me time later: references come in four textual
forms, not one. The Sphinx role `:pep:\`484\`` is by far the most common,
but it also appears with a label and anchor, it wraps across line breaks, and
PEP 585's abstract says "PEPs 484, 526, 544, 560, and 563" in a way the
obvious regex misses entirely.

## The model

Seven entity types. PEP, from the header. Construct, meaning something you
import from `typing` (TypedDict, ParamSpec, Final, 33 of them). Syntax,
meaning notation rather than a name: `X | Y`, `list[int]`, `class C[T]`,
`(int) -> str`, and angle brackets, which were never adopted but keep coming
up. Problem, a recurring motivation like "Union[X, Y] is verbose" or "a
TypedDict can't mark some keys optional". RejectedIdea, one per subsection or
bullet of a rejected-ideas section, with the disposition (rejected, postponed,
or an objection that was answered) and the sentence giving the reason.
PythonVersion from the header. And TypeChecker, which is only there for
context because the slice description mentions checkers; no rule reasons
about it.

Constructs, syntax forms and problems are hand-written tables in
`extract.py`. I did try the lazy route first, counting how often names appear
in double backticks across the corpus. It doesn't work. `Movie`, `MyType` and
`Shape` rank alongside `TypeVar` because PEPs are full of example code. So
each table entry says how the thing shows up in a PEP (a regex on code spans,
a title phrase, cue phrases for problems) and how a person would refer to it
in a question. Every entry has a comment saying which PEP it came from.

Nine relationship types. SUPERSEDES is the only one that comes purely from
headers. BUILDS_ON is any reference to another PEP outside a rejected
section, weighted by where it sits: a mention in the Abstract or Motivation
is worth 1.0, Rationale 0.8, the Specification 0.5, backwards-compatibility
and implementation sections 0.4, and acknowledgements are ignored. If the
surrounding sentence says "introduced in" or "builds on" or "as defined in",
the weight goes up to at least 0.8. INTRODUCES picks one introducing PEP per
construct or syntax. EXTENDS is a later PEP that changes something another PEP
introduced. ADDRESSES_PROBLEM links a PEP to the problems its motivation
sections match. REJECTED_ALTERNATIVE and CONCERNS hang rejected ideas off
their PEP and connect them to whatever constructs, syntax, problems and PEPs
the idea text mentions. AVAILABLE_IN and IMPLEMENTED_BY are what they sound
like.

Every edge carries evidence: the PEP number, the full heading path of the
section, the name of the rule that fired, and the snippet of text it fired
on. The rule names in `knowledge_state.json` are the same names used in the
design notes and the code, so you can go from any edge back to the line that
produced it. The build comes out at 280 entities and 603 relationships.

## Getting INTRODUCES right was most of the work

Deciding which PEP introduced each construct sounds trivial and wasn't. My
first rule was: if a sentence contains "introduced", a PEP reference and a
code span, the PEP introduced the code span. That gave me 589 introducing
`Required`, 655 introducing `ReadOnly`, and 604 introducing `Callable`. All
wrong. Two separate causes. A `::` code block with a `# PEP 589` comment was
being glued onto the prose sentence before it, and the rule accepted any code
span anywhere in the sentence. The fix was to strip literal blocks before any
sentence-level rule runs, and to require the construct to sit right next to
the "introduced in" phrase, in either order.

After that, four more misattributions, and I made myself fix each one with a
general rule rather than a special case. PEP 483 was "introducing" Generic
because it has a heading called "Generic types" and the lowest number; now
only Standards Track PEPs can introduce anything. 677 and 604 were
introducing Callable and Union from their titles; now a PEP whose title names
a syntax form is treated as being about notation, not about the construct.
"Function overloading" in 484 wasn't matching `overload`; headings now allow
the -s/-ing/-ed suffixes. And nothing at all named `Annotated` in a title,
abstract or heading, so there's a last-resort rule: the earliest Standards
Track PEP that uses the thing as code, at a low weight. The final priority
ladder is cross-reference, then title, then "this PEP introduces" in the
abstract, then a section heading, then earliest mention, with ties going to
the lowest PEP number. I checked all 43 resulting edges by hand.

## Answering a new input

`ask` runs five steps and writes every one into a `trace` list, so each
number in the output can be reproduced by hand.

Classify: a question if it starts with a question word or ends with `?`,
otherwise a proposal. This matters because a rejected PEP is strong evidence
against a proposal but weak evidence for a "why" question, so the status
weights differ.

Match: syntax regexes on the raw text, construct names and aliases, problem
cue phrases, and keyword overlap against rejected-idea titles and PEP titles.
Each seed records its strength and exactly which alias or pattern matched.

Traverse: at most two hops. From a construct or problem back to the PEPs that
introduce, extend or address it, through CONCERNS to the rejected ideas that
mention it, from those ideas to the PEPs they cite, and from each PEP to what
it builds on or supersedes. I keep every path, not just the best, because the
sum of paths is what distinguishes a PEP that is central to the question from
one that got mentioned once.

Score: sum of path strengths, where a path is match strength times the edge
weights times 0.6 per hop, multiplied by the status weight and by a small
centrality factor based on how many PEPs build on this one. The breakdown
goes into the output.

Infer: seven rules. Prior rejection, already provided, depends-on with a
minimum Python version, superseded source, design rationale (with the
alternatives that were turned down), "postponed in PEP A and delivered by PEP
B", and later extensions of a feature. The piece I'm happiest with is a
relevance filter that runs before these rules. Early on, every single
rejected idea in PEP 589 showed up for any TypedDict question, because every
one of them says ``TypedDict``. Now an idea reached only through a construct
name has to share at least one further keyword with the input; ideas reached
through a syntax form, a problem or a direct title match pass automatically.

The output is a fixed JSON shape: verdict, a summary built from the top
conclusions, minimum Python version, matched entities, ranked PEPs with their
score breakdown, conclusions with evidence, citations with URLs and sections,
and the trace.

## The question no single PEP answers

My sanity check from the design stage was: "Was it ever rejected to let a
TypedDict mark individual keys as required or optional, and since which
version can I do it?" PEP 589 postponed exactly this, in a bullet point, for
lack of a clear syntax. PEP 655 added `Required` and `NotRequired` in 3.11.
But 655 only cites 589 for notation and totality; it never says 589 had
considered and dropped the idea. The engine finds it by going from the
problem node to the 589 bullet (via CONCERNS) and from the same problem node
to 655 (via ADDRESSES_PROBLEM), notices 655 is later and Final, and fires
LATER_RESOLVED. The minimum version comes out as 3.11, not TypedDict's 3.8.
That's example 06 in the repo, trace included.

The others, briefly: typed dict literals come back as already covered by
TypedDict with the 655/692/705 family listed; the `X | Y` question is
explained by 604 with its three objections and the note that 563, which one
objection relies on, is superseded by 649; arrow callables hit PEP 677's
rejection head-on; the angle-brackets question joins two independent
rejections, the parser-ambiguity argument in 484 and the "scanner doesn't
pair `<>`" argument in 695; and a TypeGuard-that-narrows-the-else-branch
proposal resolves to TypeIs via 724 being withdrawn and 742 extending
TypeGuard.

## Things I decided not to build

No NLP library, no stemming beyond stripping five suffixes, no embeddings.
Precision over recall: an input about a typing topic that isn't in the tables
gets `insufficient_evidence` instead of a guess, and I'm fine with that.

No real understanding of what a reference means. Section position plus a cue
boost turned out to be a good enough proxy, and it's explainable.

No authors, dates or discussion threads. They'd be easy to extract and they
answer none of the questions this is for.

No networkx. The graph is three dictionaries and a twenty-line breadth-first
walk, and serialising it is trivial.

## What I'd do next

The typing spec at typing.python.org has quietly superseded details in a few
of these PEPs; a SPECIFIED_IN edge would keep answers current. The same
alternative gets rejected in more than one PEP (angle brackets in 484 and
again in 695), and a SAME_IDEA_AS edge would merge those and make the
prior-rejection rule stronger. The problem table could probably be half-mined
from Motivation headings instead of hand-written. And the six examples should
turn into a proper evaluation set with expected verdicts and PEPs, run as
tests, so that tuning a rule for one input can't quietly break another. I hit
that more than once while iterating. The parsers and rule shapes aren't
typing-specific, so a second slice would mostly mean writing three new
tables.
