"""The temporal-split gate: what a survival / out-of-time model must show before its AUC counts.

Why this exists. A model evaluated on a later period than it was trained on is the honest
version of a backtest -- and it fails in two quiet ways that a single AUC hides. In an internal
run (2026-09, pre-registered, sealed NOT_SHOWN) the post-mortem named both:

1. A feature that DEFINES the split (``year`` when train = 2014-16 and test = 2017-18) has
   disjoint supports on the two sides. Its coefficient is pure extrapolation and its
   "importance" is an artefact. Such features must be refused before fitting.
2. Balanced class weights (or any rebalancing) push predicted probabilities away from observed
   rates, so "Brier below the base rate" fails by construction, not because the model is bad.
   Compare Brier only after recalibration on a held-out fold, or fit unweighted.

Plus the checks that were right the first time: an events-per-feature rule decided BEFORE
fitting (shrink the feature list to what the events can support), a permutation null (refit on
shuffled TRAIN labels, score the same test set), a bootstrap interval for the test AUC,
calibration bins, and the standardised mean difference between train and test for every
feature (covariate shift is reported, not hidden).

You bring the model. This module takes labels and scores (and, for the permutation p, the
AUCs of your refits) and applies pre-registered thresholds. Zero dependencies.
"""
from __future__ import annotations

import math
import random
from typing import Mapping, Optional, Sequence

DEFAULT_THRESHOLDS = {"auc": 0.70, "ci_lo": 0.60, "p": 0.01, "min_events": 30}


