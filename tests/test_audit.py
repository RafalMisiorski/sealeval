#!/usr/bin/env python3
"""Deterministic tests for sealeval.audit. No model calls: every input is a fixed label, pick,
score or text. Each module carries the failure it was extracted from, so several tests are the
exact shape of that failure (a judge that knows the class; controls the author got wrong; a panel
that picks a side on identical pairs; a split-defining feature; a review that invents a number)."""
import json
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sealeval.audit import controls, leakage, pairs, recapture, review, temporal  # noqa: E402


# --- leakage -----------------------------------------------------------------

def test_exact_title_hit_is_exact_after_normalisation():
    assert leakage.exact_title_hit(["Foo Bank S.A.", "Bar"], "foo bank sa")
    assert not leakage.exact_title_hit(["Foo Bank"], "Foo")           # substring is not a hit
    assert not leakage.exact_title_hit([], "Foo")
    assert not leakage.exact_title_hit(["x"], "")


def test_probe_prompt_carries_only_public_fields():
    p = leakage.probe_prompt([{"id": "a", "name": "Acme", "outcome": "DEAD", "arm": "engine"}])
    assert '"id": "a"' in p and '"name": "Acme"' in p
    assert "DEAD" not in p and "engine" not in p
    assert "KNOWN" in p and "UNKNOWN" in p


def test_parse_probe_drops_unknown_ids_and_bad_verdicts_and_never_defaults():
    raw = 'sure:\n```json\n[{"id":"a","verdict":"known","note":"x"},{"id":"zzz","verdict":"KNOWN"},{"id":"b","verdict":"maybe"}]\n```'
    out = leakage.parse_probe(raw, ["a", "b", "c"])
    assert out == {"a": {"verdict": "KNOWN", "note": "x"}}       # b unparseable, c unanswered: both absent


def test_leak_report_share_is_over_the_labelled_class_and_unanswered_is_not_clean():
    labelled = ["a", "b", "c", "d", "e"]
    known = {"j1": {"a": {"verdict": "KNOWN"}, "b": {"verdict": "UNKNOWN"}, "c": {"verdict": "UNKNOWN"}, "d": {"verdict": "UNKNOWN"}},
             "j2": {"a": {"verdict": "UNKNOWN"}, "b": {"verdict": "KNOWN"}, "c": {"verdict": "UNKNOWN"}, "d": {"verdict": "UNKNOWN"},
                    "zz": {"verdict": "KNOWN"}}}
    r = leakage.leak_report(labelled, known, notable=["c"])
    assert r["labelled"] == 5 and r["leaked"] == 3 and r["leak_share"] == 0.6
    assert r["clean_ids"] == ["d"] and r["unanswered"] == 1        # e answered by nobody -> not clean
    assert r["per_judge"]["j2"]["answered"] == 4 and r["per_judge"]["j2"]["known"] == 1


def test_leakage_gate_applies_preregistered_share():
    r = {"leak_share": 0.77, "clean": 2}
    g = leakage.leakage_gate(r, max_share=0.20)
    assert not g["ok"] and "0.77" in g["reasons"][0]
    assert leakage.leakage_gate({"leak_share": 0.1, "clean": 30}, max_share=0.20)["ok"]
    assert not leakage.leakage_gate({"leak_share": None}, max_share=0.20)["ok"]


def test_leakage_report_lines_ascii():
    r = leakage.leak_report(["a"], {"j": {"a": {"verdict": "UNKNOWN"}}})
    lines = leakage.report_lines(r, leakage.leakage_gate(r))
    assert lines[0].startswith("leakage probe: 1 labelled, 0 leaked")
    assert all(ord(c) < 128 for line in lines for c in line)


# --- controls ----------------------------------------------------------------

POP = ([{"id": "s%02d" % i, "stratum": "engine", "name": "e%d" % i} for i in range(6)]
       + [{"id": "t%02d" % i, "stratum": "search", "name": "s%d" % i} for i in range(4)]
       + [{"id": "cp1", "stratum": "engine", "name": "pos"}, {"id": "cn1", "stratum": "search", "name": "neg"}])


