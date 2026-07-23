#!/usr/bin/env python3
"""Deterministic tests for the raised measurement standard (sealeval.measure).

Textbook-anchored: Cohen kappa == 0.40 on the classic 50-item table; Fleiss on a
hand-computable 2-item table == -1/3; Wilson at the boundary does NOT degenerate the
way bootstrap does. No LLM calls -- all inputs are fixed labels."""
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sealeval.measure import panel, stats  # noqa: E402


# --- Cohen's kappa ---------------------------------------------------------

def test_cohens_kappa_textbook_0_40():
    # Classic 2x2: both-yes=20, both-no=15, A yes/B no=5, A no/B yes=10 -> kappa 0.40
    a = ["y"] * 20 + ["n"] * 15 + ["y"] * 5 + ["n"] * 10
    b = ["y"] * 20 + ["n"] * 15 + ["n"] * 5 + ["y"] * 10
    assert stats.cohens_kappa(a, b) == 0.4


def test_cohens_kappa_perfect():
    a = ["F", "C", "F", "C", "F"]
    assert stats.cohens_kappa(a, list(a)) == 1.0


def test_cohens_kappa_constant_both_same():
    # no variance, full agreement -> defined as 1.0 (not undefined/0)
    assert stats.cohens_kappa(["F"] * 5, ["F"] * 5) == 1.0


def test_cohens_kappa_ragged_none():
    assert stats.cohens_kappa(["F", "C"], ["F"]) is None


# --- Fleiss' kappa ---------------------------------------------------------

def test_fleiss_perfect():
    labels = {"j1": ["F", "C", "F"], "j2": ["F", "C", "F"], "j3": ["F", "C", "F"]}
    assert stats.fleiss_kappa(labels) == 1.0


def test_fleiss_hand_computed_negative():
    # 2 items, 2 raters; item1 both F, item2 split -> kappa = -1/3 (hand-computed)
    labels = {"j1": ["F", "F"], "j2": ["F", "C"]}
    k = stats.fleiss_kappa(labels)
    assert abs(k - (-1.0 / 3.0)) < 1e-3


# --- Wilson vs bootstrap at the boundary -----------------------------------

def test_wilson_perfect_is_not_degenerate():
    lo, hi = stats.wilson_ci(8, 8)
    assert hi == 1.0
    assert 0.5 < lo < 0.9          # honest: 8/8 cannot claim >90%


def test_bootstrap_all_ones_degenerate():
    # documents WHY we use Wilson for accuracy: bootstrap of a perfect sample is [1,1]
    assert stats.bootstrap_ci([1.0] * 8) == (1.0, 1.0)


def test_bootstrap_mixed_bounds():
    ci = stats.bootstrap_ci([1.0, 0.0] * 4, seed=7)
    lo, hi = ci
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0


# --- controls gate ---------------------------------------------------------

def test_controls_gate_pass():
    g = stats.controls_gate({"pos": "F", "neg": "C"}, {"pos": "F", "neg": "C"})
    assert g["void"] is False and g["correct"] == 2


def test_controls_gate_void_on_misrank():
    g = stats.controls_gate({"pos": "C", "neg": "C"}, {"pos": "F", "neg": "C"})
    assert g["void"] is True and g["misranked"] == ["pos"]


def test_controls_gate_void_on_empty():
    assert stats.controls_gate({}, {})["void"] is True


# --- panel.evaluate end to end ---------------------------------------------

def _two_family():
    return {"claude": "anthropic", "gpt": "openai"}


def test_panel_void_short_circuits():
    r = panel.evaluate(
        n_cases=2,
        judge_labels={"claude": ["F", "C"], "gpt": ["F", "C"]},
        families=_two_family(),
        tool_labels=["F", "C"],
        control_judge_labels={"claude": ["C"], "gpt": ["C"]},  # both miss the positive
        control_truth={"pos_obvious": "F"},
    )
    assert r["void"] is True
    assert r["tool_vs_panel"] is None            # nothing downstream interpreted
    assert "VOID" in r["interpretation"]


