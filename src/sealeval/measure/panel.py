#!/usr/bin/env python3
"""Sync aggregator that turns collected blind-panel labels into a calibrated verdict.

Backend-agnostic: you collect per-judge labels however you like (any CLI / API /
family) and hand them in as plain dicts. This module applies the raised measurement
standard in order:

  1. CONTROLS gate first -- if the panel misranks an obvious control the run is VOID
     and NOTHING downstream is interpreted (a panel that can't discriminate is not a
     measuring instrument).
  2. INTER-JUDGE kappa -- chance-corrected agreement; low agreement is reported as a
     weak-conclusion flag, not hidden.
  3. Panel consensus (majority; ties = NO_CONSENSUS and are excluded from the rate).
  4. tool-vs-panel accuracy with a BOOTSTRAP CI -- the honest replacement for "8/8".
  5. CROSS-FAMILY flag -- whether the panel spans >=2 model families (same-family
     panels share taste bias; flagged, not silently accepted).

Zero deps. ASCII-only. All numbers deterministic given the same labels + seed.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from sealeval.measure import stats

NO_CONSENSUS = "NO_CONSENSUS"


def consensus(per_judge_labels: dict[str, Sequence], index: int) -> str:
    """Majority label across judges for one case index. Ties -> NO_CONSENSUS."""
    votes = [labs[index] for labs in per_judge_labels.values()]
    c = Counter(votes)
    top = c.most_common()
    if len(top) >= 2 and top[0][1] == top[1][1]:
        return NO_CONSENSUS
    return top[0][0]


def consensus_labels(per_judge_labels: dict[str, Sequence], n: int) -> list:
    return [consensus(per_judge_labels, i) for i in range(n)]


def evaluate(
    *,
    n_cases: int,
    judge_labels: dict[str, Sequence],
    families: dict[str, str],
    tool_labels: Optional[Sequence] = None,
    ground_truth: Optional[Sequence] = None,
    control_judge_labels: Optional[dict[str, Sequence]] = None,
    control_truth: Optional[dict[str, str]] = None,
    seed: int = 7,
) -> dict:
    """Return a full calibrated report dict. See module docstring for the ordering.

    ``judge_labels``   : judge_id -> list of labels, one per case (len == n_cases).
    ``families``       : judge_id -> model family (e.g. 'anthropic', 'openai').
    ``tool_labels``    : the system-under-test's label per case (optional).
    ``ground_truth``   : pre-registered label per case (optional; panel-vs-GT check).
    ``control_*``      : obvious sanity cases + their correct labels (the VOID gate).
    """
    judges = sorted(judge_labels)
    fam_set = sorted({families.get(j, "?") for j in judges})
    report: dict = {
        "n_cases": n_cases,
        "judges": judges,
        "families": fam_set,
        "cross_family": len(fam_set) >= 2,
        "void": False,
        "controls": None,
        "inter_judge_kappa_fleiss": None,
        "inter_judge_kappa_pairwise": None,
        "kappa_strength": None,
        "panel_vs_ground_truth": None,
        "tool_vs_panel": None,
        "no_consensus_cases": None,
        "interpretation": None,
    }

    # 1. CONTROLS gate -----------------------------------------------------
    if control_judge_labels and control_truth:
        cids = list(control_truth)
        cons = {cid: consensus(control_judge_labels, i) for i, cid in enumerate(cids)}
        gate = stats.controls_gate(cons, control_truth)
        gate["panel_consensus"] = cons
        gate["per_judge"] = {
            j: {cids[i]: control_judge_labels[j][i] for i in range(len(cids))}
            for j in control_judge_labels
        }
        report["controls"] = gate
        if gate["void"]:
            report["void"] = True
            report["interpretation"] = (
                "VOID: panel misranked an obvious control %s -> instrument cannot "
                "discriminate; fix harness before interpreting anything." % gate["misranked"]
            )
            return report
    else:
        report["controls"] = {"void": None, "note": "no controls supplied (standard requires them)"}

    # 2. INTER-JUDGE agreement (chance-corrected) --------------------------
    kf = stats.fleiss_kappa(judge_labels)
    kp = stats.mean_pairwise_kappa(judge_labels)
    report["inter_judge_kappa_fleiss"] = kf
    report["inter_judge_kappa_pairwise"] = kp
    report["kappa_strength"] = stats.kappa_strength(kf if kf is not None else kp)

    # 3. consensus ---------------------------------------------------------
    cons = consensus_labels(judge_labels, n_cases)
    no_cons = [i for i, c in enumerate(cons) if c == NO_CONSENSUS]
    report["no_consensus_cases"] = no_cons

    # 4a. panel vs pre-registered ground truth -----------------------------
    if ground_truth is not None:
        scored = [i for i in range(n_cases) if cons[i] != NO_CONSENSUS]
        k = sum(1 for i in scored if cons[i] == ground_truth[i])
        report["panel_vs_ground_truth"] = {
            "n": len(scored),
            "accuracy": round(k / len(scored), 4) if scored else None,
            "ci95": stats.wilson_ci(k, len(scored)),
        }

    # 4b. tool vs panel consensus (the headline, with a CI) ----------------
    if tool_labels is not None:
        scored = [i for i in range(n_cases) if cons[i] != NO_CONSENSUS]
        k = sum(1 for i in scored if tool_labels[i] == cons[i])
        ci = stats.wilson_ci(k, len(scored))
        report["tool_vs_panel"] = {
            "n_scored": len(scored),
            "n_excluded_no_consensus": len(no_cons),
            "point_accuracy": round(k / len(scored), 4) if scored else None,
            "ci95": ci,
        }
        report["interpretation"] = stats.interpret_ci(ci)

    return report


def report_lines(r: dict) -> list[str]:
    """Human-readable ASCII summary for the CLI / verdict file."""
    L = []
    fam = ",".join(r["families"])
    L.append("panel: %d judges [%s]  cross_family=%s"
             % (len(r["judges"]), fam, r["cross_family"]))
    c = r.get("controls") or {}
    if c.get("void") is True:
        L.append("CONTROLS: VOID  misranked=%s" % c.get("misranked"))
        L.append(r.get("interpretation", ""))
        return L
    if c.get("n"):
        L.append("controls: %d/%d obvious cases correct -> %s"
                 % (c["correct"], c["n"], "PASS" if not c["void"] else "VOID"))
    elif c.get("note"):
        L.append("controls: %s" % c["note"])
    L.append("inter-judge kappa: fleiss=%s pairwise=%s (%s)"
             % (r["inter_judge_kappa_fleiss"], r["inter_judge_kappa_pairwise"], r["kappa_strength"]))
    if r.get("panel_vs_ground_truth"):
        g = r["panel_vs_ground_truth"]
        L.append("panel vs pre-registered GT: %s  CI95=%s  (n=%s)"
                 % (g["accuracy"], g["ci95"], g["n"]))
    if r.get("tool_vs_panel"):
        t = r["tool_vs_panel"]
        L.append("TOOL vs panel: point=%s  CI95=%s  (n=%s, excluded_no_consensus=%s)"
                 % (t["point_accuracy"], t["ci95"], t["n_scored"], t["n_excluded_no_consensus"]))
        L.append("interpretation: %s" % r.get("interpretation"))
    return L
