#!/usr/bin/env python3
"""Measurement math for blind-panel evaluation: bootstrap CI, chance-corrected
inter-judge agreement (Cohen / Fleiss kappa), and the controls VOID-gate.

Zero dependencies (stdlib only) so it ships with sealeval's zero-dep contract and
can be OSS-released alongside the primitives. The LLM collection that produces the
per-judge labels lives OUTSIDE this module (bring your own backend) -- everything
here operates on already-collected labels, which is what makes it deterministic and
testable without any model call.

Why this exists (raised measurement standard, 2026-07-20): our feature/MVP blind
tests reported point estimates ("8/8 vs blind consensus") with (a) no controls to
prove the panel could even discriminate, (b) same-family judges only, and (c) no
confidence interval or chance-corrected agreement. A run that cannot rank an obvious
control is not evidence; an "8/8" at N=8 has a wide CI; raw %-agreement rewards
chance. This module supplies the three missing pieces so a verdict survives a
skeptical reviewer. ASCII-only output.
"""
from __future__ import annotations

import math
import random
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# Bootstrap confidence interval on a rate (accuracy / win-rate)
# ---------------------------------------------------------------------------


def bootstrap_ci(
    outcomes: Sequence[float],
    *,
    iters: int = 5000,
    seed: int = 7,
    alpha: float = 0.05,
) -> Optional[tuple[float, float]]:
    """Percentile bootstrap CI on the MEAN of ``outcomes`` (each in [0,1]).

    For accuracy pass 1.0 for a hit and 0.0 for a miss; for win-rate pass
    1.0/0.5/0.0 (win/tie/loss). Resamples items with replacement -- this is what
    turns "8/8" into an honest interval: at N=8 a perfect score still has a CI that
    reaches well below 0.9, so you cannot claim ">90% accurate" from 8 cases.

    Deterministic: seed-fixed RNG, no wall-clock. Returns None for empty input.
    """
    vals = [float(x) for x in outcomes]
    n = len(vals)
    if n == 0:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (round(lo, 4), round(hi, 4))


def wilson_ci(k: int, n: int, *, z: float = 1.96) -> Optional[tuple[float, float]]:
    """Wilson score 95% CI for a binomial proportion k/n.

    Use this (not bootstrap) for an ACCURACY / hit-rate: at the boundary (k == n) the
    bootstrap degenerates to [1.0, 1.0] -- i.e. it re-launders "8/8" as certainty --
    whereas Wilson correctly returns e.g. 8/8 -> ~[0.68, 1.0], the honest statement
    that a perfect score at N=8 still cannot claim ">90%". Returns None for n == 0.
    """
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


# ---------------------------------------------------------------------------
# Chance-corrected inter-judge agreement
# ---------------------------------------------------------------------------


def cohens_kappa(labels_a: Sequence, labels_b: Sequence) -> Optional[float]:
    """Cohen's kappa for two raters over paired categorical labels.

    kappa = (po - pe) / (1 - pe), where po = observed agreement and pe = agreement
    expected by chance from each rater's marginal label frequencies. Unlike raw
    %-agreement, kappa near 0 means "no better than chance" even when po looks high
    (two raters who both say F 90% of the time agree ~82% by luck alone).

    Returns None if the label lists differ in length or are empty. If neither rater
    varies (1 - pe == 0): 1.0 when they fully agree, else 0.0.
    """
    if len(labels_a) != len(labels_b) or not labels_a:
        return None
    n = len(labels_a)
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    cats = set(labels_a) | set(labels_b)
    pe = 0.0
    for c in cats:
        pa = sum(1 for a in labels_a if a == c) / n
        pb = sum(1 for b in labels_b if b == c) / n
        pe += pa * pb
    if abs(1.0 - pe) < 1e-12:
        return 1.0 if po >= 1.0 - 1e-12 else 0.0
    return round((po - pe) / (1 - pe), 4)


def mean_pairwise_kappa(judge_labels: dict[str, Sequence]) -> Optional[float]:
    """Mean Cohen's kappa over every judge pair -- the panel-level agreement number
    for 2..N judges (for exactly 2, equals cohens_kappa). Skips undefined pairs."""
    ids = sorted(judge_labels)
    ks = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            k = cohens_kappa(judge_labels[ids[i]], judge_labels[ids[j]])
            if k is not None:
                ks.append(k)
    return round(sum(ks) / len(ks), 4) if ks else None


