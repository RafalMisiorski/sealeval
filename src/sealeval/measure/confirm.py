"""Confirmed, not flagged -- match findings against the sealed injection key.

This is the step where scores get quietly inflated: a system "found 40 issues", and whoever
scores it decides, after the fact, which of them count. Here the rule is fixed in code and the
tolerance is an explicit, pre-registerable parameter.

A finding is a TRUE POSITIVE only if BOTH hold:
  * the adjudicated verdict is GENUINE_BUG (a skeptic could not refute it), AND
  * it lands within ``line_tolerance`` lines of an injected site in the SAME file.
Each injection can be claimed at most once, so ten findings shotgunned at one bug score one hit,
not ten. Everything else is a false positive; unclaimed injections are false negatives.

**Declare ``line_tolerance`` in your pre-registration.** Widening it after seeing the results is
exactly the goalpost-move the seal exists to prevent. ASCII-only.
"""
from __future__ import annotations

from typing import Iterable, Optional


def _norm(p) -> str:
    s = str(p).replace("\\", "/")
    while s.startswith("./"):          # NOT lstrip("./"): that mangles ".hidden/x" and "../up"
        s = s[2:]
    return s


def _rec(r) -> tuple[str, int]:
    if isinstance(r, dict):
        return _norm(r.get("file", "")), int(r.get("line", -1))
    return _norm(getattr(r, "file", "")), int(getattr(r, "line", -1))


def match_findings(
    findings: Iterable,
    key_records: Iterable,
    *,
    line_tolerance: int = 2,
    genuine_verdict: str = "GENUINE_BUG",
    require_verdict: bool = True,
) -> dict:
    """Score findings against the sealed key.

    findings     : iterable of {file, line, verdict} (dicts or objects with those attributes)
    key_records  : the sealed injection key (``InjectionRecord``s or their dicts)
    line_tolerance: max |finding.line - injection.line| that still counts as the same site.
                    MUST be pre-registered; widening it post-hoc invalidates the run.
    require_verdict: if True, only findings whose verdict == genuine_verdict can score.

    Returns tp / fp / fn, recall, precision, and the pairings for audit.
    """
    injections = [_rec(r) for r in key_records]
    eligible, rejected = [], []
    for f in findings:
        if isinstance(f, dict):
            ffile, fline = _norm(f.get("file", "")), int(f.get("line", -1))
            verdict = str(f.get("verdict", ""))
        else:
            ffile, fline = _norm(getattr(f, "file", "")), int(getattr(f, "line", -1))
            verdict = str(getattr(f, "verdict", ""))
        row = {"file": ffile, "line": fline, "verdict": verdict}
        if require_verdict and verdict != genuine_verdict:
            rejected.append({**row, "reason": "verdict not %s" % genuine_verdict})
        else:
            eligible.append(row)

    # MAXIMUM BIPARTITE MATCHING (Kuhn), not greedy/nearest: findings at 11 and 9 against
    # injections at 10 and 12 with tolerance 2 must score TWO hits. Greedy takes 11->10 and then
    # strands 9, under-counting recall -- and under-counting is still a wrong number.
    adj = [[j for j, (ifile, iline) in enumerate(injections)
            if ifile == row["file"] and abs(row["line"] - iline) <= line_tolerance]
           for row in eligible]
    match_r: dict[int, int] = {}

    def _augment(u: int, seen: set) -> bool:
        for v in adj[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or _augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in range(len(eligible)):
        _augment(u, set())

    matched_left = {u: v for v, u in match_r.items()}
    matched = [{"file": eligible[u]["file"], "finding_line": eligible[u]["line"],
                "injection_line": injections[v][1]} for u, v in sorted(matched_left.items())]
    # A finding adjudicated GENUINE_BUG that matches no injection is NOT necessarily a false
    # positive: it may be a real pre-existing defect in the corpus. Reported separately so the
    # caller decides, instead of being silently charged against precision.
    confirmed_not_injected = [eligible[u] for u in range(len(eligible)) if u not in matched_left]

    if injections and not eligible and rejected:
        # every finding was filtered out by require_verdict -- almost always a wiring mistake
        # (findings passed straight from a reviewer, before adjudication, carry no `verdict`).
        # Silently reporting tp=0 here reads as "the system found nothing", which is a lie.
        raise ValueError(
            "no finding carried verdict=%r, so nothing could score. Adjudicate first (see "
            "sealeval.judge) or pass require_verdict=False if your findings are already "
            "confirmed." % genuine_verdict)

    tp = len(matched)
    fp_strict = len(confirmed_not_injected)
    fn = len(injections) - tp
    missed = [{"file": f, "line": l} for i, (f, l) in enumerate(injections) if i not in match_r]
    return {
        "tp": tp, "fp": fp_strict, "fn": fn,
        "recall": round(tp / len(injections), 4) if injections else None,
        "precision": round(tp / (tp + fp_strict), 4) if (tp + fp_strict) else None,
        "line_tolerance": line_tolerance,
        "matched": matched,
        "confirmed_not_injected": confirmed_not_injected,
        "rejected_by_verdict": rejected,
        "unmatched_findings": confirmed_not_injected + rejected,   # back-compat view
        "missed_injections": missed,
        "note": ("precision counts confirmed-but-not-injected findings as false positives; if your "
                 "corpus has real pre-existing defects, inspect confirmed_not_injected before "
                 "quoting precision."),
    }
