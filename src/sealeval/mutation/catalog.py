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


def _seg(src: str, node: ast.AST) -> Optional[str]:
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
        arg_segs = [_seg(src, a) for a in node.args]
        if any(s is None for s in arg_segs):
            continue
        arg_segs[-1] = f"({arg_segs[-1]}) - 1"
        new = "range(" + ", ".join(arg_segs) + ")"  # type: ignore[arg-type]
        out.append(Candidate("off_by_one", node, new, "narrowed range upper bound by 1"))
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
        seg = _seg(src, test)
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
        left = _seg(src, node.left)
        right = _seg(src, node.right)
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
        obj = _seg(src, node.func.value)
        key = _seg(src, node.args[0])
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

MVP_ARCHETYPES = ("off_by_one", "inverted_condition", "wrong_operator", "null_deref", "swallowed_exception")


def find_candidates(src: str, archetypes: tuple[str, ...] = MVP_ARCHETYPES) -> list[Candidate]:
    """Parse ``src`` and return all candidate mutations for the given archetypes.

    Returns an empty list if the source does not parse (the file is unusable as a
    seeding target but is still copied verbatim into the corpus by the seeder).
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[Candidate] = []
    for name in archetypes:
        finder = ARCHETYPES.get(name)
        if finder is None:
            raise KeyError(f"unknown archetype {name!r}")
        out.extend(finder(tree, src))
    return out
