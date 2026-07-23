"""Bug archetypes for AST-guided injection.

Each archetype scans a parsed module and yields ``Candidate`` mutations. A
candidate is a single-line, source-span replacement: we keep the rest of the file
(and therefore every other line number) byte-identical, so the injection key's
``line`` is exact and the injected diff is exactly one line. This is the most
defensible design against a reviewer who checks for tells: the mutated file looks
like ordinary code, differing from clean code only at the seeded site.

Archetypes (MVP + a couple of cheap extras for site availability):
    off_by_one        range(n) -> range((n) - 1)
    inverted_condition  if <test>: -> if not (<test>):
    wrong_operator    a + b <-> a - b
    null_deref        d.get(k, default) -> d.get(k)   (drops the None-fallback)
    swallowed_exception  raise X  ->  pass            (inside an except handler)

Every candidate is independently compile-guarded by the seeder (re-parse the whole
mutated file) before it is accepted, so a candidate that produces invalid syntax is
silently dropped.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Candidate:
    """One concrete mutation: replace ``node``'s source span with ``new_src``."""

    archetype: str
    node: ast.AST
    new_src: str
    description: str

    @property
    def line(self) -> int:
        return int(getattr(self.node, "lineno", 0))


# ---------------------------------------------------------------------------
# Span helpers (single-line nodes only -> line numbers stay stable)
# ---------------------------------------------------------------------------


def _single_line(node: ast.AST) -> bool:
    lo = getattr(node, "lineno", None)
    hi = getattr(node, "end_lineno", None)
    return lo is not None and hi is not None and lo == hi


def source_segment(src: str, node: ast.AST) -> Optional[str]:
    """Public: exact source text of an AST node (``ast.get_source_segment``), or None.
    Used by the seeder to record the original segment of each injection."""
    try:
        return ast.get_source_segment(src, node)
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return None


def replace_span(src: str, node: ast.AST, new_text: str) -> str:
    """Replace ``node``'s single-line source span with ``new_text``.

    Only valid for nodes where ``lineno == end_lineno`` (guaranteed by callers).
    Column offsets index into the line's character string (exact for ASCII source;
    the seeder's compile-guard catches any rare multibyte edge case).
    """
    lines = src.splitlines(keepends=True)
    i = node.lineno - 1  # type: ignore[attr-defined]
    line = lines[i]
    lines[i] = line[: node.col_offset] + new_text + line[node.end_col_offset :]  # type: ignore[attr-defined]
    return "".join(lines)


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------


