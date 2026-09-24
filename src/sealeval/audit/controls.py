"""Hidden controls inside a blind sample, a per-judge VOID rule, and packet hygiene.

Why this exists. A blind panel that grades a sample tells you what the panel thinks; it does not
tell you whether the panel can tell an obvious case apart. Hidden controls do: items whose
correct answer is known in advance, drawn INTO the sample with opaque ids so the judges cannot
tell them from the rest. A judge that misses too many is VOID for the run; a run with fewer than
two live judges is VOID.

The instrument works against its author too. In an internal audit (2026-09) the first run was
VOID because two of the six controls were the experimenter's own mistakes (a "negative" that was
a real provider; a "positive" whose excerpt showed navigation text, not the offer). The gate
caught the experimenter before any effect was read; the corrected run was sealed separately.
That is the behaviour you want from a gate, and the reason controls must be verified against
the exact material the judges receive (the excerpt), not against what the author believes.

Zero dependencies; operates on already-collected labels. You bring the judge call.
"""
from __future__ import annotations

import json
import random
from typing import Iterable, Mapping, Optional, Sequence

ROLE_SAMPLE = "sample"
ROLE_POS = "control_pos"
ROLE_NEG = "control_neg"
DEFAULT_EXPECTED = {ROLE_POS: "YES", ROLE_NEG: "NO"}


def draw_sample(population: Sequence[Mapping], *, strata: Mapping[str, int], controls: Mapping[str, str],
                seed: int, key: str = "id", stratum_key: str = "stratum") -> list:
    """Seeded per-stratum draw with the controls forced in, shuffled, re-keyed with opaque ids.

    ``strata``   stratum name -> number of items to draw from it (all if fewer exist).
    ``controls`` population key -> "positive" | "negative". Controls are never counted against a
                 stratum quota and never appear twice.

    Every row keeps its original fields plus ``role`` and an opaque ``id`` ("e001", ...); the
    original key is kept under ``source_key`` so results can be joined back after the run.
    """
    rng = random.Random(int(seed))
    role_of = {}
    for k, kind in controls.items():
        kind = str(kind).lower()
        if kind not in ("positive", "negative"):
            raise ValueError("control %r: kind must be positive|negative, got %r" % (k, kind))
        role_of[k] = ROLE_POS if kind == "positive" else ROLE_NEG
    rows = []
    for stratum, n in strata.items():
        cands = [p for p in population if p.get(stratum_key) == stratum and p.get(key) not in role_of]
        cands.sort(key=lambda p: str(p.get(key)))
        pick = cands if len(cands) <= int(n) else rng.sample(cands, int(n))
        rows += [{**p, "role": ROLE_SAMPLE} for p in pick]
    for p in population:
        if p.get(key) in role_of:
            rows.append({**p, "role": role_of[p.get(key)]})
    rng.shuffle(rows)
    out = []
    for i, r in enumerate(rows):
        r = dict(r)
        r["source_key"] = r.get(key)
        r["id"] = "e%03d" % (i + 1)
        out.append(r)
    return out


def judge_void(judgments: Mapping[str, Optional[str]], sample: Sequence[Mapping], *,
               expected: Mapping[str, str] = DEFAULT_EXPECTED, max_wrong: int = 2,
               max_unparsed: float = 0.20) -> dict:
    """Per-judge VOID rule. ``judgments`` maps sample id -> label (None = unparsed/missing).

    VOID when the judge gets more than ``max_wrong`` controls wrong OR left more than
    ``max_unparsed`` of the sample without a parseable answer. Both thresholds are pre-registered
    numbers, not tuned after the fact.
    """
    controls = [r for r in sample if r.get("role") in expected]
    wrong = [r["id"] for r in controls if judgments.get(r["id"]) != expected[r["role"]]]
    missing = [r["id"] for r in sample if judgments.get(r["id"]) is None]
    share = round(len(missing) / len(sample), 4) if sample else 0.0
    reasons = []
    if len(wrong) > max_wrong:
        reasons.append("%d of %d controls wrong (> %d)" % (len(wrong), len(controls), max_wrong))
    if share > max_unparsed:
        reasons.append("unparsed share %.2f (> %.2f)" % (share, max_unparsed))
    return {"void": bool(reasons), "controls": len(controls), "controls_wrong": len(wrong), "wrong_ids": wrong,
            "missing": len(missing), "unparsed_share": share, "reasons": reasons}