def test_panel_end_to_end_cross_family():
    jl = {"claude": ["F", "F", "C", "C"], "gpt": ["F", "F", "C", "C"]}
    r = panel.evaluate(
        n_cases=4,
        judge_labels=jl,
        families=_two_family(),
        tool_labels=["F", "F", "C", "C"],         # tool matches panel on all 4
        ground_truth=["F", "F", "C", "C"],
        control_judge_labels={"claude": ["F", "C"], "gpt": ["F", "C"]},
        control_truth={"pos": "F", "neg": "C"},
    )
    assert r["void"] is False
    assert r["cross_family"] is True
    assert r["controls"]["correct"] == 2
    assert r["tool_vs_panel"]["point_accuracy"] == 1.0
    assert r["tool_vs_panel"]["ci95"][0] < 0.9    # Wilson, not degenerate at N=4
    assert r["inter_judge_kappa_fleiss"] == 1.0


def test_panel_same_family_flagged():
    jl = {"opus": ["F", "C"], "sonnet": ["F", "C"]}
    r = panel.evaluate(
        n_cases=2, judge_labels=jl,
        families={"opus": "anthropic", "sonnet": "anthropic"},
        tool_labels=["F", "C"],
    )
    assert r["cross_family"] is False             # our old panels were exactly this


def test_match_findings_confirmed_not_flagged():
    from sealeval.measure import match_findings
    key = [{"file": "a.py", "line": 10}, {"file": "b.py", "line": 4}]
    findings = [
        {"file": "a.py", "line": 11, "verdict": "GENUINE_BUG"},   # within tolerance -> TP
        {"file": "b.py", "line": 40, "verdict": "GENUINE_BUG"},   # far away -> FP
        {"file": "a.py", "line": 10, "verdict": "REFUTED"},       # refuted -> does not score
    ]
    r = match_findings(findings, key, line_tolerance=2)
    # fp counts only CONFIRMED findings that hit no injection; a REFUTED finding was never a
    # claim the system got credit for, so it is reported separately, not charged as a false alarm.
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 1)
    assert len(r["rejected_by_verdict"]) == 1
    assert r["recall"] == 0.5 and r["missed_injections"] == [{"file": "b.py", "line": 4}]


def test_match_findings_shotgun_scores_once():
    """Ten findings aimed at one bug must score one hit, not ten."""
    from sealeval.measure import match_findings
    key = [{"file": "a.py", "line": 10}]
    findings = [{"file": "a.py", "line": 10, "verdict": "GENUINE_BUG"} for _ in range(10)]
    r = match_findings(findings, key, line_tolerance=2)
    assert r["tp"] == 1 and r["fp"] == 9 and r["precision"] == 0.1


def test_match_findings_uses_maximum_matching_not_greedy():
    """Findings at 11 and 9 vs injections at 10 and 12 (tol 2) must score TWO hits; greedy
    matching takes 11->10, strands 9, and under-reports recall."""
    from sealeval.measure import match_findings
    r = match_findings(
        [{"file": "a.py", "line": 11, "verdict": "GENUINE_BUG"},
         {"file": "a.py", "line": 9, "verdict": "GENUINE_BUG"}],
        [{"file": "a.py", "line": 10}, {"file": "a.py", "line": 12}], line_tolerance=2)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 0)


def test_match_findings_path_norm_keeps_dotted_dirs():
    from sealeval.measure import match_findings
    r = match_findings([{"file": ".hidden/x.py", "line": 5, "verdict": "GENUINE_BUG"}],
                       [{"file": ".hidden/x.py", "line": 5}])
    assert r["tp"] == 1        # lstrip("./") used to mangle this to "hidden/x.py"


def test_match_findings_tolerance_is_explicit():
    from sealeval.measure import match_findings
    key = [{"file": "a.py", "line": 10}]
    f = [{"file": "./a.py", "line": 14, "verdict": "GENUINE_BUG"}]   # also checks path norm
    assert match_findings(f, key, line_tolerance=2)["tp"] == 0
    assert match_findings(f, key, line_tolerance=5)["tp"] == 1       # widening MUST be prereg'd


