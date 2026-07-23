"""Raised measurement standard for blind-panel evaluation.

Adds the three BCCA-derived disciplines our earlier feature/MVP blind tests lacked:
controls (VOID-gate), cross-family judges (reported/flagged), and confidence
intervals + chance-corrected inter-judge agreement (bootstrap CI + Cohen/Fleiss
kappa) -- so a verdict is an interval with a calibration proof, not a point estimate.

The math (``stats``) and the aggregator (``panel``) are zero-dependency and operate on
already-collected labels; you bring the model backend that produces the labels.
"""
from sealeval.measure import panel, stats
from sealeval.measure.panel import consensus, consensus_labels, evaluate, report_lines
from sealeval.measure.stats import (
    bootstrap_ci,
    cohens_kappa,
    controls_gate,
    fleiss_kappa,
    interpret_ci,
    kappa_strength,
    mean_pairwise_kappa,
    metric_discrimination_gate,
    wilson_ci,
)
from sealeval.measure.pilot import round0_gate, round0_receipt
from sealeval.measure.confirm import match_findings

__all__ = [
    "stats", "panel", "pilot", "confirm", "round0_gate", "round0_receipt", "match_findings",
    "bootstrap_ci", "wilson_ci", "cohens_kappa", "mean_pairwise_kappa", "fleiss_kappa",
    "kappa_strength", "controls_gate", "interpret_ci", "metric_discrimination_gate",
    "evaluate", "consensus", "consensus_labels", "report_lines",
]
