"""Capture-recapture for "how much of the class did we find?", and recall that pays for its
precision.

Why this exists. When two independent finders (a search arm and a manual arm, two tools, two
analysts) each produce a list of members of some class, the overlap tells you how big the
class probably is. The Chapman estimator turns (n1, n2, m) into N_hat. In an internal run
(2026-09) a 45-item list and an 11-item list overlapped on 5: N_hat = 91, so the union of the two
arms covered about half the class -- a number no single arm could have told you.

The second half is the lesson of the follow-up audit: a recall computed against a truth list
that one arm wrote itself rewards loose matching. Recall must be adjusted by the precision of
each stratum of the union (audited blind), and the class size becomes the precision-weighted
N* rather than the raw union count. Recall_adj = found * precision / N*.

Zero dependencies.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from sealeval.measure.stats import wilson_ci


def chapman(n1: int, n2: int, m: int) -> Optional[float]:
    """Chapman's bias-corrected Lincoln-Petersen estimate of the class size from two captures
    of sizes ``n1`` and ``n2`` with ``m`` items in both. None when either capture is empty."""
    if n1 <= 0 or n2 <= 0 or m < 0:
        return None
    return round((n1 + 1) * (n2 + 1) / (m + 1) - 1, 1)


def all_pairs_recapture(found_by_arm: Mapping[str, Iterable]) -> dict:
    """Chapman for every pair of arms. ``found_by_arm``: arm -> iterable of member keys.
    Reported, not gated: the spread across pairs is itself a sanity check on independence."""
    sets = {a: set(v) for a, v in found_by_arm.items()}
    names = sorted(sets)
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            m = len(sets[a] & sets[b])
            out["%s_x_%s" % (a, b)] = {"n1": len(sets[a]), "n2": len(sets[b]), "m": m, "N_hat": chapman(len(sets[a]), len(sets[b]), m)}
    return out


def recall(found: int, n_truth: int) -> dict:
    """Raw recall against a sealed truth list, with a Wilson interval."""
    p = round(found / n_truth, 4) if n_truth else None
    return {"found": found, "n": n_truth, "recall": p, "ci95": wilson_ci(found, n_truth)}


def class_size(strata_counts: Mapping[str, int], strata_precision: Mapping[str, float]) -> float:
    """N* = sum over strata of (items in the stratum) x (audited precision of that stratum)."""
    return round(sum(float(strata_counts[s]) * float(strata_precision.get(s, 0.0)) for s in strata_counts), 1)


def precision_adjusted_recall(found: int, precision: float, n_star: float) -> Optional[float]:
    """found x precision / N*: the share of the (precision-weighted) class this arm truly found."""
    if not n_star:
        return None
    return round(found * float(precision) / float(n_star), 4)


def arm_recall(arm_composition: Mapping[str, Mapping[str, int]], strata_precision: Mapping[str, float],
               strata_counts: Mapping[str, int]) -> dict:
    """Per arm: how many it found, its precision (weighted over the strata its finds fall in) and
    its precision-adjusted recall against N*.

    ``arm_composition``  arm -> {stratum: number of the arm's finds in that stratum}
    ``strata_precision`` stratum -> audited precision (from a blind panel with hidden controls)
    ``strata_counts``    stratum -> total items in the union
    """
    n_star = class_size(strata_counts, strata_precision)
    out = {"N_star": n_star, "arms": {}}
    for arm, comp in arm_composition.items():
        tot = sum(comp.values())
        prec = (sum(comp[s] * float(strata_precision.get(s, 0.0)) for s in comp) / tot) if tot else 0.0
        out["arms"][arm] = {"found": tot, "precision": round(prec, 4), "recall_adj": precision_adjusted_recall(tot, prec, n_star)}
    return out


def report_lines(pairs: Mapping, arms: Optional[Mapping] = None) -> list:
    L = ["capture-recapture (Chapman):"]
    for k, v in sorted(pairs.items()):
        L.append("  %s: n1=%d n2=%d m=%d -> N_hat=%s" % (k, v["n1"], v["n2"], v["m"], v["N_hat"]))
    if arms:
        L.append("precision-adjusted recall (N*=%s):" % arms["N_star"])
        for a, r in sorted(arms["arms"].items()):
            L.append("  %s: found %d, precision %.3f, recall_adj %s" % (a, r["found"], r["precision"], r["recall_adj"]))
    return L