def test_round0_ready_when_apparatus_clean():
    from sealeval.measure import round0_gate
    r = round0_gate(control_labels={"pos": "F", "neg": "C"}, control_truth={"pos": "F", "neg": "C"},
                    baseline_rate=0.30, attempted=18, parsed=18, leak_controlled=True)
    assert r["ready_to_seal"] is True and r["verdict"] == "READY_TO_SEAL"
    assert r["blocking"] == [] and r["warnings"] == []


def test_round0_strict_blocks_unverified_checks():
    """Absence of evidence != a sound apparatus. In strict mode (use it before a freeze) an
    UNVERIFIED check must block, otherwise Round 0 can be 'passed' by supplying nothing."""
    from sealeval.measure import round0_gate
    lax = round0_gate(baseline_rate=0.0, leak_controlled=True, strict=False)
    assert lax["ready_to_seal"] is True and lax["warnings"]      # the misleading pass
    strict = round0_gate(baseline_rate=0.0, leak_controlled=True)  # strict is DEFAULT
    assert strict["ready_to_seal"] is False
    assert any("UNVERIFIED" in b for b in strict["blocking"])


def test_round0_blocks_on_ceilinged_metric():
    # the meta_ab lesson: module-recall baseline 0.909 -> a NULL would be uninformative
    from sealeval.measure import round0_gate
    r = round0_gate(baseline_rate=0.909, attempted=10, parsed=10, leak_controlled=True,
                    control_labels={"pos": "F"}, control_truth={"pos": "F"})
    assert r["ready_to_seal"] is False
    assert any("METRIC_CEILING_LIMITED" in b for b in r["blocking"])


def test_round0_blocks_on_void_controls_and_low_parse():
    from sealeval.measure import round0_gate
    r = round0_gate(control_labels={"pos": "C"}, control_truth={"pos": "F"},
                    baseline_rate=0.30, attempted=18, parsed=14, leak_controlled=True)
    assert r["ready_to_seal"] is False
    assert any("CONTROLS_VOID" in b for b in r["blocking"])
    assert any("PARSE_RATE_LOW" in b for b in r["blocking"])


def test_round0_takes_no_hypothesis_result_and_blocks_leakage():
    """Structural guard: Round 0 must not be able to report an effect, and declared leakage blocks."""
    import inspect
    from sealeval.measure import round0_gate, round0_receipt
    params = set(inspect.signature(round0_gate).parameters)
    assert not (params & {"lift", "effect", "delta", "treatment_rate", "result"})
    r = round0_gate(baseline_rate=0.30, leak_controlled=False,
                    control_labels={"p": "F"}, control_truth={"p": "F"}, attempted=5, parsed=5)
    assert r["ready_to_seal"] is False
    assert any("LEAKAGE_UNCONTROLLED" in b for b in r["blocking"])
    assert round0_receipt(r)["round0_ready"] is False


def test_metric_discrimination_ceiling_limited():
    # the meta-skill A/B lesson: module-recall baseline sat at ~0.90 -> no headroom
    g = stats.metric_discrimination_gate(0.909)
    assert g["verdict"] == "CEILING_LIMITED" and g["ceiling_limited"] is True
    assert g["headroom"] < 0.10


def test_metric_discrimination_ok_finer_metric():
    # file-level baseline 0.30 -> ample headroom, the metric CAN reveal a lift
    g = stats.metric_discrimination_gate(0.30)
    assert g["verdict"] == "OK" and g["ceiling_limited"] is False
    assert g["headroom"] == 0.70


def test_metric_discrimination_floor_room():
    g = stats.metric_discrimination_gate(0.05)
    assert g["verdict"] == "FLOOR_ROOM" and g["ceiling_limited"] is False


def test_metric_discrimination_boundary():
    # baseline exactly 0.90 -> headroom exactly 0.10, not < 0.10 -> OK
    assert stats.metric_discrimination_gate(0.90)["verdict"] == "OK"