def _off_by_one(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "range" or not node.args or node.keywords:
            continue
        if not _single_line(node):
            continue
        arg_segs = [source_segment(src, a) for a in node.args]
        if any(s is None for s in arg_segs):
            continue
        # narrow the STOP bound, never the step: range(stop) -> arg0; range(start, stop[, step]) -> arg1.
        # (The old `arg_segs[-1]` mutated the STEP on 3-arg range -- a step change mislabeled as off-by-one.)
        stop_i = 0 if len(arg_segs) == 1 else 1
        arg_segs[stop_i] = f"({arg_segs[stop_i]}) - 1"
        new = "range(" + ", ".join(arg_segs) + ")"  # type: ignore[arg-type]
        out.append(Candidate("off_by_one", node, new, "narrowed range stop bound by 1"))
    return out


def _inverted_condition(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not _single_line(test):
            continue
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            continue  # avoid double-negation that reads as a no-op tell
        seg = source_segment(src, test)
        if seg is None:
            continue
        out.append(
            Candidate("inverted_condition", test, f"not ({seg})", "negated an if-condition")
        )
    return out


def _is_strlike(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _wrong_operator(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))):
            continue
        if not _single_line(node):
            continue
        if _is_strlike(node.left) or _is_strlike(node.right):
            continue  # skip obvious string concat -> avoids a trivial TypeError tell
        left = source_segment(src, node.left)
        right = source_segment(src, node.right)
        if left is None or right is None:
            continue
        flipped = "-" if isinstance(node.op, ast.Add) else "+"
        new = f"({left}) {flipped} ({right})"
        out.append(Candidate("wrong_operator", node, new, "flipped + and - operator"))
    return out


def _null_deref(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or len(node.args) != 2 or node.keywords:
            continue
        if not _single_line(node):
            continue
        obj = source_segment(src, node.func.value)
        key = source_segment(src, node.args[0])
        if obj is None or key is None:
            continue
        new = f"{obj}.get({key})"
        out.append(Candidate("null_deref", node, new, "dropped .get() default -> may yield None"))
    return out


def _swallowed_exception(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for stmt in handler.body:
            if isinstance(stmt, ast.Raise) and _single_line(stmt):
                out.append(
                    Candidate("swallowed_exception", stmt, "pass", "swallowed a raised exception")
                )
    return out


# ---------------------------------------------------------------------------
# v2 archetypes -- IDIOMATIC mutants (no stylistic fingerprint)
#
# Why v2 exists: the v1 generators wrap their edits in parentheses -- `not (flag)`,
# `(n) - (0)`, `range((len(rows)) - 1)`. An independent reviewer wrote a "reviewer" out of
# three regexes (`not \(`, `\) [-+] \(`, `\) - 1\)`) -- no AST, no model, no understanding --
# and scored recall 0.80 / precision 0.77 against a v1-sealed key. A benchmark that can be
# beaten by tell-spotting measures tell-spotting, and it biases recall UP.
#
# v2 emits code that reads like ordinary code: comparison operators are FLIPPED rather than
# negated, and parentheses are added only where precedence genuinely requires them.
# v1 is left byte-identical so existing sealed keys still verify -- the archetype set is
# VERSIONED, not patched in place.
# ---------------------------------------------------------------------------

# operands that bind looser than the operator we splice them into -> need parens
_LOOSE = (ast.BoolOp, ast.IfExp, ast.Lambda, ast.Compare, ast.Await, ast.NamedExpr)
_CMP_FLIP = {ast.Lt: "<=", ast.LtE: "<", ast.Gt: ">=", ast.GtE: ">",
             ast.Eq: "!=", ast.NotEq: "==", ast.Is: "is not", ast.IsNot: "is",
             ast.In: "not in", ast.NotIn: "in"}
# NOTE: Lt->LtE (not Lt->GtE): an off-by-one on a boundary is a realistic defect, whereas a
# full inversion often changes behaviour so grossly that any smoke test catches it.


def _paren(seg: str, node: ast.AST, *, also=()) -> str:
    return f"({seg})" if isinstance(node, _LOOSE + tuple(also)) else seg


def _off_by_one_v2(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "range" or not node.args or node.keywords:
            continue
        if not _single_line(node):
            continue
        segs = [source_segment(src, a) for a in node.args]
        if any(s is None for s in segs):
            continue
        last = node.args[-1]
        # `a + b - 1` == `(a + b) - 1`, so only genuinely-looser operands get parens
        segs[-1] = _paren(segs[-1], last) + " - 1"
        out.append(Candidate("off_by_one", node, "range(" + ", ".join(segs) + ")",
                             "narrowed range upper bound by 1"))
    return out


def _inverted_condition_v2(tree: ast.AST, src: str) -> list[Candidate]:
    """Flip the comparison instead of wrapping it in `not (...)`."""
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _single_line(node.test):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op = _CMP_FLIP.get(type(test.ops[0]))
            left, right = source_segment(src, test.left), source_segment(src, test.comparators[0])
            if op is None or left is None or right is None:
                continue
            out.append(Candidate("inverted_condition", test, f"{left} {op} {right}",
                                 "flipped a comparison operator"))
        elif isinstance(test, ast.BoolOp) and len(test.values) >= 2:
            joiner = " or " if isinstance(test.op, ast.And) else " and "
            segs = [source_segment(src, v) for v in test.values]
            if any(s is None for s in segs):
                continue
            parts = [_paren(s, v) for s, v in zip(segs, test.values)]
            out.append(Candidate("inverted_condition", test, joiner.join(parts),
                                 "swapped and/or in a condition"))
        # anything else (bare call, name) is skipped: it cannot be inverted idiomatically
    return out


def _wrong_operator_v2(tree: ast.AST, src: str) -> list[Candidate]:
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))):
            continue
        if not _single_line(node) or _is_strlike(node.left) or _is_strlike(node.right):
            continue
        left, right = source_segment(src, node.left), source_segment(src, node.right)
        if left is None or right is None:
            continue
        flipped = "-" if isinstance(node.op, ast.Add) else "+"
        # `a - b + c` != `a - (b + c)`: a right operand at the SAME precedence needs parens
        rseg = _paren(right, node.right,
                      also=(ast.BinOp,) if isinstance(node.right, ast.BinOp)
                      and isinstance(node.right.op, (ast.Add, ast.Sub)) else ())
        out.append(Candidate("wrong_operator", node, f"{_paren(left, node.left)} {flipped} {rseg}",
                             "flipped + and - operator"))
    return out


def _null_deref_v2(tree: ast.AST, src: str) -> list[Candidate]:
    """Same edit as v1, but skips EQUIVALENT mutants: `.get(k, None)` == `.get(k)`."""
    out: list[Candidate] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or len(node.args) != 2 or node.keywords:
            continue
        if not _single_line(node):
            continue
        default = node.args[1]
        if isinstance(default, ast.Constant) and default.value is None:
            continue   # behaviour-identical -> must never enter a sealed key
        obj, key = source_segment(src, node.func.value), source_segment(src, node.args[0])
        if obj is None or key is None:
            continue
        out.append(Candidate("null_deref", node, f"{obj}.get({key})",
                             "dropped .get() default -> may yield None"))
    return out


# Registry: name -> finder. MVP default uses the first three; the extras widen
# site availability so a small repo can still reach the labeled-bug target.
ArchetypeFn = Callable[[ast.AST, str], list[Candidate]]

ARCHETYPES: dict[str, ArchetypeFn] = {
    "off_by_one": _off_by_one,
    "inverted_condition": _inverted_condition,
    "wrong_operator": _wrong_operator,
    "null_deref": _null_deref,
    "swallowed_exception": _swallowed_exception,
}

ARCHETYPES_V2: dict[str, ArchetypeFn] = {
    "off_by_one": _off_by_one_v2,
    "inverted_condition": _inverted_condition_v2,
    "wrong_operator": _wrong_operator_v2,
    "null_deref": _null_deref_v2,
    "swallowed_exception": _swallowed_exception,   # already idiomatic (`except: pass`)
}

# version -> registry. v1 is frozen for reproducing existing sealed runs; v2 is the default
# for NEW runs because v1 is tell-spottable (see the v2 note above).
ARCHETYPE_SETS: dict[str, dict[str, ArchetypeFn]] = {"v1": ARCHETYPES, "v2": ARCHETYPES_V2}
DEFAULT_ARCHETYPE_VERSION = "v2"

MVP_ARCHETYPES = ("off_by_one", "inverted_condition", "wrong_operator", "null_deref", "swallowed_exception")


def find_candidates(src: str, archetypes: tuple[str, ...] = MVP_ARCHETYPES,
                    version: str = DEFAULT_ARCHETYPE_VERSION) -> list[Candidate]:
    """Parse ``src`` and return all candidate mutations for the given archetypes.

    ``version`` selects the archetype set: ``"v2"`` (default) emits idiomatic mutants;
    ``"v1"`` reproduces the original, tell-spottable edits and exists only so previously
    sealed runs stay reproducible. Record the version in your pre-registration.

    Returns an empty list if the source does not parse (the file is unusable as a
    seeding target but is still copied verbatim into the corpus by the seeder).
    """
    registry = ARCHETYPE_SETS.get(version)
    if registry is None:
        raise KeyError(f"unknown archetype version {version!r} (have {sorted(ARCHETYPE_SETS)})")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[Candidate] = []
    for name in archetypes:
        finder = registry.get(name)
        if finder is None:
            raise KeyError(f"unknown archetype {name!r}")
        out.extend(finder(tree, src))
    return out
