"""Turn raw PEP text into typed entities and relationships with evidence.

Everything here is hand-written: regular expressions, string handling and
explicit lookup tables. There are three layers, top to bottom:

1. PARSERS (generic, no typing knowledge)
   - parse_header():     the ``Field: value`` block at the top of a PEP
   - split_sections():   RST headings (title line + underline) with nesting
   - find_references():  ``:pep:`484```, ``PEP 484``, ```PEP 484 <url>`_``,
                         ``PEPs 484, 526 and 544`` with section + snippet

2. CURATED TABLES (the slice-specific knowledge)
   - CONSTRUCTS, SYNTAX, PROBLEMS, TYPE_CHECKERS: what to look for and how
     each thing shows up in PEP text and in a user's question.

3. MAPPING RULES (parsers + tables -> graph)
   - extract_pep(): runs every rule R-* from notes/design.md on one PEP and
     returns Entities and Relationships, each with Evidence.

The rule names in Evidence.rule (e.g. "R-BLD section-weight") match the
headings in notes/design.md so a reviewer can go from an edge in
knowledge_state.json back to the rule that produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .schema import Entity, Evidence, Relationship, make_id


# ===========================================================================
# 1. PARSERS
# ===========================================================================

# ---- 1a. Header block ------------------------------------------------------

_HEADER_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):(?:\s+(.*))?$")


def parse_header(text: str) -> dict[str, str]:
    """Parse the RFC-822 style header at the top of a PEP.

    Reads ``Field: value`` lines up to the first blank line. A line that starts
    with whitespace continues the previous field (used by Author and
    Post-History). Field names keep their original spelling ("Python-Version").
    """
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if not line.strip():
            break
        m = _HEADER_FIELD.match(line)
        if m and not line[0].isspace():
            current = m.group(1)
            fields[current] = (m.group(2) or "").strip()
        elif current is not None and line[0].isspace():
            fields[current] = (fields[current] + " " + line.strip()).strip()
        # Anything else (should not happen in a well-formed PEP) is ignored.
    return fields


# ---- 1b. RST sections ------------------------------------------------------

@dataclass
class Section:
    """One RST section: its heading, nesting level and body text."""

    title: str
    level: int                  # 1 = top level (first underline char seen)
    path: list[str]             # headings from the top level down to this one
    body: str                   # text under the heading, excluding sub-sections
    start_line: int

    @property
    def path_str(self) -> str:
        return " > ".join(self.path)


_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+_:.])\1{2,}\s*$")


def split_sections(text: str) -> list[Section]:
    """Split a PEP into sections.

    A heading is a non-blank line immediately followed by an underline made of
    one repeated punctuation character, at least as long as the heading. As in
    RST, the nesting level of an underline character is decided by the order in
    which it first appears in the document (first char = level 1, ...).

    Text before the first heading (the header block) is returned as a level-0
    section called "Header". The body of a section stops where its first
    sub-section begins, so each line of the document belongs to exactly one
    Section.
    """
    lines = text.splitlines()
    headings: list[tuple[int, str, int]] = []  # (line index, title, level)
    char_level: dict[str, int] = {}
    i = 0
    while i < len(lines) - 1:
        title, under = lines[i], lines[i + 1]
        if (title.strip() and not title[0].isspace() and _UNDERLINE.match(under)
                and len(under.rstrip()) >= len(title.rstrip())
                and not _UNDERLINE.match(title)):
            ch = under[0]
            if ch not in char_level:
                char_level[ch] = len(char_level) + 1
            headings.append((i, title.strip(), char_level[ch]))
            i += 2
            continue
        i += 1

    sections: list[Section] = []
    first = headings[0][0] if headings else len(lines)
    sections.append(Section("Header", 0, ["Header"], "\n".join(lines[:first]), 0))

    stack: list[tuple[int, str]] = []  # (level, title)
    for n, (idx, title, level) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        body = "\n".join(lines[idx + 2:end])
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        sections.append(Section(title, level, [t for _, t in stack], body, idx))
    return sections


def section_tree(sections: list[Section]) -> dict[int, list[int]]:
    """Map section index -> indexes of its direct sub-sections."""
    children: dict[int, list[int]] = {i: [] for i in range(len(sections))}
    stack: list[int] = []
    for i, sec in enumerate(sections):
        while stack and sections[stack[-1]].level >= sec.level:
            stack.pop()
        if stack:
            children[stack[-1]].append(i)
        stack.append(i)
    return children


# ---- 1c. PEP references ----------------------------------------------------

@dataclass
class Reference:
    number: int
    section: str        # heading path of the section the mention is in
    snippet: str        # ~150 chars of context, whitespace-normalised
    form: str           # which textual form matched
    start: int          # offset in the section body (for de-duplication)


# The four textual forms, tried in this order. Each has one capture group
# for the number (the plural-list form captures the whole list instead).
_REF_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # :pep:`484`  and  :pep:`some label <484#anchor>`
    ("rst-role", re.compile(r":pep:`(?:[^`<]*<)?\s*(\d+)(?:#[^`>]*)?\s*>?`", re.S)),
    # `PEP 484 <https://...>`_
    ("rst-link", re.compile(r"`PEP[\s-]?(\d+)\s*<[^>]*>`_")),
    # PEPs 484, 526, 544, 560, and 563
    ("plural-list", re.compile(r"\bPEPs\s+((?:\d+\s*,?\s*(?:and\s+)?)+\d*)")),
    # plain  PEP 484  /  PEP-484
    ("plain", re.compile(r"\bPEP[\s-]?(\d+)\b")),
]
_RST_LABEL_LINE = re.compile(r"^\.\.\s+_[^:]*:\s*$", re.M)


def _snippet(text: str, start: int, end: int, width: int = 150) -> str:
    """Return about `width` chars around [start, end), whitespace-normalised."""
    pad = max(0, (width - (end - start)) // 2)
    lo = max(0, start - pad)
    hi = min(len(text), end + pad + (pad - (start - lo)))   # give unused left room to the right
    s = re.sub(r"\s+", " ", text[lo:hi]).strip()
    return ("…" if lo > 0 else "") + s + ("…" if hi < len(text) else "")


def find_references(text: str, section: str = "", self_number: int | None = None
                    ) -> list[Reference]:
    """Find every mention of another PEP in `text`.

    Runs on whole section text (not line by line) so an RST role that wraps
    across a line break is still found. Skips RST label lines such as
    ``.. _PEP 544 rationale:`` and self-references. A number mentioned both in
    a role's label and its target (``:pep:`spec of PEP 484 <484#x>```) is
    reported once, because later patterns ignore spans already claimed.
    """
    text = _RST_LABEL_LINE.sub("", text)
    claimed: list[tuple[int, int]] = []
    refs: list[Reference] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < e and b > s for s, e in claimed)

    for form, pat in _REF_PATTERNS:
        for m in pat.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            claimed.append((m.start(), m.end()))
            numbers = [int(n) for n in re.findall(r"\d+", m.group(1))]
            for n in numbers:
                if self_number is not None and n == self_number:
                    continue
                refs.append(Reference(n, section, _snippet(text, m.start(), m.end()),
                                      form, m.start()))
    refs.sort(key=lambda r: r.start)
    return refs


# ---- 1d. Small text helpers used by the rules ------------------------------

_SENTENCE_SPLIT = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][)\"']))\s+(?=[A-Z`:*(\"])")
_CODE_SPAN = re.compile(
    r"``([^`\n]+)``"                                             # ``Name``
    r"|:(?:py:)?(?:class|data|func|meth|mod|obj|attr|term):`~?([^`<]+?)(?:\s*<[^>]*>)?`"  # :class:`~typing.X`
)


def prose_only(text: str) -> str:
    """Drop literal code blocks (indented lines after a line ending in '::' or
    a code-block directive) so code comments never leak into prose rules."""
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.rstrip()
        if in_block:
            if stripped and not line[0].isspace():
                in_block = False
            else:
                continue
        if stripped.endswith("::") or re.match(r"\s*\.\.\s+code(-block)?::", stripped):
            in_block = True
            out.append(stripped[:-1] if stripped.endswith("::") else "")  # "Example::" -> "Example:"
            continue
        out.append(line)
    return "\n".join(out)


def sentences(text: str) -> list[str]:
    """Split prose into sentences (good enough for cue matching). Literal
    code blocks are removed first."""
    flat = re.sub(r"\s+", " ", prose_only(text)).strip()
    flat = re.sub(r"\b(i\.e|e\.g|vs|etc|cf)\.", lambda m: m.group(1) + "\u00b7", flat)  # protect abbreviations
    return [s.replace("\u00b7", ".") for s in _SENTENCE_SPLIT.split(flat) if s]


def code_spans(text: str) -> list[str]:
    """Contents of every inline code span / Sphinx object role in the text."""
    return [m.group(1) or m.group(2) for m in _CODE_SPAN.finditer(text)]


def strip_rst(text: str) -> str:
    """Remove the RST markup that gets in the way of plain-text matching."""
    text = re.sub(r":pep:`(?:[^`<]*<)?\s*(\d+)[^`]*`", r"PEP \1", text)
    text = _CODE_SPAN.sub(lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"``|\*\*?|\\", "", text)
    return text


def slugify(title: str, max_words: int = 7) -> str:
    """'3. Add a new operator for ``Union``?' -> 'add-a-new-operator-for-union'."""
    t = re.sub(r"^\s*\d+[.)]\s*", "", strip_rst(title))
    words = re.findall(r"[a-z0-9]+", t.lower())
    return "-".join(words[:max_words]) or "idea"


# ===========================================================================
# 2. CURATED TABLES
# ===========================================================================
# These tables are the hand-written knowledge of the slice. Each entry says
# how the thing appears (a) in PEP text and (b) in a user's input.
#
#   code:    regex matched INSIDE inline code spans of PEP text (``...``)
#   title:   plain phrases matched case-insensitively in a PEP Title
#   words:   lowercase phrases matched in user input (strength 1.0)
#   weak:    lowercase single words that are ambiguous in English and are
#            matched in user input at strength 0.6 only
#
# The comment after each entry says which PEP the entry was derived from.

@dataclass(frozen=True)
class ConstructSpec:
    key: str
    name: str
    kind: str
    code: str
    title: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()


CONSTRUCTS: list[ConstructSpec] = [
    # -- TypedDict family (589, 655, 692, 705)
    ConstructSpec("typeddict", "TypedDict", "class", r"TypedDict\b",
                  title=("TypedDict",), words=("typeddict", "typed dict", "typed dictionary")),
    ConstructSpec("required", "Required", "qualifier", r"\bRequired\b",
                  words=("required[", "required qualifier", "required key")),
    ConstructSpec("notrequired", "NotRequired", "qualifier", r"NotRequired\b",
                  words=("notrequired", "not required", "potentially missing", "potentially-missing")),
    ConstructSpec("readonly", "ReadOnly", "qualifier", r"ReadOnly\b",
                  title=("Read-only items",), words=("readonly", "read-only", "read only")),
    ConstructSpec("unpack", "Unpack", "special_form", r"Unpack\b",
                  words=("unpack[", "unpack")),
    # -- narrowing family (647, 724, 742)
    ConstructSpec("typeguard", "TypeGuard", "special_form", r"TypeGuard\b",
                  title=("Type Guards",), words=("typeguard", "type guard", "type guards")),
    ConstructSpec("typeis", "TypeIs", "special_form", r"TypeIs\b",
                  title=("TypeIs",), words=("typeis",)),
    # -- generics family (484, 612, 646, 695, 696)
    ConstructSpec("typevar", "TypeVar", "class", r"TypeVar\b(?!Tuple)",
                  words=("typevar", "type variable", "type variables", "type var")),
    ConstructSpec("generic", "Generic", "class", r"\bGeneric\b",
                  words=("generic[", "generic class", "generic classes", "generic type", "generics"),
                  weak=("generic",)),
    ConstructSpec("paramspec", "ParamSpec", "class", r"ParamSpec\b",
                  title=("Parameter Specification",),
                  words=("paramspec", "parameter specification", "param spec")),
    ConstructSpec("concatenate", "Concatenate", "special_form", r"Concatenate\b",
                  words=("concatenate",)),
    ConstructSpec("typevartuple", "TypeVarTuple", "class", r"TypeVarTuple\b",
                  title=("Variadic Generics",),
                  words=("typevartuple", "variadic generic", "variadic generics", "variadic")),
    # -- special forms from 484 / 586 / 675 / 593 / 673
    ConstructSpec("union", "Union", "special_form", r"\bUnion\b",
                  words=("union[", "union type", "union types", "typing.union"), weak=("union",)),
    ConstructSpec("optional", "Optional", "special_form", r"\bOptional\b",
                  words=("optional[", "typing.optional")),
    ConstructSpec("callable", "Callable", "special_form", r"\bCallable\b",
                  words=("callable[", "callable type", "callable types", "typing.callable"),
                  weak=("callable",)),
    ConstructSpec("any", "Any", "special_form", r"\bAny\b",
                  words=("typing.any", "any type")),
    ConstructSpec("noreturn", "NoReturn", "special_form", r"NoReturn\b",
                  words=("noreturn", "no return")),
    ConstructSpec("newtype", "NewType", "function", r"NewType\b",
                  words=("newtype", "new type")),
    ConstructSpec("overload", "overload", "decorator", r"(^|[@.\s])overload\b",
                  words=("@overload", "overload")),
    ConstructSpec("typing-collection-aliases", "typing.List / Dict / Tuple / Set",
                  "alias", r"(typing\.)?(List|Dict|Tuple|Set|FrozenSet)\[",
                  words=("typing.list", "typing.dict", "typing.tuple", "list[", "capital list")),
    ConstructSpec("literal", "Literal", "special_form", r"\bLiteral\b(?!String)",
                  title=("Literal Types",), words=("literal[", "literal type", "literal types"),
                  weak=("literal",)),
    ConstructSpec("literalstring", "LiteralString", "special_form", r"LiteralString\b",
                  title=("Literal String",), words=("literalstring", "literal string")),
    ConstructSpec("annotated", "Annotated", "special_form", r"\bAnnotated\b",
                  title=("Flexible function and variable annotations",),
                  words=("annotated[", "typing.annotated", "annotated metadata")),
    ConstructSpec("self", "Self", "special_form", r"\bSelf\b",
                  title=("Self Type",), words=("self type", "typing.self", "return self")),
    # -- qualifiers / decorators (526, 591, 698, 702, 681)
    ConstructSpec("classvar", "ClassVar", "qualifier", r"ClassVar\b",
                  words=("classvar", "class variable", "class variables")),
    ConstructSpec("final", "Final", "qualifier", r"(\bFinal\b|(^|[@\s])final\b)",
                  title=("final qualifier",), words=("@final", "final qualifier", "typing.final"),
                  weak=("final",)),
    ConstructSpec("override", "override", "decorator", r"(^|[@.\s])override\b",
                  title=("Override Decorator",), words=("@override", "override decorator"),
                  weak=("override",)),
    ConstructSpec("deprecated", "deprecated", "decorator", r"(^|[@.\s])deprecated\b",
                  title=("deprecations",), words=("@deprecated", "warnings.deprecated"),
                  weak=("deprecated", "deprecation", "deprecations")),
    ConstructSpec("dataclass-transform", "dataclass_transform", "decorator", r"dataclass_transform\b",
                  title=("Data Class Transforms",),
                  words=("dataclass_transform", "dataclass transform", "data class transform")),
    # -- protocols (544)
    ConstructSpec("protocol", "Protocol", "class", r"\bProtocol\b",
                  title=("Protocols",), words=("protocol[", "typing.protocol", "structural subtyping",
                                               "structural typing", "static duck typing"),
                  weak=("protocol", "protocols")),
    ConstructSpec("runtime-checkable", "runtime_checkable", "decorator", r"runtime_checkable\b",
                  words=("runtime_checkable", "runtime checkable")),
    # -- aliases (613)
    ConstructSpec("typealias", "TypeAlias", "special_form", r"TypeAlias\b",
                  title=("Type Aliases",), words=("typealias", "type alias", "type aliases")),
    # -- packaging (561)
    ConstructSpec("py-typed", "py.typed marker / stub packages", "convention", r"py\.typed|-stubs\b",
                  title=("Distributing and Packaging Type Information",),
                  words=("py.typed", "stub package", "stub packages", "stub-only", "type stubs",
                         "typeshed")),
]


@dataclass(frozen=True)
class SyntaxSpec:
    key: str
    notation: str
    label: str
    code: str                       # regex on code spans / code blocks in PEP text
    title: tuple[str, ...] = ()     # phrases matched in a Title or Abstract sentence
    words: tuple[str, ...] = ()     # phrases in user input
    input_pattern: str = ""         # regex on raw user input
    never_adopted: bool = False     # True = no PEP may be recorded as introducing it
    prose: str = ""                 # regex on plain prose; for forms discussed by name ("angle brackets")


SYNTAX: list[SyntaxSpec] = [
    SyntaxSpec("union-pipe", "X | Y", "X | Y union syntax",
               code=r"\b[A-Za-z_]\w*\s*\|\s*[A-Za-z_]\w*", title=("X | Y",),
               words=("pipe operator", "pipe syntax", "vertical bar", "bar operator", "| operator",
                      "or operator"),
               input_pattern=r"[A-Za-z_\]]\s*\|\s*[A-Za-z_]"),                     # 604
    SyntaxSpec("builtin-generics", "list[int]", "subscripted builtin collections",
               code=r"\b(list|dict|tuple|set|frozenset|type)\[", title=("Generics In Standard Collections",),
               words=("builtin generics", "built-in generics", "standard collections", "lowercase list",
                      "subscript builtins", "list[int]", "dict[str"),
               input_pattern=r"\b(list|dict|tuple|set|frozenset)\[\w"),          # 585
    SyntaxSpec("type-param-list", "class C[T]: / def f[T]()", "inline type parameter lists",
               code=r"\b(class|def)\s+\w+\[", title=("Type Parameter Syntax",),
               words=("type parameter syntax", "type parameter list", "type parameters",
                      "class c[t]", "def f[t]", "generic syntax", "inline type parameters"),
               input_pattern=r"\b(class|def)\s+\w+\["),                            # 695
    SyntaxSpec("type-statement", "type X = ...", "the type alias statement",
               code=r"(^|\n)\s*type\s+\w+(\[[^\]]*\])?\s*=",
               title=("type statement", "statement for declaring type aliases"),
               words=("type statement", "type keyword", "type alias statement"),
               input_pattern=r"\btype\s+\w+\s*="),                                 # 695
    SyntaxSpec("arrow-callable", "(int, str) -> bool", "arrow syntax for callable types",
               code=r"(?<!def )(?<!\w)\([^()]*\)\s*->\s*[A-Za-z]", title=("Callable Type Syntax",),
               words=("arrow syntax", "arrow callable", "arrow function type", "-> syntax"),
               input_pattern=r"\)\s*->\s*\w"),                                     # 677 (rejected)
    SyntaxSpec("variable-annotation", "x: int", "variable annotations",
               code=r"(^|\n)\s*[a-z_]\w*\s*:\s*[A-Z]\w*(\[[^\]]*\])?\s*(=|$)",
               title=("Syntax for Variable Annotations",),
               words=("variable annotation", "variable annotations", "annotate variables",
                      "annotated assignment", "annotate a variable")),           # 526
    SyntaxSpec("future-annotations", "from __future__ import annotations", "postponed (string) annotations",
               code=r"from __future__ import annotations", title=("Postponed Evaluation of Annotations",),
               words=("from __future__ import annotations", "future import", "postponed evaluation",
                      "lazy annotations", "string annotations", "stringified annotations",
                      "deferred evaluation")),                                     # 563 / 649
    SyntaxSpec("star-unpack", "*Ts", "star-unpacking a TypeVarTuple",
               code=r"\*Ts\b|\*tuple\[", title=(),
               words=("*ts", "star unpack", "star-unpack", "unpack a tuple type"),
               input_pattern=r"\*[A-Z]\w*\b(?!\w*\()"),                            # 646
    SyntaxSpec("kwargs-unpack", "**kwargs: Unpack[TD]", "typing **kwargs with a TypedDict",
               code=r"\*\*kwargs:\s*Unpack\[", title=("**kwargs",),
               words=("**kwargs", "kwargs typing", "keyword arguments typing", "typed kwargs"),
               input_pattern=r"\*\*kwargs"),                                       # 692
    SyntaxSpec("angle-brackets", "C<T>", "angle-bracket generics (never adopted)",
               code=r"\b[A-Z]\w*<[A-Z]\w*>", title=(), prose=r"\b(angle|angular) brackets?\b",
               words=("angle bracket", "angle brackets", "angular brackets", "chevrons", "<t>"),
               input_pattern=r"\b[A-Z]\w*<[A-Z]\w*>", never_adopted=True),          # 484, 695 rejected ideas
    SyntaxSpec("square-brackets", "C[T]", "square-bracket generics",
               code=r"(?!)", title=(), prose=r"\bsquare brackets?\b",   # only when discussed by name
               words=("square bracket", "square brackets", "subscript syntax", "[t]")),  # 484 §Which brackets
]


@dataclass(frozen=True)
class ProblemSpec:
    key: str
    label: str
    cues: tuple[str, ...]           # regexes on lowercased PEP prose (Abstract/Motivation/Rationale)
    words: tuple[str, ...] = ()     # phrases in user input


PROBLEMS: list[ProblemSpec] = [
    ProblemSpec("verbose-union-syntax", "Union[X, Y] is verbose and hurts adoption",
                (r"verbosity of this syntax", r"must use ``union\[x, y\]``", r"union\[x, y\]``\.? *the verbosity"),
                ("union is verbose", "verbose union", "shorter union", "write unions")),            # 604
    ProblemSpec("duplicate-collection-hierarchy", "typing.List duplicates the builtin list hierarchy",
                (r"duplicated collection hierarchy", r"``typing\.list`` and the built-in ``list``",
                 r"parallel type hierarchy"),
                ("typing.list", "duplicate hierarchy", "two ways to write list", "import list from typing")),  # 585
    ProblemSpec("forward-references", "annotations must refer to names not yet defined",
                (r"forward references?", r"not yet (been )?defined", r"string literal.{0,40}annotation"),
                ("forward reference", "forward references", "not yet defined", "string annotation")),  # 484, 563, 649
    ProblemSpec("annotation-runtime-cost", "evaluating annotations at definition time is costly",
                (r"(cost|overhead|expensive|performance|slow\w*).{0,80}annotations",
                 r"annotations.{0,80}(cost|overhead|expensive|evaluated at definition time)",
                 r"eagerly evaluated"),
                ("import time", "startup cost", "evaluation cost", "lazy evaluation", "eagerly evaluated")),  # 563, 649
    ProblemSpec("no-structural-typing", "PEP 484 only has nominal subtyping; duck typing is unsupported",
                (r"nominal subtyping", r"structural subtyping", r"duck typing"),
                ("duck typing", "structural subtyping", "nominal subtyping", "implicit interface")),  # 544
    ProblemSpec("typevar-scoping-confusion", "TypeVar declarations are verbose and their scoping is confusing",
                (r"points of confusion", r"scoping rules for (the )?type parameters",
                 r"typevar.{0,80}(confus|verbose|cumbersome|awkward)",
                 r"variance.{0,60}(confus|difficult|manual|explicit)"),
                ("typevar is confusing", "typevar scoping", "declare a typevar", "variance",
                 "covariant", "contravariant")),                                                     # 695
    ProblemSpec("verbose-callable-types", "Callable[[...], R] is hard to read",
                (r"callable\[\[.{0,120}(hard to read|verbose|unwieldy|cumbersome|concise|friendly|difficult)",
                 r"(concise|friendly) syntax for callable", r"callable types? (is|are) (hard|difficult|verbose)",
                 r"nesting of square brackets"),
                ("callable is verbose", "callable readability", "function type syntax", "hard to read callable")),  # 677
    ProblemSpec("typeddict-partial-keys", "a TypedDict cannot mark individual keys as required or optional",
                (r"each key is required", r"required and others.{0,60}(potentially[- ]missing|optional)",
                 r"some keys.{0,60}required.{0,80}(missing|optional)", r"total=false",
                 r"declare some keys as required"),
                ("optional keys", "required keys", "partially required", "some keys optional",
                 "missing keys", "not all keys required", "required or optional", "optional or required",
                 "individual keys", "individually", "per-key", "some keys")),                        # 589, 655
    ProblemSpec("untyped-kwargs", "**kwargs can only be typed uniformly",
                (r"\*\*kwargs.{0,160}(same type|precise|restrict)", r"type of a ``\*\*kwargs``"),
                ("kwargs", "keyword arguments", "**kwargs")),                                         # 589, 692
    ProblemSpec("mutable-typeddict-items", "TypedDict items cannot be declared read-only",
                (r"read-only (items|keys|parameters)", r"mutable type.{0,100}read-only", r"prevent.{0,60}mutat"),
                ("read-only", "readonly", "immutable", "frozen typeddict", "mutate")),                # 705
    ProblemSpec("unsafe-narrowing", "type narrowing is unsound or cannot narrow the negative case",
                (r"narrow(ing)?.{0,160}(unsound|unsafe|negative|else branch|surpris|incorrect|not narrow)",
                 r"(negative|else) (case|branch).{0,120}narrow", r"type narrowing"),
                ("narrowing", "narrow the type", "else branch", "negative case", "type guard", "isinstance")),  # 647, 724, 742
    ProblemSpec("decorator-signatures", "decorators and higher-order functions lose parameter types",
                (r"forwarding the parameter types", r"decorators?.{0,160}(signature|parameter types|lose|preserve)",
                 r"higher[- ]order function"),
                ("decorator signature", "preserve signature", "forward parameters", "wrapper function",
                 "higher-order")),                                                                   # 612
    ProblemSpec("string-injection", "APIs need to reject untrusted strings (injection attacks)",
                (r"sql injection", r"injection attack", r"untrusted (string|input|data)"),
                ("injection", "sql injection", "untrusted string", "shell injection")),              # 675
    ProblemSpec("distributing-type-info", "type information must be distributed by hand",
                (r"distribut\w+ (manually|type information|type hints)", r"stub[- ]only package",
                 r"package.{0,60}type information"),
                ("distribute types", "stub files", "ship types", "package types", "typed package")),  # 561
    ProblemSpec("accidental-override", "renaming a base-class method silently breaks overrides",
                (r"base class changes", r"overrid\w+.{0,120}(typo|rename|accidental|silently|mistake)",
                 r"no longer overrid"),
                ("renamed method", "base class change", "accidental override", "mark override")),     # 698
    ProblemSpec("invisible-deprecations", "deprecations are invisible to static checkers",
                (r"deprecat\w+.{0,160}(static checker|statically|type checker|warn)", r"mark.{0,60}deprecated"),
                ("deprecation warning", "mark as deprecated", "statically deprecated")),             # 702
    ProblemSpec("runtime-use-of-annotations", "annotations are also used for non-typing runtime metadata",
                (r"(runtime|at run ?time).{0,100}annotations.{0,80}(metadata|other uses|non-typing)",
                 r"non-typing (usage|use)s? of annotations", r"arbitrary metadata",
                 r"other uses? of annotations"),
                ("metadata", "runtime annotations", "non-typing use", "annotation metadata",
                 "attach metadata")),                                                               # 593, 563, 649
    ProblemSpec("returning-self", "methods returning an instance of their class need a bound TypeVar",
                (r"return(s|ing)? an instance of (their|its|the) class", r"return ``self``", r"returns? self"),
                ("return self", "instance of their class", "fluent interface", "method chaining")),   # 673
    ProblemSpec("dataclass-like-libraries", "attrs/pydantic-style classes cannot be described to checkers",
                (r"(attrs|pydantic).{0,120}(behav|describe)", r"dataclass[- ]like", r"similar to dataclasses"),
                ("attrs", "pydantic", "dataclass-like", "custom dataclass", "orm models")),           # 681
    ProblemSpec("generic-defaults", "type parameters cannot have defaults",
                (r"defaults? for type parameters", r"type defaults"),
                ("default type parameter", "typevar default", "default for typevar", "default type argument")),  # 696
    ProblemSpec("literal-values", "a type cannot say 'exactly this value'",
                (r"literally a specific value", r"specific (literal )?value", r"only.{0,40}specific (strings|values)"),
                ("specific value", "literal value", "exact value", "string enum")),                   # 586
    ProblemSpec("no-final-marker", "no way to declare a name, method or class as final",
                (r"should not be overridden", r"cannot be (subclassed|overridden|reassigned|re-assigned)",
                 r"declaring that a (method|class|variable)"),
                ("constant", "cannot be overridden", "prevent subclassing", "final class", "final method")),  # 591
    ProblemSpec("class-vs-instance-vars", "class variables cannot be told apart from instance variables",
                (r"class variable.{0,120}instance variable", r"``classvar``"),
                ("class variable", "class attribute", "instance variable")),                           # 526
    ProblemSpec("precise-dict-types", "dict value types depend on the key (JSON-like objects)",
                (r"type of a dictionary value depends on the string value of the key",
                 r"heterogeneous dictionar", r"json objects?", r"fixed set of keys"),
                ("json", "heterogeneous dict", "dict with fixed keys", "dictionary keys with different types",
                 "dict schema", "dict literal", "dict literals", "shape of a dict")),                 # 589
    ProblemSpec("type-alias-ambiguity", "a plain assignment cannot be told apart from a type alias",
                (r"explicitly declare an assignment as a type alias", r"ambigu\w+.{0,80}alias",
                 r"alias.{0,80}ambigu"),
                ("type alias ambiguity", "is this an alias", "declare alias")),                        # 613
]


# Type checkers named in implementation sections. (24 of 30 PEPs name mypy.)
TYPE_CHECKERS: list[tuple[str, str, str]] = [  # (key, label, regex)
    ("mypy", "mypy", r"\bmypy\b"),
    ("pyright", "pyright", r"\bpyright\b"),
    ("pyre", "Pyre", r"\bpyre\b"),
    ("pytype", "pytype", r"\bpytype\b"),
    ("pycharm", "PyCharm", r"\bpycharm\b"),
]

# ---- Section classification used by the rules ----------------------------

# Top-level section title -> BUILDS_ON weight (rule R-BLD). None = ignore.
_SECTION_WEIGHTS: list[tuple[str, float | None]] = [
    (r"acknowledg|^references?$|footnote|copyright|resources|open issues|appendix", None),
    (r"reject|objection|postponed|deferred|^alternatives\b|other proposals considered", None),  # -> R-CON instead
    (r"abstract|motivation", 1.0),
    (r"rationale", 0.8),
    (r"backward|compat|implementation|how to teach|security|performance", 0.4),
]
_DEFAULT_SECTION_WEIGHT = 0.5
_REJECTED_HEADING = re.compile(
    r"reject|objection|postponed|deferred|^alternatives\b|other proposals considered", re.I)
_MOTIVATION_HEADING = re.compile(r"abstract|motivation|rationale", re.I)
_IMPLEMENTATION_HEADING = re.compile(r"implementation", re.I)
_BUILDS_ON_CUE = re.compile(
    r"introduced (in|by)|builds? on|based on|extend(s|ed|ing)?|as (defined|specified|described) in"
    r"|defined in|specified in|follow(s|ing) the", re.I)
_INTRO_CUE = re.compile(
    r"\b(this pep|in this pep,? we|we)\s+(introduc|propos|add|defin|specif|formaliz)\w*", re.I)
_INTRODUCED_BY = re.compile(r"\bintroduc\w*\b", re.I)
_REASON_CUE = re.compile(
    r"reject|because|not clear|unclear|confus|inconsisten|instead|would (be|require|make|break)|downside|drawback"
    r"|problem|limited need|incompatib|ambigu|surpris|not (a )?good|cumbersome|unsightly|overkill|complex", re.I)
_POSTPONED_CUE = re.compile(r"future|later|extension|postpone|defer|left out|out of scope", re.I)


def section_weight(top_title: str) -> float | None:
    """BUILDS_ON weight for a reference found under this top-level heading."""
    t = top_title.lower()
    for pattern, weight in _SECTION_WEIGHTS:
        if re.search(pattern, t):
            return weight
    return _DEFAULT_SECTION_WEIGHT


# ===========================================================================
# 3. MAPPING RULES
# ===========================================================================

@dataclass
class Extraction:
    """What one PEP contributes to the graph."""

    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    # INTRODUCES is decided across PEPs (one introducer per construct), so each
    # PEP only reports candidates: (target id, priority, evidence).
    introduces_candidates: list[tuple[str, int, Evidence]] = field(default_factory=list)
    # EXTENDS candidates become edges once the introducer is known.
    extends_candidates: list[tuple[str, Evidence]] = field(default_factory=list)


def _ev(pep: int, section: str, rule: str, snippet: str) -> Evidence:
    return Evidence(pep, section, rule, re.sub(r"\s+", " ", snippet).strip()[:220])


def _code_matches(spec_code: str, text: str) -> list[str]:
    """Code spans in `text` that match a construct/syntax code regex."""
    pat = re.compile(spec_code)
    return [span for span in code_spans(text) if pat.search(span)]


def _syntax_in_text(spec: SyntaxSpec, text: str) -> str | None:
    """A syntax form is matched in code spans *and* in literal code blocks
    (lines indented after '::'), because `type X = ...` and `class C[T]:`
    mostly appear in blocks, not inline."""
    if spec.prose:
        m = re.search(spec.prose, strip_rst(text), re.I)
        return m.group(0) if m else None
    for span in code_spans(text):
        if re.search(spec.code, span):
            return span
    block_lines = [ln for ln in text.splitlines() if ln.startswith(("    ", "\t"))]
    block = "\n".join(block_lines)
    m = re.search(spec.code, block, re.M)
    return m.group(0) if m else None


def pep_entity(number: int, header: dict[str, str], in_slice: bool) -> Entity:
    """The PEP node itself (rule: header fields)."""
    title = re.sub(r"``|\\", "", header.get("Title", f"PEP {number}"))
    attrs = {
        "number": number,
        "title": title,
        "status": header.get("Status"),
        "type": header.get("Type"),
        "python_version": header.get("Python-Version"),
        "created": header.get("Created"),
        "in_slice": in_slice,
        "url": f"https://peps.python.org/pep-{number:04d}/",
    }
    label = f"PEP {number}: {title}" if in_slice else f"PEP {number} (outside slice)"
    return Entity(make_id("PEP", str(number)), "PEP", label,
                  sources=[{"pep": number, "section": "Header"}], attributes=attrs)


def extract_pep(number: int, text: str) -> Extraction:
    """Run every mapping rule on one PEP's raw text."""
    out = Extraction()
    header = parse_header(text)
    secs = split_sections(text)
    children = section_tree(secs)
    me = make_id("PEP", str(number))
    title = re.sub(r"``|\\", "", header.get("Title", ""))
    abstract = next((s for s in secs if s.title.lower().startswith("abstract")), None)

    # ---- R-SUP: SUPERSEDES from Replaces / Superseded-By -------------------
    for num in re.findall(r"\d+", header.get("Replaces", "")):
        out.relationships.append(Relationship(
            "SUPERSEDES", me, make_id("PEP", num), 1.0,
            [_ev(number, "Header", "R-SUP Replaces", f"Replaces: {header['Replaces']}")]))
    for num in re.findall(r"\d+", header.get("Superseded-By", "")):
        out.relationships.append(Relationship(
            "SUPERSEDES", make_id("PEP", num), me, 1.0,
            [_ev(number, "Header", "R-SUP Superseded-By", f"Superseded-By: {header['Superseded-By']}")]))

    # ---- R-VER: AVAILABLE_IN from Python-Version ---------------------------
    ver = header.get("Python-Version", "").strip()
    if re.fullmatch(r"\d+\.\d+", ver):
        major, minor = (int(x) for x in ver.split("."))
        out.entities.append(Entity(make_id("PythonVersion", ver), "PythonVersion", f"Python {ver}",
                                   sources=[{"pep": number, "section": "Header"}],
                                   attributes={"version": ver, "sort_key": [major, minor]}))
        out.relationships.append(Relationship(
            "AVAILABLE_IN", me, make_id("PythonVersion", ver), 1.0,
            [_ev(number, "Header", "R-VER Python-Version", f"Python-Version: {ver}")]))

    # ---- R-BLD: BUILDS_ON from references outside rejected sections --------
    # R-BLD Requires (header): weight 1.0. Zero occurrences in the slice, but
    # the rule is kept so the graph is right if the corpus grows.
    for num in re.findall(r"\d+", header.get("Requires", "")):
        out.relationships.append(Relationship(
            "BUILDS_ON", me, make_id("PEP", num), 1.0,
            [_ev(number, "Header", "R-BLD Requires", f"Requires: {header['Requires']}")]))

    rejected_root: set[int] = set()
    for i, sec in enumerate(secs):
        if sec.level >= 1 and _REJECTED_HEADING.search(sec.title):
            rejected_root.add(i)

    def under_rejected(i: int) -> bool:
        # A section is "rejected" if it or any ancestor heading matches.
        return any(_REJECTED_HEADING.search(t) for t in secs[i].path)

    for i, sec in enumerate(secs):
        if sec.level == 0 or under_rejected(i):
            continue
        weight = section_weight(sec.path[0])
        if weight is None:
            continue
        for ref in find_references(sec.body, sec.path_str, self_number=number):
            w = weight
            rule = "R-BLD section-weight"
            # Cue boost: the sentence around the reference says "introduced
            # in" / "builds on" / "extends" / "as defined in" -> at least 0.8.
            if _BUILDS_ON_CUE.search(ref.snippet):
                w, rule = max(w, 0.8), "R-BLD cue-boost"
            out.relationships.append(Relationship(
                "BUILDS_ON", me, make_id("PEP", str(ref.number)), w,
                [_ev(number, sec.path_str, rule, ref.snippet)]))

    # ---- R-INT: INTRODUCES candidates (resolved across PEPs by build) ------
    # Priority 1: "``X`` ... introduced in :pep:`N`" or ":pep:`N` introduced ``X``"
    #             anywhere in any PEP (reported for PEP N, not for this PEP).
    for sec in secs:
        if sec.level == 0:
            continue
        for sent in sentences(sec.body):
            if not _INTRODUCED_BY.search(sent):
                continue
            # The construct must sit right next to the "introduced" phrase:
            #   A: ``X`` [few words] introduced in/by :pep:`N`
            #   B: :pep:`N` introduced [the] [few words] ``X``
            for m in _INTRODUCED_A.finditer(sent):
                span, pep_num = m.group(1) or m.group(2), m.group(3)
                _add_intro_candidate(out, number, sec.path_str, span, pep_num, sent)
            for m in _INTRODUCED_B.finditer(sent):
                pep_num, span = m.group(1), m.group(2) or m.group(3)
                _add_intro_candidate(out, number, sec.path_str, span, pep_num, sent)
    # Only a Standards Track PEP can introduce a feature (PEP 483 is theory).
    # And a PEP whose title names a *syntax form* (604 "X | Y", 677 "Callable
    # Type Syntax") is about notation for an existing construct, so its title
    # and abstract must not make it the introducer of that construct.
    standards_track = header.get("Type", "").startswith("Standards")
    title_is_syntax = any(t.lower() in title.lower() for sx in SYNTAX for t in sx.title)

    # Priority 2: construct / syntax named in this PEP's Title.
    if standards_track and not title_is_syntax:
        for cs in CONSTRUCTS:
            if any(re.search(r"\b" + re.escape(t) + r"\b", title, re.I) for t in cs.title) \
                    or (cs.title == () and re.search(r"\b" + re.escape(cs.name) + r"\b", title)):
                out.introduces_candidates.append((make_id("Construct", cs.key), 2,
                                                  _ev(number, "Header", "R-INT title", f"Title: {title}")))
    if standards_track:
        for sx in SYNTAX:
            if not sx.never_adopted and any(t.lower() in title.lower() for t in sx.title):
                out.introduces_candidates.append((make_id("Syntax", sx.key), 2,
                                                  _ev(number, "Header", "R-INT title", f"Title: {title}")))
    # Priority 3: an Abstract sentence "This PEP introduces/proposes/adds ``X``".
    if abstract is not None and standards_track:
        for sent in sentences(abstract.body):
            if not _INTRO_CUE.search(sent):
                continue
            spans = code_spans(sent)
            if not title_is_syntax:
                for cs in CONSTRUCTS:
                    if any(re.search(cs.code, sp) for sp in spans):
                        out.introduces_candidates.append((make_id("Construct", cs.key), 3,
                                                          _ev(number, abstract.path_str, "R-INT abstract-cue", sent)))
            for sx in SYNTAX:
                if sx.never_adopted:
                    continue
                if any(re.search(sx.code, sp) for sp in spans) or \
                        any(t.lower() in sent.lower() for t in sx.title):
                    out.introduces_candidates.append((make_id("Syntax", sx.key), 3,
                                                      _ev(number, abstract.path_str, "R-INT abstract-cue", sent)))
    # Priority 4: a section heading names the construct (e.g. 484 "Union types",
    # "Callable", "The ``Any`` type", "Function overloading"). Ties go to the
    # lowest PEP number.
    # Priority 5: earliest mention. The oldest Standards Track PEP whose body
    # uses the construct/syntax as code. Weakest rule; catches things like
    # ``Optional`` and ``ClassVar`` that no PEP title or heading names.
    if standards_track:
        for i, sec in enumerate(secs):
            if sec.level == 0 or under_rejected(i):
                continue
            plain = strip_rst(sec.title)
            for cs in CONSTRUCTS:
                if re.search(r"\b" + re.escape(cs.name) + r"(s|ing|ed)?\b", plain):
                    out.introduces_candidates.append((make_id("Construct", cs.key), 4,
                                                      _ev(number, sec.path_str, "R-INT section-heading",
                                                          f"Heading: {sec.title}")))
            for cs in CONSTRUCTS:
                hits = _code_matches(cs.code, sec.body)
                if hits:
                    out.introduces_candidates.append((make_id("Construct", cs.key), 5,
                                                      _ev(number, sec.path_str, "R-INT earliest-mention",
                                                          f"uses ``{hits[0]}``")))
            for sx in SYNTAX:
                hit = None if sx.never_adopted else _syntax_in_text(sx, sec.body)
                if hit:
                    out.introduces_candidates.append((make_id("Syntax", sx.key), 5,
                                                      _ev(number, sec.path_str, "R-INT earliest-mention",
                                                          f"uses ``{hit.strip()}``")))

    # ---- R-EXT candidates: construct/syntax mentioned as code in Title/Abstract
    head_text = title + "\n" + (abstract.body if abstract else "")
    for cs in CONSTRUCTS:
        hit = _code_matches(cs.code, head_text)
        if hit or any(re.search(r"\b" + re.escape(t) + r"\b", title, re.I) for t in cs.title):
            where = "Header" if not hit else (abstract.path_str if abstract else "Header")
            out.extends_candidates.append((make_id("Construct", cs.key),
                                           _ev(number, where, "R-EXT title/abstract-mention",
                                               f"mentions ``{hit[0]}``" if hit else f"Title: {title}")))
    for sx in SYNTAX:
        hit = _syntax_in_text(sx, head_text)
        if hit:
            out.extends_candidates.append((make_id("Syntax", sx.key),
                                           _ev(number, abstract.path_str if abstract else "Header",
                                               "R-EXT title/abstract-mention", f"mentions ``{hit}``")))

    # ---- R-PRB: ADDRESSES_PROBLEM from cues in Abstract/Motivation/Rationale
    for i, sec in enumerate(secs):
        if sec.level == 0 or not _MOTIVATION_HEADING.search(sec.path[0]) or under_rejected(i):
            continue
        low = sec.body.lower()
        weight = 0.7 if "rationale" in sec.path[0].lower() else 1.0
        for pb in PROBLEMS:
            for cue in pb.cues:
                m = re.search(cue, low, re.S)
                if m:
                    out.entities.append(Entity(make_id("Problem", pb.key), "Problem", pb.label,
                                               sources=[{"pep": number, "section": sec.path_str}]))
                    out.relationships.append(Relationship(
                        "ADDRESSES_PROBLEM", me, make_id("Problem", pb.key), weight,
                        [_ev(number, sec.path_str, f"R-PRB cue /{cue}/", _snippet(sec.body, m.start(), m.end()))]))
                    break  # one piece of evidence per (section, problem) is enough

    # ---- R-REJ + R-CON: RejectedIdea entities and what they concern ---------
    # Only the outermost rejected section counts (a nested "Alternatives" under
    # "Rejected Ideas" is not a second root).
    roots = [i for i in rejected_root if not any(
        _REJECTED_HEADING.search(t) for t in secs[i].path[:-1])]
    for root in roots:
        root_sec = secs[root]
        root_disp = ("postponed" if re.search(r"postponed|deferred", root_sec.title, re.I)
                     else "objection" if re.search(r"objection", root_sec.title, re.I) else "rejected")
        leaves = _leaf_descendants(root, children)
        if leaves:
            ideas = [(secs[i].title, secs[i].body, secs[i].path_str, root_disp) for i in leaves]
        else:
            ideas = _bullet_ideas(root_sec, root_disp)
        for idea_title, idea_body, idea_path, disp in ideas:
            _emit_idea(out, number, me, idea_title, idea_body, idea_path, disp)

    # ---- R-IMP: IMPLEMENTED_BY from implementation sections ----------------
    for sec in secs:
        if sec.level == 0 or not any(_IMPLEMENTATION_HEADING.search(t) for t in sec.path):
            continue
        for key, label, pat in TYPE_CHECKERS:
            m = re.search(pat, sec.body, re.I)
            if m:
                out.entities.append(Entity(make_id("TypeChecker", key), "TypeChecker", label,
                                           sources=[{"pep": number, "section": sec.path_str}]))
                out.relationships.append(Relationship(
                    "IMPLEMENTED_BY", me, make_id("TypeChecker", key), 0.5,
                    [_ev(number, sec.path_str, "R-IMP checker-in-implementation",
                         _snippet(sec.body, m.start(), m.end()))]))
    return out