def fleiss_kappa(judge_labels: dict[str, Sequence], categories: Optional[Sequence] = None) -> Optional[float]:
    """Fleiss' kappa: chance-corrected agreement for a FIXED panel of >=2 raters that
    all rate the same items. More standard than averaged-pairwise for >2 raters.

    ``judge_labels`` maps judge_id -> per-item label list (all equal length). Returns
    None if fewer than 2 judges, ragged lists, or no items.
    """
    ids = sorted(judge_labels)
    if len(ids) < 2:
        return None
    seqs = [list(judge_labels[i]) for i in ids]
    N = len(seqs[0])
    if N == 0 or any(len(s) != N for s in seqs):
        return None
    n = len(seqs)  # raters per item (fixed)
    cats = list(categories) if categories is not None else sorted({x for s in seqs for x in s})
    # n_ij: item i, category j -> count of raters choosing j
    P_is = []
    col_totals = {c: 0 for c in cats}
    for i in range(N):
        counts = {c: 0 for c in cats}
        for s in seqs:
            counts[s[i]] += 1
        for c in cats:
            col_totals[c] += counts[c]
        sq = sum(counts[c] * counts[c] for c in cats)
        P_is.append((sq - n) / (n * (n - 1)) if n > 1 else 1.0)
    P_bar = sum(P_is) / N
    p_j = {c: col_totals[c] / (N * n) for c in cats}
    P_e = sum(v * v for v in p_j.values())
    if abs(1.0 - P_e) < 1e-12:
        return 1.0 if P_bar >= 1.0 - 1e-12 else 0.0
    return round((P_bar - P_e) / (1 - P_e), 4)


def kappa_strength(k: Optional[float]) -> str:
    """Landis & Koch bands, for reporting 'low agreement = weak conclusion'."""
    if k is None:
        return "undefined"
    if k < 0.0:
        return "worse-than-chance"
    if k < 0.20:
        return "slight"
    if k < 0.40:
        return "fair"
    if k < 0.60:
        return "moderate"
    if k < 0.80:
        return "substantial"
    return "almost-perfect"


# ---------------------------------------------------------------------------
# Controls VOID-gate (measure the measurer)
# ---------------------------------------------------------------------------


def controls_gate(predicted: dict, truth: dict) -> dict:
    """Measure-the-measurer gate. ``predicted`` maps control_id -> the panel's label;
    ``truth`` maps control_id -> the obvious correct label. If the panel cannot get an
    OBVIOUS control right, it lacks discriminative power and the whole run is VOID --
    fix the harness before interpreting anything (SPEC BCCA sec.7/sec.12).

    Returns {void, n, correct, accuracy, misranked:[ids]}.
    """
    ids = [cid for cid in truth if cid in predicted]
    misranked = [cid for cid in ids if predicted[cid] != truth[cid]]
    n = len(ids)
    correct = n - len(misranked)
    return {
        "void": (n == 0) or bool(misranked),
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else None,
        "misranked": misranked,
    }


# ---------------------------------------------------------------------------
# Interpretation bands for a rate CI
# ---------------------------------------------------------------------------


def interpret_ci(ci: Optional[tuple[float, float]], *, strong: float = 0.9, floor: float = 0.6) -> str:
    """Pre-registered reading of an accuracy/agreement CI (tool vs independent panel).

    strong: CI entirely above -> "strong agreement with independent panel".
    floor : CI entirely above -> "agrees with panel above chance".
    CI straddling 0.5 -> "indistinguishable from chance".
    """
    if ci is None:
        return "no-data"
    lo, hi = ci
    if lo >= strong:
        return "strong agreement with independent panel"
    if lo >= floor:
        return "agrees with panel above chance (CI below 'strong')"
    if lo <= 0.5 <= hi:
        return "indistinguishable from chance (wide CI / small N)"
    return "inconclusive band"


# ---------------------------------------------------------------------------
# Metric-discrimination gate (controls for the MEASUREMENT AXIS, not the judge)
# ---------------------------------------------------------------------------


def metric_discrimination_gate(
    baseline_rate: float, *, min_detectable_effect: float = 0.10
) -> dict:
    """Is the chosen metric able to REVEAL an improvement over ``baseline_rate``?

    Controls prove the JUDGE discriminates; this proves the METRIC does. If the baseline is
    already near the ceiling, there is no headroom for a lift to show -- a NULL is then
    CEILING-LIMITED (uninformative), not evidence of no effect. Learned the hard way: a
    meta-skill A/B sealed module-recall as primary; baseline sat at 0.90 -> headroom 0.10 ->
    the metric could not reveal a lift, so the NULL said nothing. The fix is a FINER metric
    (file-level baseline was 0.30 -> ample headroom), decided BEFORE sealing.

    verdict:
      - CEILING_LIMITED : headroom < min_detectable_effect -> pick a finer metric before sealing.
      - FLOOR_ROOM      : baseline <= min_detectable_effect -> baseline fails; ample headroom
                          (this is exactly where skills tend to lift -- a good primary).
      - OK              : enough headroom to detect a min_detectable_effect improvement.
    """
    headroom = round(1.0 - baseline_rate, 4)
    if headroom < min_detectable_effect:
        verdict, note = "CEILING_LIMITED", (
            "baseline near ceiling -> a lift cannot show; choose a finer metric BEFORE sealing")
    elif baseline_rate <= min_detectable_effect:
        verdict, note = "FLOOR_ROOM", (
            "baseline fails -> ample headroom; where skills typically lift (good primary)")
    else:
        verdict, note = "OK", "enough headroom to detect a min_detectable_effect improvement"
    return {
        "baseline_rate": round(baseline_rate, 4),
        "headroom": headroom,
        "min_detectable_effect": min_detectable_effect,
        "ceiling_limited": verdict == "CEILING_LIMITED",
        "verdict": verdict,
        "note": note,
    }