def test_draw_sample_forces_controls_in_with_opaque_ids_and_is_seeded():
    s1 = controls.draw_sample(POP, strata={"engine": 3, "search": 2}, controls={"cp1": "positive", "cn1": "negative"}, seed=3)
    s2 = controls.draw_sample(POP, strata={"engine": 3, "search": 2}, controls={"cp1": "positive", "cn1": "negative"}, seed=3)
    assert [r["id"] for r in s1] == ["e%03d" % (i + 1) for i in range(7)]
    assert [r["source_key"] for r in s1] == [r["source_key"] for r in s2]
    roles = sorted(r["role"] for r in s1)
    assert roles == ["control_neg", "control_pos", "sample", "sample", "sample", "sample", "sample"]
    assert all(r["source_key"] not in ("cp1", "cn1") for r in s1 if r["role"] == "sample")


def test_draw_sample_rejects_bad_control_kind():
    try:
        controls.draw_sample(POP, strata={"engine": 1}, controls={"cp1": "maybe"}, seed=1)
    except ValueError as exc:
        assert "positive|negative" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _sample():
    return controls.draw_sample(POP, strata={"engine": 4, "search": 3}, controls={"cp1": "positive", "cn1": "negative"}, seed=11)


def _judgments(sample, wrong_controls=0, missing=0, yes_ids=()):
    out = {}
    wrong_left, miss_left = wrong_controls, missing
    for r in sample:
        if r["role"] == "control_pos":
            out[r["id"]] = "NO" if wrong_left > 0 else "YES"
            wrong_left -= 1
        elif r["role"] == "control_neg":
            out[r["id"]] = "YES" if wrong_left > 0 else "NO"
            wrong_left -= 1
        elif miss_left > 0:
            out[r["id"]] = None
            miss_left -= 1
        else:
            out[r["id"]] = "YES" if r["source_key"] in yes_ids else "NO"
    return out


def test_judge_void_on_controls_and_on_unparsed_share():
    s = _sample()
    ok = controls.judge_void(_judgments(s), s)
    assert not ok["void"] and ok["controls"] == 2 and ok["controls_wrong"] == 0
    bad = controls.judge_void(_judgments(s, wrong_controls=2), s, max_wrong=1)
    assert bad["void"] and "2 of 2 controls wrong" in bad["reasons"][0]
    lazy = controls.judge_void(_judgments(s, missing=3), s, max_unparsed=0.20)
    assert lazy["void"] and "unparsed share" in lazy["reasons"][0]


def test_panel_void_needs_two_live_judges_and_members_use_live_only():
    s = _sample()
    engine_keys = [r["source_key"] for r in s if r["role"] == "sample" and r["stratum"] == "engine"]
    first, second = engine_keys[0], engine_keys[1]
    jb = {"a": _judgments(s, yes_ids=(first, second)), "b": _judgments(s, yes_ids=(first,)), "c": _judgments(s, wrong_controls=2)}
    p = controls.panel_void(jb, s, max_wrong=1)
    assert p["live"] == ["a", "b"] and p["void_judges"] == ["c"] and not p["run_void"]
    m = controls.members(jb, s, p["live"], need=2)
    by_key = {r["source_key"]: m[r["id"]] for r in s}
    assert by_key[first] is True and by_key[second] is False           # the void judge c cannot make a member
    prec = controls.stratum_precision(s, m)
    assert prec["engine"]["judged"] == 4 and prec["engine"]["members"] == 1 and prec["engine"]["ci95"] is not None
    p2 = controls.panel_void({"a": jb["a"], "c": jb["c"]}, s, max_wrong=1)
    assert p2["run_void"] and "fewer than 2" in p2["reason"]