_CODE_SPAN_SRC = r"(?:``([^`\n]+)``|:(?:py:)?(?:class|data|func|meth|mod|obj|attr|term):`~?([^`<]+?)(?:\s*<[^>]*>)?`)"
_PEP_ROLE_SRC = r":pep:`(?:[^`<]*<)?\s*(\d+)[^`]*`"
_INTRODUCED_A = re.compile(
    _CODE_SPAN_SRC + r"(?:\s+\w+){0,4}\s+(?:was |were |first |originally |initially )*introduced\s+(?:in|by)\s+" + _PEP_ROLE_SRC)
_INTRODUCED_B = re.compile(
    _PEP_ROLE_SRC + r"\s+(?:\w+\s+){0,2}introduced\s+(?:the\s+|a\s+|an\s+)?(?:\w+\s+){0,3}" + _CODE_SPAN_SRC)


def _add_intro_candidate(out: "Extraction", number: int, path: str, span: str,
                         pep_num: str, sent: str) -> None:
    """Record ``pep:N INTRODUCES construct`` for the construct matching `span`."""
    introducer = make_id("PEP", pep_num)
    for cs in CONSTRUCTS:
        if re.search(cs.code, span):
            out.introduces_candidates.append((
                make_id("Construct", cs.key), 1,
                Evidence(number, path, "R-INT cross-reference",
                         f"[{introducer}] " + _snippet(sent, 0, len(sent), 200))))


