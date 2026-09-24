"""sealeval.audit -- the five questions a skeptic asks before your number counts, as code.

Every module here was extracted from a pre-registered measurement that FAILED honestly, and
encodes the specific way it would otherwise have looked like a success:

    leakage    did the judge already know the answer? (probe BEFORE pairing; share over the labelled class)
    controls   can the panel tell an obvious case apart? (hidden controls with opaque ids; per-judge VOID)
    pairs      does the panel invent differences? (seeded side flip; NULL pairs must straddle 0.5)
    recapture  how much of the class did you find, and at what precision? (Chapman; recall x precision / N*)
    temporal   does an out-of-time model earn its AUC? (split-defining guard; events per feature;
               permutation null; bootstrap; calibration; Brier vs base rate with a class-weight warning)
    review     what does the sealed result decide? (a bounded reader that cannot invent a number)

Zero dependencies; every function operates on labels, picks, scores or text you already have.
You bring the model or judge call, as everywhere in sealeval.
"""
from sealeval.audit import controls, leakage, pairs, recapture, review, temporal
from sealeval.audit.controls import draw_sample, judge_void, packet_hygiene, panel_void
from sealeval.audit.leakage import exact_title_hit, leak_report, leakage_gate, parse_probe, probe_prompt
from sealeval.audit.pairs import matched_pairs, null_control, null_pairs, score_pairs
from sealeval.audit.recapture import all_pairs_recapture, arm_recall, chapman, precision_adjusted_recall
from sealeval.audit.review import Bundle, build_bundle, parse_review, render_prompt, validate
from sealeval.audit.review import review as run_review
from sealeval.audit.temporal import (
    auc,
    bootstrap_auc_ci,
    calibration_bins,
    class_weight_drift,
    events_per_feature,
    permutation_p,
    split_defining_features,
    temporal_gate,
)

__all__ = [
    "leakage", "controls", "pairs", "recapture", "temporal", "review",
    "probe_prompt", "parse_probe", "exact_title_hit", "leak_report", "leakage_gate",
    "draw_sample", "judge_void", "panel_void", "packet_hygiene",
    "matched_pairs", "null_pairs", "score_pairs", "null_control",
    "chapman", "all_pairs_recapture", "arm_recall", "precision_adjusted_recall",
    "auc", "events_per_feature", "split_defining_features", "permutation_p", "bootstrap_auc_ci",
    "calibration_bins", "class_weight_drift", "temporal_gate",
    "Bundle", "build_bundle", "render_prompt", "parse_review", "validate", "run_review",
]