def test_packet_hygiene_catches_extra_keys_and_leak_tokens():
    packets = [{"id": "e001", "name": "x", "excerpt": "offer"}, {"id": "e002", "name": "y", "excerpt": "ENGINE_only hit", "stratum": "multi"}]
    bad = controls.packet_hygiene(packets, allowed_keys=("id", "name", "excerpt"), leak_tokens=("ENGINE_only", "control_pos"))
    assert len(bad) == 2 and any("keys" in b for b in bad) and any("ENGINE_only" in b for b in bad)
    assert controls.packet_hygiene(packets[:1], allowed_keys=("id", "name", "excerpt"), leak_tokens=("stratum",)) == []


def test_controls_report_lines_ascii():
    s = _sample()
    p = controls.panel_void({"a": _judgments(s), "b": _judgments(s, wrong_controls=2)}, s, max_wrong=1)
    lines = controls.report_lines(p)
    assert "RUN VOID" in lines[0] and all(ord(c) < 128 for line in lines for c in line)


# --- pairs -------------------------------------------------------------------

POS = [{"id": "p%d" % i, "year": 2015 + (i % 3), "cat": "a" if i % 2 else "b"} for i in range(6)]
NEG = [{"id": "n%d" % i, "year": 2015 + (i % 3), "cat": "a" if i % 2 else "b"} for i in range(6)]


def test_matched_pairs_exact_key_then_fallback_seeded_flip():
    key = lambda e: (e["year"], e["cat"])  # noqa: E731
    r1 = pairs.matched_pairs(POS, NEG, key=key, seed=5)
    r2 = pairs.matched_pairs(POS, NEG, key=key, seed=5)
    assert r1 == r2 and len(r1) == 6
    assert all(p["positive_side"] in ("A", "B") for p in r1)
    assert {p["positive_side"] for p in r1} == {"A", "B"}          # the flip is not constant
    for p in r1:
        pos = p["A"] if p["positive_side"] == "A" else p["B"]
        assert pos.startswith("p")
    capped = pairs.matched_pairs(POS, NEG, key=lambda e: e["id"], seed=5,
                                 fallback=lambda a, b: abs(a["year"] - b["year"]) <= 1, cap=2)
    assert len(capped) == 2                                          # exact never matches; fallback pairs, cap holds


def test_null_pairs_same_outcome_pool_and_no_reuse():
    n = pairs.null_pairs(POS, n=2, seed=1)
    assert len(n) == 2 and all(p["kind"] == "null" for p in n)
    ids = [p["A"] for p in n] + [p["B"] for p in n]
    assert len(set(ids)) == 4


def test_score_pairs_and_null_control():
    real = [{"pair_id": "p1", "positive_side": "A"}, {"pair_id": "p2", "positive_side": "B"}, {"pair_id": "p3", "positive_side": "A"}]
    picks = {"j1": {"p1": "A", "p2": "B", "p3": "B"}, "j2": {"p1": "A", "p2": "A"}}
    s = pairs.score_pairs(real, picks)
    assert s["per_judge"]["j1"]["accuracy"] == round(2 / 3, 4) and s["per_judge"]["j2"]["missing"] == 1
    assert s["pooled"]["correct"] == 3 and s["pooled"]["judged"] == 5
    nulls = [{"pair_id": "n%d" % i, "positive_side": "A"} for i in range(12)]
    even = {"j": {"n%d" % i: ("A" if i % 2 else "B") for i in range(12)}}
    nc = pairs.null_control(nulls, even)
    assert nc["ok"] is True and nc["n"] == 12
    biased = {"j": {"n%d" % i: "A" for i in range(12)}}
    assert pairs.null_control(nulls, biased)["ok"] is False
    assert pairs.null_control(nulls, {"j": {}})["ok"] is None        # unverified, not passed


def test_pairs_agreement_and_report_lines():
    real = [{"pair_id": "p1", "positive_side": "A"}, {"pair_id": "p2", "positive_side": "B"}]
    picks = {"j1": {"p1": "A", "p2": "B"}, "j2": {"p1": "A", "p2": "A"}}
    assert pairs.agreement(real, picks) == 0.5
    assert pairs.agreement(real, {"j1": picks["j1"]}) is None
    lines = pairs.report_lines(pairs.score_pairs(real, picks), pairs.null_control([], picks))
    assert lines[0].startswith("pairs: 2 judged") and "UNVERIFIED" in lines[-1]