def _leaf_descendants(i: int, children: dict[int, list[int]]) -> list[int]:
    """Sub-sections of `i` that have no sub-sections themselves."""
    out: list[int] = []
    for c in children[i]:
        out.extend(_leaf_descendants(c, children) if children[c] else [c])
    return out


_BULLET = re.compile(r"^[*\-+]\s+", re.M)


def _bullet_ideas(sec: Section, root_disp: str) -> list[tuple[str, str, str, str]]:
    """A rejected section with no subsections: each top-level bullet is one idea.

    The paragraph introducing a bullet group decides the disposition: if it
    talks about the future / extensions / leaving things out, the bullets
    under it are 'postponed' rather than 'rejected' (PEP 589 has both groups).
    """
    ideas: list[tuple[str, str, str, str]] = []
    paragraphs = re.split(r"\n\s*\n", sec.body)
    disp = root_disp
    for para in paragraphs:
        if _BULLET.match(para.strip()):
            for item in _BULLET.split(para.strip()):
                item = re.sub(r"\s+", " ", item).strip()
                if len(item) < 20:
                    continue
                first = sentences(strip_rst(item))[0] if sentences(strip_rst(item)) else strip_rst(item)
                title = first if len(first) <= 90 else first[:90].rsplit(" ", 1)[0] + "…"
                ideas.append((title, item, f"{sec.path_str} > bullet", disp))
        elif para.strip():
            disp = "postponed" if _POSTPONED_CUE.search(para) else root_disp
    return ideas


