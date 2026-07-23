"""Round 0 -- the throwaway pilot that validates the APPARATUS before you seal anything.

Why this exists (learned the expensive way, 2026-07-21..23): four separate runs were spoiled by
defects of the instrument, not of the hypothesis --
  * a sealed primary metric that sat at the ceiling (baseline 0.90) -> the NULL said nothing;
  * 2 of 18 arms returned unparseable output -> silently dropped checkpoints;
  * a bare-substring stale-number match -> a false BLOCK on a real, sourced claim;
  * a positive control that was not actually a bug -> the whole run VOIDed.
Every one would have been caught by a cheap pilot BEFORE the sealed run.

THE HARD RULE
-------------
**Hypothesis results produced during Round 0 are INADMISSIBLE.** Round 0 exists to find broken
apparatus; its effect estimates are burned -- never cite them, never decide on them. That is the
price of the first pancake. This function enforces the separation STRUCTURALLY: it accepts only
apparatus diagnostics (controls, baseline rate, parse counts, leak control). It has no parameter
for a lift/effect, so a Round 0 report cannot carry one.

REPAIRS ALLOWED BETWEEN ROUND 0 AND ROUND 1
-------------------------------------------
Only repairs that pass the OUTCOME-BLIND test: *would I accept this fix if it worked against me?*
"17 must not match 117" is true regardless of who it helps -> allowed. "Use the finer metric
because the coarse one gave NULL" is justified only by the result -> NOT allowed (that is
RIGOR-INVERSION). Record each repair BEFORE unblinding the hypothesis effect.

ASCII-only.
"""
from __future__ import annotations

from typing import Optional

from sealeval.measure import stats


def round0_gate(
    *,
    control_labels: Optional[dict] = None,
    control_truth: Optional[dict] = None,
    baseline_rate: Optional[float] = None,
    attempted: Optional[int] = None,
    parsed: Optional[int] = None,
    leak_controlled: Optional[bool] = None,
    min_parse_rate: float = 0.90,
    min_detectable_effect: float = 0.10,
    strict: bool = True,
) -> dict:
    """Validate the apparatus. Returns ``ready_to_seal`` plus every blocking reason.

    Deliberately accepts NO hypothesis/effect argument -- Round 0 cannot report one.

    Checks (each skipped, with a warning, if its inputs are absent):
      1. controls      -- can the judge/panel get an OBVIOUS case right? (reuses controls_gate)
      2. metric        -- does the baseline leave headroom on this metric? (metric_discrimination_gate)
      3. parse-rate    -- did enough runs return usable output?
      4. leak control  -- was leakage explicitly controlled for? (declarative; False blocks)
    """
    checks: dict = {}
    blocking: list[str] = []
    warnings: list[str] = []

    # 1. controls -- does the judge discriminate?
    if control_labels and control_truth:
        c = stats.controls_gate(control_labels, control_truth)
        checks["controls"] = c
        if c["void"]:
            blocking.append(
                "CONTROLS_VOID: judge missed an obvious control %s -- fix the harness (or the "
                "control itself) before sealing" % (c["misranked"] or "(none supplied)"))
    else:
        warnings.append("controls not supplied -- judge discrimination UNVERIFIED")

    # 2. metric -- does the measurement axis have room to show an effect?
    if baseline_rate is not None:
        m = stats.metric_discrimination_gate(
            baseline_rate, min_detectable_effect=min_detectable_effect)
        checks["metric"] = m
        if m["ceiling_limited"]:
            blocking.append(
                "METRIC_CEILING_LIMITED: baseline %.3f leaves headroom %.3f (< %.2f) -- a NULL on "
                "this metric would be uninformative; choose a finer metric BEFORE sealing"
                % (m["baseline_rate"], m["headroom"], min_detectable_effect))
    else:
        warnings.append("baseline_rate not supplied -- metric headroom UNVERIFIED")

    # 3. parse / completion rate -- are runs actually usable?
    if attempted:
        rate = round((parsed or 0) / attempted, 4)
        checks["parse"] = {"attempted": attempted, "parsed": parsed or 0,
                           "rate": rate, "min_required": min_parse_rate}
        if rate < min_parse_rate:
            blocking.append(
                "PARSE_RATE_LOW: %d/%d = %.2f usable (< %.2f) -- dropped runs bias the sample; fix "
                "output contract or retry policy before sealing" % (parsed or 0, attempted, rate,
                                                                    min_parse_rate))
    else:
        warnings.append("parse counts not supplied -- usable-output rate UNVERIFIED")

    # 4. leakage -- declarative, but an explicit False is disqualifying
    checks["leak_controlled"] = leak_controlled
    if leak_controlled is False:
        blocking.append("LEAKAGE_UNCONTROLLED: the arm can see the answer/future -- any effect is "
                        "confounded; isolate before sealing")
    elif leak_controlled is None:
        warnings.append("leak control not declared -- state how leakage is prevented")

    # strict (DEFAULT): absence of evidence is NOT evidence of a sound apparatus. An UNVERIFIED
    # check must block -- otherwise a Round 0 can be "passed" by supplying nothing, and the seal
    # then attests to a validation that never happened. Shipped as opt-in first; an independent
    # reviewer pointed out that for a library whose thesis is exactly this, the default was
    # backwards. Pass strict=False only for an exploratory look you will NOT seal.
    if strict and warnings:
        blocking.extend("UNVERIFIED_" + w.split(" ")[0].upper() + ": " + w for w in warnings)

    ready = not blocking
    return {
        "round": 0,
        "strict": strict,
        "ready_to_seal": ready,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
        "admissibility": ("Round-0 hypothesis results are INADMISSIBLE -- diagnostics only. "
                          "Repairs must pass the outcome-blind test and be recorded before "
                          "unblinding the effect."),
        "verdict": "READY_TO_SEAL" if ready else "REPAIR_THEN_RERUN",
    }


def round0_receipt(gate_result: dict) -> dict:
    """Compact, embeddable evidence that Round 0 ran -- put this inside the prereg payload so the
    seal itself records that the apparatus was validated first."""
    return {
        "round0_verdict": gate_result.get("verdict"),
        "round0_ready": bool(gate_result.get("ready_to_seal")),
        "round0_blocking": list(gate_result.get("blocking", [])),
        "round0_checks_present": sorted(gate_result.get("checks", {}).keys()),
    }