def test_panel_no_consensus_excluded():
    jl = {"claude": ["F", "F"], "gpt": ["C", "F"]}   # case0 disagreement -> NO_CONSENSUS
    r = panel.evaluate(
        n_cases=2, judge_labels=jl, families=_two_family(),
        tool_labels=["F", "F"],
    )
    assert r["no_consensus_cases"] == [0]
    assert r["tool_vs_panel"]["n_scored"] == 1
    assert r["tool_vs_panel"]["n_excluded_no_consensus"] == 1


# --- CLI: the zero-integration entry point ---------------------------------

def test_cli_accuracy_reports_honest_interval(capsys):
    from sealeval.cli import main
    assert main(["check", "--accuracy", "8/8"]) == 0
    out = capsys.readouterr().out
    assert "[0.68, 1.00]" in out and "at least 68%" in out


def test_cli_accuracy_warns_on_coin_flip(capsys):
    from sealeval.cli import main
    main(["check", "--accuracy", "4/7"])
    assert "coin flip" in capsys.readouterr().out


def test_cli_accuracy_rejects_nonsense(capsys):
    from sealeval.cli import main
    assert main(["check", "--accuracy", "9/8"]) == 2
    assert main(["check", "--accuracy", "banana"]) == 2


def test_cli_baseline_flags_ceiling(capsys):
    from sealeval.cli import main
    main(["check", "--baseline", "0.93"])
    assert "CEILING_LIMITED" in capsys.readouterr().out


def test_cli_labels_reads_csv_and_warns_unchecked_family(tmp_path, capsys):
    from sealeval.cli import main
    p = tmp_path / "j.csv"
    p.write_text("a,b,truth\nF,F,F\nC,C,C\nF,C,F\nF,F,F\n", encoding="utf-8")
    assert main(["check", "--labels", str(p)]) == 0
    out = capsys.readouterr().out
    assert "4 cases, 2 judges" in out
    assert "UNCHECKED" in out          # no --families -> must not silently pass as cross-family
    assert "cross_family=False" in out


def test_cli_labels_needs_two_judges(tmp_path, capsys):
    from sealeval.cli import main
    p = tmp_path / "one.csv"
    p.write_text("a,truth\nF,F\n", encoding="utf-8")
    assert main(["check", "--labels", str(p)]) == 2


def test_cli_controls_actually_run_the_void_gate(tmp_path, capsys):
    """The `control` column used to be parsed out and then ignored, so the tool kept telling
    people to add controls and did nothing when they did."""
    from sealeval.cli import main
    p = tmp_path / "c.csv"
    p.write_text("a,b,truth,control\nF,F,F,obvious_pos\nC,C,C,obvious_neg\nF,C,F,\nC,C,C,\n",
                 encoding="utf-8")
    main(["check", "--labels", str(p), "--families", "a:anthropic,b:openai"])
    out = capsys.readouterr().out
    assert "controls: 2/2" in out and "No controls in this file" not in out


def test_cli_controls_void_a_run_when_judges_miss_the_obvious(tmp_path, capsys):
    from sealeval.cli import main
    p = tmp_path / "v.csv"
    p.write_text("a,b,truth,control\nC,F,F,obvious_pos\nC,C,C,obvious_neg\nF,C,F,\nC,C,C,\n",
                 encoding="utf-8")
    main(["check", "--labels", str(p)])
    out = capsys.readouterr().out
    assert "VOID" in out and "obvious_pos" in out


def test_cli_control_row_without_truth_is_rejected(tmp_path, capsys):
    from sealeval.cli import main
    p = tmp_path / "bad.csv"
    p.write_text("a,b,truth,control\nF,F,,obvious_pos\nF,C,F,\n", encoding="utf-8")
    assert main(["check", "--labels", str(p)]) == 2


def test_match_findings_raises_when_no_finding_carries_a_verdict():
    """Findings passed straight from a reviewer have no `verdict`; silently reporting tp=0
    would read as 'the system found nothing', which is a lie."""
    import pytest
    from sealeval.measure import match_findings
    with pytest.raises(ValueError):
        match_findings([{"file": "a.py", "line": 10}], [{"file": "a.py", "line": 10}])