def _reason_sentence(body: str) -> str:
    """The sentence that explains why an idea was turned down.

    - An objection written as PROS/CONS lists (PEP 604) gives its CONS.
    - Otherwise the first sentence with a reason cue ("rejected because",
      "confusing", "inconsistent", ...). If that sentence only announces the
      reasons ("...rejected it for two reasons."), the next sentence is added.
    - Falls back to the first sentence.
    """
    cons = re.search(r"CONS:\s*(.+?)(?:\n\s*\n\s*\n|$)", body, re.S)
    if cons:
        items = [re.sub(r"^-\s*", "", re.sub(r"\s+", " ", x).strip())
                 for x in re.split(r"\n\s*-\s+", cons.group(1)) if x.strip(" -\n")]
        return ("Cons: " + "; ".join(strip_rst(i) for i in items))[:220]
    sents = sentences(body)
    if not sents:
        return ""
    idx = next((i for i, s in enumerate(sents) if _REASON_CUE.search(s)), 0)
    reason = sents[idx]
    if re.search(r"(reasons?|following|because)[.:]$", reason) and idx + 1 < len(sents):
        reason += " " + sents[idx + 1]
    reason = re.sub(r"^\([^)]*\)\s*", "", reason)  # drop a leading "(Refer to ...)"
    return strip_rst(reason)[:220]