def auc(y: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    """Rank AUC (Mann-Whitney with average ranks for ties). None unless both classes are present."""
    n = len(y)
    if n != len(scores):
        raise ValueError("y and scores differ in length")
    pos = sum(1 for v in y if v)
    neg = n - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(n), key=lambda i: scores[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(ranks[i] for i in range(n) if y[i])
    return round((sum_pos - pos * (pos + 1) / 2.0) / (pos * neg), 4)


def events_per_feature(n_events: int, n_features: int, *, min_epf: int = 10) -> dict:
    """Decide BEFORE fitting how many features the events can support.

    With fewer than ``min_epf`` events per feature the fit is noise; the pre-registered remedy is
    to shrink to the ``max_features`` strongest univariate features on the TRAIN side only.
    """
    max_f = int(n_events // min_epf)
    return {"ok": n_events >= min_epf * n_features, "n_events": n_events, "n_features": n_features,
            "min_epf": min_epf, "max_features": max_f,
            "note": None if n_events >= min_epf * n_features else
            "%d events support at most %d features at %d events per feature; shrink on TRAIN before fitting" % (n_events, max_f, min_epf)}


def smd(train_vals: Sequence[float], test_vals: Sequence[float]) -> Optional[float]:
    """Standardised mean difference (test - train) / pooled sd. None when both sides are constant."""
    a = [float(v) for v in train_vals]
    b = [float(v) for v in test_vals]
    if not a or not b:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / len(a)
    vb = sum((v - mb) ** 2 for v in b) / len(b)
    sd = math.sqrt((va + vb) / 2.0)
    if sd == 0:
        return 0.0 if ma == mb else None
    return round((mb - ma) / sd, 3)


def split_defining_features(train_rows: Sequence[Mapping], test_rows: Sequence[Mapping], features: Sequence[str]) -> dict:
    """Refuse features whose train and test supports are DISJOINT (the split-defining guard), and
    report the standardised mean difference of every feature.

    A disjoint support means the model never saw a test value of that feature; whatever it
    learned about it is extrapolation. ``year`` under a temporal split is the canonical case.
    """
    refused, shift = [], {}
    for f in features:
        tr = [r[f] for r in train_rows if f in r and r[f] is not None]
        te = [r[f] for r in test_rows if f in r and r[f] is not None]
        if tr and te and not (set(tr) & set(te)):
            refused.append(f)
        try:
            shift[f] = smd(tr, te)
        except (TypeError, ValueError):
            shift[f] = None
    return {"refused": refused, "smd": shift}


def permutation_p(observed: Optional[float], null_values: Sequence[float]) -> Optional[float]:
    """One-sided permutation p with the +1 correction: (#null >= observed + 1) / (n + 1).

    ``null_values`` are the test AUCs of models refit on SHUFFLED TRAIN labels (you run the
    refits; this only counts). The +1 keeps p > 0 at any n and is the pre-registered form.
    """
    if observed is None or not null_values:
        return None
    ge = sum(1 for v in null_values if v >= observed)
    return round((ge + 1) / (len(null_values) + 1), 4)


def bootstrap_auc_ci(y: Sequence[int], scores: Sequence[float], *, iters: int = 1000, seed: int = 7,
                     alpha: float = 0.05) -> Optional[tuple]:
    """Percentile bootstrap of the test AUC (resample cases with replacement). Deterministic."""
    n = len(y)
    if n == 0:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        a = auc([y[i] for i in idx], [scores[i] for i in idx])
        if a is not None:
            vals.append(a)
    if not vals:
        return None
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return (round(lo, 4), round(hi, 4))


def calibration_bins(p: Sequence[float], y: Sequence[int], *, bins: int = 5) -> list:
    """Quantile bins of the predicted probability: n, mean predicted, observed event rate."""
    n = len(p)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p[i])
    k = max(1, min(bins, n))
    base, extra = divmod(n, k)
    out, start = [], 0
    for b in range(k):
        size = base + (1 if b < extra else 0)
        chunk = order[start:start + size]
        start += size
        if chunk:
            out.append({"n": len(chunk), "mean_p": round(sum(p[i] for i in chunk) / len(chunk), 3),
                        "observed": round(sum(1 for i in chunk if y[i]) / len(chunk), 3)})
    return out


def brier(p: Sequence[float], y: Sequence[int]) -> Optional[float]:
    if not p:
        return None
    return round(sum((float(pi) - (1.0 if yi else 0.0)) ** 2 for pi, yi in zip(p, y)) / len(p), 4)


def base_rate_brier(base_rate: float, y: Sequence[int]) -> Optional[float]:
    """Brier of the constant forecast ``base_rate`` (take it from TRAIN, never from test)."""
    if not y:
        return None
    return round(sum((float(base_rate) - (1.0 if yi else 0.0)) ** 2 for yi in y) / len(y), 4)


def class_weight_drift(bins: Sequence[Mapping], *, warn_above: float = 0.15) -> dict:
    """Weighted mean |mean_p - observed| over calibration bins. Above ``warn_above`` the predicted
    probabilities are far from observed rates -- the signature of a class-weighted / rebalanced
    fit -- and a Brier-vs-base-rate comparison is not meaningful until recalibration."""
    n = sum(b["n"] for b in bins)
    if not n:
        return {"drift": None, "warn": None, "note": "no bins"}
    d = sum(b["n"] * abs(b["mean_p"] - b["observed"]) for b in bins) / n
    return {"drift": round(d, 4), "warn": d > warn_above,
            "note": ("predicted probabilities drift %.2f from observed rates: recalibrate on a held-out fold "
                     "(or fit unweighted) before comparing Brier with the base rate" % d) if d > warn_above else None}


def temporal_gate(*, auc_test: Optional[float], ci_lo: Optional[float], p_perm: Optional[float],
                  brier_test: Optional[float], brier_base: Optional[float], n_events_test: int,
                  refused_features: Sequence[str] = (), thresholds: Optional[Mapping] = None) -> dict:
    """Apply pre-registered thresholds. Every input that is None fails its clause (unverified is
    not passed). ``refused_features`` non-empty fails: the fit used a split-defining feature."""
    t = dict(DEFAULT_THRESHOLDS)
    t.update(thresholds or {})
    failed = []
    if auc_test is None or auc_test < t["auc"]:
        failed.append("test AUC %s below %.2f" % (_f(auc_test), t["auc"]))
    if ci_lo is None or ci_lo <= t["ci_lo"]:
        failed.append("bootstrap lower bound %s not above %.2f" % (_f(ci_lo), t["ci_lo"]))
    if p_perm is None or p_perm >= t["p"]:
        failed.append("permutation p %s not below %.3f" % (_f(p_perm), t["p"]))
    if brier_test is None or brier_base is None or brier_test >= brier_base:
        failed.append("Brier %s not below the base-rate Brier %s" % (_f(brier_test), _f(brier_base)))
    if n_events_test < t["min_events"]:
        failed.append("%d events in test below %d" % (n_events_test, t["min_events"]))
    if refused_features:
        failed.append("split-defining features used: %s" % ", ".join(refused_features))
    return {"verdict": "WORTH_USING" if not failed else "NOT_SHOWN", "failed": failed, "thresholds": t}


def report_lines(gate: Mapping, *, drift: Optional[Mapping] = None, shift: Optional[Mapping] = None) -> list:
    L = ["temporal gate: %s" % gate["verdict"]]
    for f in gate.get("failed", []):
        L.append("  FAIL: %s" % f)
    if drift and drift.get("warn"):
        L.append("  WARN: %s" % drift["note"])
    if shift:
        big = {f: v for f, v in shift.items() if v is not None and abs(v) >= 0.5}
        if big:
            L.append("  covariate shift |SMD| >= 0.5: " + ", ".join("%s=%.2f" % (f, v) for f, v in sorted(big.items())))
    return L


def _f(x) -> str:
    return "n/a" if x is None else ("%.4f" % x if isinstance(x, float) else str(x))