# --- recapture ---------------------------------------------------------------

def test_chapman_textbook_and_edge_cases():
    assert recapture.chapman(45, 11, 5) == 91.0
    assert recapture.chapman(10, 10, 10) == 10.0                       # full overlap -> the lists ARE the class
    assert recapture.chapman(0, 5, 0) is None


def test_all_pairs_recapture_and_recall():
    r = recapture.all_pairs_recapture({"engine": {"a", "b"}, "search": {"a", "b", "c", "d"}, "manual": {"d", "e"}})
    assert r["engine_x_search"]["m"] == 2 and r["engine_x_search"]["N_hat"] == 4.0
    assert r["engine_x_manual"]["m"] == 0 and r["engine_x_manual"]["N_hat"] == 8.0
    rc = recapture.recall(9, 51)
    assert rc["recall"] == round(9 / 51, 4) and rc["ci95"][0] < rc["recall"] < rc["ci95"][1]


def test_precision_adjusted_recall_pays_for_precision():
    counts = {"engine_only": 200, "search_only": 40, "multi": 20}
    prec = {"engine_only": 0.175, "search_only": 0.51, "multi": 0.55}
    n_star = recapture.class_size(counts, prec)
    assert n_star == round(200 * 0.175 + 40 * 0.51 + 20 * 0.55, 1)
    arms = recapture.arm_recall({"ENGINE": {"engine_only": 200, "multi": 20}, "SEARCH": {"search_only": 40, "multi": 20}}, prec, counts)
    e, s = arms["arms"]["ENGINE"], arms["arms"]["SEARCH"]
    assert e["found"] == 220 and s["found"] == 60
    assert e["precision"] < s["precision"]
    assert e["recall_adj"] == recapture.precision_adjusted_recall(220, e["precision"], n_star)
    assert recapture.precision_adjusted_recall(5, 0.5, 0) is None


def test_recapture_report_lines_ascii():
    lines = recapture.report_lines(recapture.all_pairs_recapture({"a": {1, 2}, "b": {2, 3}}))
    assert lines[1].startswith("  a_x_b: n1=2 n2=2 m=1 -> N_hat=3.5") and all(ord(c) < 128 for line in lines for c in line)


# --- temporal ----------------------------------------------------------------

def test_auc_perfect_random_and_ties():
    assert temporal.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert temporal.auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert temporal.auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5           # all tied -> 0.5
    assert temporal.auc([0, 0, 0], [0.1, 0.2, 0.3]) is None                  # one class


def test_events_per_feature_rule():
    r = temporal.events_per_feature(12, 15, min_epf=10)
    assert not r["ok"] and r["max_features"] == 1 and "shrink" in r["note"]
    assert temporal.events_per_feature(150, 15)["ok"]


def test_split_defining_guard_refuses_year_and_reports_smd():
    tr = [{"year": 2014 + i % 3, "x": i % 2, "c": "a"} for i in range(30)]
    te = [{"year": 2017 + i % 2, "x": (i + 1) % 2, "c": "a"} for i in range(30)]
    g = temporal.split_defining_features(tr, te, ["year", "x", "c"])
    assert g["refused"] == ["year"]                                    # same value on both sides -> not refused
    assert abs(g["smd"]["year"]) > 2.0 and g["smd"]["c"] is None and abs(g["smd"]["x"]) < 0.5   # categorical: no SMD


def test_permutation_p_plus_one_and_none():
    assert temporal.permutation_p(0.70, [0.5, 0.6, 0.72, 0.8]) == round(3 / 5, 4)
    assert temporal.permutation_p(0.99, [0.5] * 199) == round(1 / 200, 4)
    assert temporal.permutation_p(None, [0.5]) is None and temporal.permutation_p(0.5, []) is None