def _emit_idea(out: Extraction, number: int, me: str, title: str, body: str,
               path: str, disposition: str) -> None:
    """Create one RejectedIdea entity, its REJECTED_ALTERNATIVE edge and its
    CONCERNS edges (rules R-REJ and R-CON)."""
    plain_title = re.sub(r"^\s*\d+[.)]\s*", "", strip_rst(title)).strip()
    if re.search(r"postponed|deferred|future", plain_title, re.I):
        disposition = "postponed"
    idea_id = make_id("RejectedIdea", f"{number}/{slugify(title)}")
    reason = _reason_sentence(body)
    keywords = sorted({w for w in re.findall(r"[a-z][a-z0-9_]+", plain_title.lower())
                       if w not in STOPWORDS})
    out.entities.append(Entity(
        idea_id, "RejectedIdea", f"{plain_title} (PEP {number}, {disposition})",
        sources=[{"pep": number, "section": path}],
        attributes={"title": plain_title, "pep": number, "disposition": disposition,
                    "reason": reason, "keywords": keywords}))
    out.relationships.append(Relationship(
        "REJECTED_ALTERNATIVE", me, idea_id, 1.0,
        [_ev(number, path, "R-REJ rejected-section", reason or plain_title)]))

    # A bullet idea's title is its own first sentence: don't scan it twice.
    full = body if strip_rst(body).startswith(plain_title[:30]) else title + "\n" + body
    for cs in CONSTRUCTS:
        hits = _code_matches(cs.code, full)
        in_title = re.search(r"\b" + re.escape(cs.name) + r"s?\b", plain_title) or \
            any(re.search(r"\b" + re.escape(t) + r"\b", plain_title, re.I) for t in cs.title)
        if hits or in_title:
            out.relationships.append(Relationship(
                "CONCERNS", idea_id, make_id("Construct", cs.key), 0.9 if in_title else 0.7,
                [_ev(number, path, "R-CON construct-in-idea", f"``{hits[0]}``" if hits else plain_title)]))
    for sx in SYNTAX:
        hit = _syntax_in_text(sx, full) or next(
            (w for w in sx.words if w in plain_title.lower()), None)
        if hit:
            out.relationships.append(Relationship(
                "CONCERNS", idea_id, make_id("Syntax", sx.key), 0.9,
                [_ev(number, path, "R-CON syntax-in-idea", hit)]))
    plain = strip_rst(full)
    low = plain.lower()
    for pb in PROBLEMS:
        for cue in pb.cues:
            m = re.search(cue, low, re.S)
            if m:
                out.entities.append(Entity(make_id("Problem", pb.key), "Problem", pb.label,
                                           sources=[{"pep": number, "section": path}]))
                out.relationships.append(Relationship(
                    "CONCERNS", idea_id, make_id("Problem", pb.key), 0.8,
                    [_ev(number, path, f"R-CON problem-cue /{cue}/", _snippet(plain, m.start(), m.end()))]))
                break
    for ref in find_references(full, path, self_number=number):
        out.relationships.append(Relationship(
            "CONCERNS", idea_id, make_id("PEP", str(ref.number)), 0.8,
            [_ev(number, path, "R-CON pep-in-idea", ref.snippet)]))


# Stopwords for keyword extraction (also used by the reasoner's matcher).
STOPWORDS = frozenset("""
a an the and or of to in on for with as by at from into is are was were be been being
it its this that these those there here than then so if not no nor but about over
use using used uses make making made allow allowing allows add adding added new
instead rather only also just can could should would may might will do does did
how why what when which who whom whose where does python pep peps type types typing
syntax way ways let lets i we you they our your their other another some any all
each every more most much many such like via per vs versus
""".split())