def panel_void(judgments_by_judge: Mapping[str, Mapping[str, Optional[str]]], sample: Sequence[Mapping], *,
               expected: Mapping[str, str] = DEFAULT_EXPECTED, max_wrong: int = 2, max_unparsed: float = 0.20,
               min_live: int = 2) -> dict:
    """Apply ``judge_void`` to every judge; the RUN is void with fewer than ``min_live`` live judges."""
    per = {j: judge_void(v, sample, expected=expected, max_wrong=max_wrong, max_unparsed=max_unparsed)
           for j, v in judgments_by_judge.items()}
    live = sorted(j for j, r in per.items() if not r["void"])
    void = sorted(j for j, r in per.items() if r["void"])
    return {"per_judge": per, "live": live, "void_judges": void, "run_void": len(live) < min_live,
            "reason": None if len(live) >= min_live else "fewer than %d live judges" % min_live}


def members(judgments_by_judge: Mapping[str, Mapping[str, Optional[str]]], sample: Sequence[Mapping], live: Sequence[str], *,
            need: int = 2, yes: str = "YES") -> dict:
    """Majority membership over the LIVE judges: id -> True when at least ``need`` say ``yes``."""
    return {r["id"]: sum(1 for j in live if judgments_by_judge[j].get(r["id"]) == yes) >= need for r in sample}


def stratum_precision(sample: Sequence[Mapping], member: Mapping[str, bool], *, stratum_key: str = "stratum") -> dict:
    """Precision per stratum over the non-control rows, with a Wilson interval."""
    from sealeval.measure.stats import wilson_ci
    out = {}
    strata = sorted({r.get(stratum_key) for r in sample if r.get("role") == ROLE_SAMPLE}, key=str)
    for s in strata:
        rows = [r for r in sample if r.get(stratum_key) == s and r.get("role") == ROLE_SAMPLE]
        k = sum(1 for r in rows if member.get(r["id"]))
        out[s] = {"judged": len(rows), "members": k, "precision": round(k / len(rows), 4) if rows else None,
                  "ci95": wilson_ci(k, len(rows))}
    return out


def packet_hygiene(packets: Sequence[Mapping], *, allowed_keys: Sequence[str], leak_tokens: Iterable[str] = ()) -> list:
    """Violations in what the judges will see: a key outside ``allowed_keys`` (arm, stratum, role,
    provenance ...) or any ``leak_tokens`` substring anywhere in the packet. Empty list = clean.

    Run this on the exact packets, after rendering, before the first judge call.
    """
    allowed = tuple(sorted(allowed_keys))
    bad = []
    toks = [t for t in leak_tokens if t]
    for p in packets:
        keys = tuple(sorted(p.keys()))
        if keys != allowed:
            bad.append("%s: keys %s (allowed %s)" % (p.get("id"), list(keys), list(allowed)))
        blob = json.dumps(p, ensure_ascii=True)
        for t in toks:
            if t in blob:
                bad.append("%s: token %r" % (p.get("id"), t))
    return bad


def report_lines(panel: Mapping) -> list:
    L = ["controls: live judges %s, void %s%s" % (
        ", ".join(panel.get("live", [])) or "-", ", ".join(panel.get("void_judges", [])) or "-",
        " -> RUN VOID (%s)" % panel["reason"] if panel.get("run_void") else "")]
    for j, r in sorted(panel.get("per_judge", {}).items()):
        L.append("  judge %s: controls wrong %d/%d, unparsed %.2f%s" % (
            j, r["controls_wrong"], r["controls"], r["unparsed_share"], " VOID: " + "; ".join(r["reasons"]) if r["void"] else ""))
    return L