def test_bootstrap_auc_ci_deterministic_and_brackets_point():
    y = [0, 0, 0, 0, 1, 1, 1, 1, 0, 1]
    s = [0.1, 0.3, 0.2, 0.6, 0.7, 0.4, 0.9, 0.8, 0.5, 0.55]
    ci1 = temporal.bootstrap_auc_ci(y, s, iters=300, seed=3)
    ci2 = temporal.bootstrap_auc_ci(y, s, iters=300, seed=3)
    assert ci1 == ci2 and ci1[0] <= temporal.auc(y, s) <= ci1[1]
    assert temporal.bootstrap_auc_ci([], []) is None


def test_calibration_bins_brier_and_class_weight_drift():
    p = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05]
    y = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    bins = temporal.calibration_bins(p, y, bins=5)
    assert len(bins) == 5 and sum(b["n"] for b in bins) == 10
    assert bins[0]["mean_p"] < bins[-1]["mean_p"]
    d = temporal.class_weight_drift(bins)
    assert d["warn"] is True and "recalibrate" in d["note"]
    assert temporal.brier(p, y) > temporal.base_rate_brier(0.1, y)             # the over-predicting fit loses to the base rate
    assert temporal.class_weight_drift([])["warn"] is None


def test_temporal_gate_unverified_fails_and_refused_features_fail():
    g = temporal.temporal_gate(auc_test=0.568, ci_lo=0.426, p_perm=0.2886, brier_test=0.4213, brier_base=0.1728,
                               n_events_test=19, refused_features=["year"])
    assert g["verdict"] == "NOT_SHOWN" and len(g["failed"]) == 6
    ok = temporal.temporal_gate(auc_test=0.81, ci_lo=0.72, p_perm=0.005, brier_test=0.12, brier_base=0.18, n_events_test=40)
    assert ok["verdict"] == "WORTH_USING" and ok["failed"] == []
    unv = temporal.temporal_gate(auc_test=0.81, ci_lo=0.72, p_perm=None, brier_test=0.12, brier_base=0.18, n_events_test=40)
    assert unv["verdict"] == "NOT_SHOWN" and "permutation p n/a" in unv["failed"][0]


def test_temporal_report_lines_ascii():
    g = temporal.temporal_gate(auc_test=None, ci_lo=None, p_perm=None, brier_test=None, brier_base=None, n_events_test=0)
    lines = temporal.report_lines(g, drift={"warn": True, "note": "x"}, shift={"year": 3.65, "x": 0.1})
    assert lines[0] == "temporal gate: NOT_SHOWN" and any("year=3.65" in line for line in lines)
    assert all(ord(c) < 128 for line in lines for c in line)


# --- review ------------------------------------------------------------------

RESULT = "Verdict NOT_SHOWN. test AUC 0.5682 [0.4262, 0.7199], permutation p 0.2886, dead in test 19."
GUARDS = "RIGOR-INVERSION: a sealed verdict is never overturned post hoc."


def _bundle():
    return review.build_bundle("m38c", {"runs/RESULT.md": RESULT, "PREREG.md": "R6: AUC >= 0.70"}, guards=GUARDS)


def _good():
    return {"measurement": "m38c", "verdict_restated": "Sealed NOT_SHOWN: AUC 0.5682 [0.4262, 0.7199], p 0.2886.",
            "decides": [{"claim": "Under R6 the model is NOT_SHOWN (AUC 0.5682 below 0.70).", "evidence": "runs/RESULT.md"}],
            "does_not_decide": [{"claim": "That launch signals carry no signal in general.", "why": "19 events is underpowered."}],
            "moves": [{"rank": 1, "title": "Close out", "cost": "2 hours", "decides": "the durable record", "needs_prereg": False,
                       "owner": "team", "blocked_by": ""}],
            "operator_decisions": [{"question": "Close the question?", "options": ["close", "probe"], "default_if_silent": "probe",
                                    "why_operator": "priority call", "default_move_rank": 1}],
            "guards": {"rigor_inversion": "verdict untouched", "platform_absorption": "no build proposed"},
            "confidence": 0.8}


def test_bundle_is_deterministic_and_labelled():
    b1, b2 = _bundle(), _bundle()
    assert b1.sha256 == b2.sha256 and b1.labels == ["runs/RESULT.md", "PREREG.md", "GUARDS"]
    assert "### FILE: GUARDS" in b1.text
    assert review.build_bundle("m", {"PREREG.md": "R6: AUC >= 0.70", "runs/RESULT.md": RESULT}).sha256 != b1.sha256


def test_render_prompt_binds_bundle_and_owners():
    p = review.render_prompt(_bundle(), owners=("operator", "lab", "both"))
    assert "=== BUNDLE (measurement m38c" in p and '"operator" | "lab" | "both"' in p and RESULT in p


def test_validate_accepts_good_review():
    assert review.validate(_good(), _bundle(), owners=("operator", "team", "both")) == []


def test_validate_rejects_invented_number_bad_evidence_owner_and_default_rank():
    b = _bundle()
    r = _good()
    r["verdict_restated"] = "AUC 0.57 fails"                                    # 0.57 is not in the bundle verbatim
    r["decides"][0]["evidence"] = "somewhere/else.md"
    r["moves"][0]["owner"] = "ceo"
    r["operator_decisions"][0]["default_move_rank"] = 9
    errs = review.validate(r, b, owners=("operator", "team", "both"))
    assert any("numbers not in bundle: 0.57" in e for e in errs)
    assert any("not a bundle label" in e for e in errs)
    assert any("owner must be one of" in e for e in errs)
    assert any("default_move_rank 9" in e for e in errs)


def test_validate_rejects_missing_keys_guards_and_confidence():
    b = _bundle()
    assert review.validate({"measurement": "m38c"}, b)[0].startswith("missing key")
    r = _good()
    r["guards"] = {"rigor_inversion": "ok"}
    r["confidence"] = 1.7
    errs = review.validate(r, b)
    assert any("guards:" in e for e in errs) and any("confidence outside" in e for e in errs)


def test_parse_review_strips_fences_and_rejects_non_objects():
    assert review.parse_review('```json\n{"a": 1}\n```')["a"] == 1
    for bad in ("no json here", "[1, 2]"):
        try:
            review.parse_review(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for %r" % bad)


def test_review_end_to_end_with_injected_model_accepts_and_rejects():
    b = _bundle()
    ok = review.review(b, lambda prompt: json.dumps(_good()))
    assert ok["status"] == "ACCEPTED" and ok["errors"] == [] and ok["bundle_sha256"] == b.sha256
    bad = review.review(b, lambda prompt: "I think the AUC is about 0.57, so...")
    assert bad["status"] == "REJECTED" and bad["errors"][0].startswith("unparseable")
    inv = _good()
    inv["decides"][0]["claim"] = "AUC 0.57"
    rej = review.review(b, lambda prompt: json.dumps(inv))
    assert rej["status"] == "REJECTED" and any("0.57" in e for e in rej["errors"])
    lines = review.report_lines(ok) + review.report_lines(rej)
    assert lines[0].startswith("review: ACCEPTED") and any("violation" in line for line in lines)
    assert all(ord(c) < 128 for line in lines for c in line)


# --- package surface ---------------------------------------------------------

def test_package_exports_and_zero_dependencies():
    import sealeval
    import sealeval.audit as A
    for name in ("leak_report", "panel_void", "null_control", "chapman", "temporal_gate", "validate", "run_review"):
        assert hasattr(A, name), name
    assert hasattr(sealeval, "audit")
    import importlib
    for mod in ("leakage", "controls", "pairs", "recapture", "temporal", "review", "cli"):
        m = importlib.import_module("sealeval.audit." + mod)
        src = open(m.__file__, encoding="utf-8").read()
        for banned in ("numpy", "sklearn", "pandas", "scipy", "requests"):
            assert banned not in src, (mod, banned)
