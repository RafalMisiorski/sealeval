"""sealeval test suite — mutation, sealing, judge. Offline, zero LLM calls.

Mirrors the harvested NH benchmark foundation/judge tests, against the standalone API.
"""

from __future__ import annotations

import ast
import json

import pytest

import sealeval as se
from sealeval import judge as bj
from sealeval.mutation import catalog, seeder
from sealeval.sealing import keyseal, prereg

SAMPLE_SRC = '''\
def f(items, cfg):
    total = 0
    for i in range(len(items)):
        total = total + i
    if total > 10:
        total = 0
    name = cfg.get("name", "fallback")
    try:
        do_thing()
    except Exception:
        raise
    return total, name
'''


# ---------------------------------------------------------------------------
# mutation
# ---------------------------------------------------------------------------


def test_catalog_finds_every_archetype():
    found = {c.archetype for c in catalog.find_candidates(SAMPLE_SRC)}
    assert {"off_by_one", "inverted_condition", "wrong_operator", "null_deref",
            "swallowed_exception"} <= found


def test_off_by_one_narrows_stop_not_step():
    # regression: on range(start, stop, step) the mutation must narrow the STOP bound,
    # never the STEP (mutating the step is not an off-by-one and mislabels the injection).
    def mutate(code):
        return catalog._off_by_one(ast.parse(f"x = {code}"), f"x = {code}")[0].new_src

    assert mutate("range(n)") == "range((n) - 1)"
    assert mutate("range(a, b)") == "range(a, (b) - 1)"
    assert mutate("range(a, b, 2)") == "range(a, (b) - 1, 2)"  # step 2 untouched


def test_seed_corpus_injects_valid_and_stable(tmp_path):
    src = tmp_path / "clean"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text(SAMPLE_SRC, encoding="utf-8")

    res = seeder.seed_corpus(src, tmp_path / "corpus", per_file=5, seed=7)
    assert res.mutations >= 3 and res.files_mutated == 1

    mutated = (tmp_path / "corpus" / "pkg" / "mod.py").read_text(encoding="utf-8")
    ast.parse(mutated)  # always parses (compile-guard)
    assert len(mutated.splitlines()) == len(SAMPLE_SRC.splitlines())  # line numbers stable
    for r in res.records:
        assert r.original_segment != r.mutated_segment
        assert r.original_sha256 != r.mutated_sha256


def test_seed_corpus_deterministic_and_key_roundtrip(tmp_path):
    src = tmp_path / "clean"
    src.mkdir()
    (src / "mod.py").write_text(SAMPLE_SRC, encoding="utf-8")
    a = seeder.seed_corpus(src, tmp_path / "a", per_file=5, seed=42)
    b = seeder.seed_corpus(src, tmp_path / "b", per_file=5, seed=42)
    assert [r.to_dict() for r in a.records] == [r.to_dict() for r in b.records]

    keypath = seeder.dump_injection_key(a.records, tmp_path / "key.json")
    assert [r.to_dict() for r in seeder.load_injection_key(keypath)] == [r.to_dict() for r in a.records]


def test_per_archetype_cap(tmp_path):
    src = tmp_path / "clean"
    src.mkdir()
    for n in range(6):
        (src / f"m{n}.py").write_text(SAMPLE_SRC, encoding="utf-8")
    res = seeder.seed_corpus(src, tmp_path / "corpus", per_file=5, seed=2, per_archetype_cap=2)
    assert all(c <= 2 for c in res.archetype_counts.values())


def test_max_total_caps_injections(tmp_path):
    src = tmp_path / "clean"
    src.mkdir()
    for n in range(6):
        (src / f"m{n}.py").write_text(SAMPLE_SRC, encoding="utf-8")
    res = seeder.seed_corpus(src, tmp_path / "corpus", per_file=5, seed=3, max_total=4)
    assert res.mutations == 4


def test_multibyte_source_line_numbers_stay_stable(tmp_path):
    # non-ASCII content must not desync the line-numbered key from the mutated file
    src = tmp_path / "clean"
    src.mkdir()
    body = 'def g(xs):\n    label = "café ☕ π"  # multibyte\n    for i in range(len(xs)):\n        pass\n    return label\n'
    (src / "u.py").write_text(body, encoding="utf-8")
    res = seeder.seed_corpus(src, tmp_path / "corpus", per_file=3, seed=1)
    mutated = (tmp_path / "corpus" / "u.py").read_text(encoding="utf-8")
    ast.parse(mutated)
    assert len(mutated.splitlines()) == len(body.splitlines())  # byte-stable line count
    for r in res.records:
        assert mutated.splitlines()[r.line - 1] != body.splitlines()[r.line - 1]  # key line = changed line


# ---------------------------------------------------------------------------
# sealing
# ---------------------------------------------------------------------------


def test_seal_roundtrip_and_tamper():
    key = [{"file": "a.py", "line": 3, "archetype": "off_by_one"}]
    seal = keyseal.make_seal(key, salt="deadbeef")
    assert keyseal.verify_seal(key, seal) is True
    assert keyseal.verify_seal([{"file": "a.py", "line": 4}], seal) is False  # moved a byte


def test_reveal_gate_and_mismatch(tmp_path):
    findings = tmp_path / "findings"
    findings.mkdir()
    key = [{"file": "a.py", "line": 1, "archetype": "null_deref"}]
    seal = keyseal.make_seal(key, salt="aa")
    (findings / "B0.json").write_text("[]", encoding="utf-8")
    with pytest.raises(keyseal.SealError):  # gate: not all systems done
        keyseal.reveal(key, seal, tmp_path / "k.json", findings_dir=findings, systems=["B0", "NH"])
    (findings / "NH.json").write_text("[]", encoding="utf-8")
    assert keyseal.reveal(key, seal, tmp_path / "k.json", findings_dir=findings, systems=["B0", "NH"]).exists()
    with pytest.raises(keyseal.SealError):  # plaintext != seal
        keyseal.reveal([{"file": "x"}], seal, tmp_path / "k2.json")


def test_reveal_gate_rejects_malformed_findings(tmp_path):
    findings = tmp_path / "findings"
    findings.mkdir()
    # a non-JSON / non-list / empty placeholder must NOT unlock the reveal (only existence-checked before)
    (findings / "A.json").write_text("not json at all", encoding="utf-8")
    assert keyseal.reveal_gate_ok(findings, ["A"]) is False
    (findings / "A.json").write_text("{}", encoding="utf-8")            # JSON, but not the list shape
    assert keyseal.reveal_gate_ok(findings, ["A"]) is False
    (findings / "A.json").write_text("", encoding="utf-8")             # 0-byte
    assert keyseal.reveal_gate_ok(findings, ["A"]) is False
    (findings / "A.json").write_text("[]", encoding="utf-8")           # empty list = legit "found nothing"
    assert keyseal.reveal_gate_ok(findings, ["A"]) is True


def test_prereg_freeze_verify_drift(tmp_path):
    content = {"hypotheses": {"H1": "x"}, "thresholds": {"go_ratio": 0.33}}
    lock = tmp_path / "prereg.lock.json"
    prereg.freeze(content, lock, frozen_at="2026-06-20T00:00:00Z")
    assert prereg.verify(lock) is True and prereg.require_verified(lock) == content
    d = prereg.load(lock)
    d["content"]["thresholds"]["go_ratio"] = 0.99  # post-hoc tamper
    lock.write_text(json.dumps(d), encoding="utf-8")
    assert prereg.verify(lock) is False
    with pytest.raises(prereg.PreRegError):
        prereg.require_verified(lock)


# ---------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------


def _fake_judge(system, user, model):
    if "GENUINE" in user:
        return '{"verdict":"GENUINE_BUG","confidence":0.9,"mechanism":"line 1 does X"}'
    if "STYLE" in user:
        return '```json\n{"verdict":"REAL_NOT_BUG","confidence":0.5,"mechanism":"smell"}\n```'
    return "not json at all"  # -> REFUTED default


def test_judge_taxonomy_strip_and_scope(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    claims = [
        {"id": "c1", "file": "mod.py", "line": 1, "claim": "GENUINE defect", "severity": "HIGH", "src": "A"},
        {"id": "c2", "file": "mod.py", "line": 2, "claim": "STYLE smell only"},
        {"id": "c3", "file": "mod.py", "line": 1, "claim": "vague refuted thing"},
        {"id": "c4", "file": "missing.py", "line": 1, "claim": "file gone"},
    ]
    rep = bj.judge_claims(claims, scope=str(tmp_path), judge_fn=_fake_judge)
    # c4 claims a NONEXISTENT file -> that is the SYSTEM hallucinating, so it counts as
    # REFUTED and stays in the precision denominator (a reviewer inventing paths must be
    # penalized, not excluded). `errored` is only for backend/infra failures.
    assert (rep.genuine_bug, rep.real_not_bug, rep.refuted, rep.errored) == (1, 1, 2, 0)
    assert rep.precision_genuine_bug == round(1 / 4, 3)
    c4 = next(v for v in rep.verdicts if v.claim_id == "c4")
    assert c4.verdict == "REFUTED" and c4.error == "file not found"


def test_judge_requires_a_backend(tmp_path):
    (tmp_path / "m.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(ValueError):  # no judge_fn, no built-in backend
        bj.judge_claims([{"id": "c", "file": "m.py", "line": 1, "claim": "x"}], scope=str(tmp_path))


def test_parse_verdict_skeptical():
    assert bj._parse_verdict("not json")[0] == "REFUTED"
    assert bj._parse_verdict('{"verdict":"BOGUS"}')[0] == "REFUTED"
    assert bj._parse_verdict('{"verdict":"GENUINE_BUG","confidence":5}')[:2] == ("GENUINE_BUG", 1.0)


def test_strip_provenance():
    out = bj._strip_provenance({"id": "x", "file": "f", "line": 3, "claim": "c",
                                "severity": "HIGH", "src": "A", "verdict": "X"})
    assert set(out) == {"id", "file", "line", "claim"}


def test_calibration_writes_jsonl(tmp_path):
    rep = bj.JudgeReport("m", 2, 1, 0, 1, 0, [bj.JudgeVerdict("c1", "GENUINE_BUG", 0.9, "m")])
    p = tmp_path / "cal.jsonl"
    bj.append_calibration(rep, run_id="r1", when="2026-06-20T00:00:00Z", path=p)
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["genuine_bug"] == 1 and rec["verdicts"] == {"c1": "GENUINE_BUG"}


def test_public_api_surface():
    for name in ["judge_claims", "seed_corpus", "make_seal", "verify_seal", "reveal",
                 "prereg_freeze", "JUDGE_SYSTEM", "VERDICTS"]:
        assert hasattr(se, name)


def test_prereg_backwards_compatible_without_round0(tmp_path):
    """No round0 argument -> content and hash bit-identical to the pre-Round-0 behaviour, so
    existing locks (skillsbench prereg_v1..v27) still verify."""
    content = {"bar": "keep iff lift >= 0.15", "n": 12}
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    lock_old = prereg.freeze(content, a, frozen_at="2026-06-20T00:00:00Z")
    lock_new = prereg.freeze(content, b, frozen_at="2026-06-20T00:00:00Z")
    assert lock_old["content_sha256"] == lock_new["content_sha256"]
    assert "round0" not in lock_new["content"] and prereg.verify(b) is True


def test_prereg_binds_round0_receipt_into_the_seal(tmp_path):
    from sealeval.measure import round0_gate
    r0 = round0_gate(control_labels={"p": "F"}, control_truth={"p": "F"}, baseline_rate=0.30,
                     attempted=10, parsed=10, leak_controlled=True)
    lock = prereg.freeze({"bar": "x"}, tmp_path / "c.json", round0=r0)
    assert lock["content"]["round0"]["round0_ready"] is True
    assert prereg.verify(tmp_path / "c.json") is True   # receipt is inside the hashed content


def test_prereg_refuses_to_seal_unvalidated_apparatus(tmp_path):
    from sealeval.measure import round0_gate
    bad = round0_gate(baseline_rate=0.95, attempted=10, parsed=10, leak_controlled=True,
                      control_labels={"p": "F"}, control_truth={"p": "F"})  # ceiling'd metric
    assert bad["ready_to_seal"] is False
    with pytest.raises(prereg.PreRegError):
        prereg.freeze({"bar": "x"}, tmp_path / "d.json", round0=bad)
    with pytest.raises(prereg.PreRegError):
        prereg.freeze({"bar": "x"}, tmp_path / "e.json", require_round0=True)


def test_public_api_surface():
    for name in ["judge_claims", "seed_corpus", "make_seal", "verify_seal", "reveal",
                 "prereg_freeze", "JUDGE_SYSTEM", "VERDICTS"]:
        assert hasattr(se, name)


# --- versioned archetypes: the fingerprint fix -----------------------------

_FP_SRC = '''
def scan(rows, limit, cfg):
    if len(rows) > limit:
        return None
    for i in range(len(rows)):
        rows[i] = rows[i] + 1
    mode = cfg.get("mode", "fast")
    skip = cfg.get("skip", None)
    return mode, skip
'''
# v1-SPECIFIC fingerprints. Note `) - 1)` is deliberately NOT here: `foo(len(x) - 1)` is
# ordinary Python, and the empirical regex attack scored 12 false positives on clean source
# with it. A tell has to discriminate, or it is just a common substring.
_TELLS = ("not (", ") - (", ") + (", ")) - 1)")


def test_v1_mutants_carry_the_parenthesis_fingerprint():
    """Documents the defect v2 fixes: a 3-regex 'reviewer' scored recall 0.79 against a
    v1-sealed key because these edits are stylistically distinctive."""
    from sealeval.mutation import catalog
    c1 = catalog.find_candidates(_FP_SRC, version="v1")
    assert any(any(t in c.new_src for t in _TELLS) for c in c1)


def test_v2_mutants_are_idiomatic_no_fingerprint():
    from sealeval.mutation import catalog
    c2 = catalog.find_candidates(_FP_SRC, version="v2")
    assert c2, "v2 must still find candidates"
    assert not any(any(t in c.new_src for t in _TELLS) for c in c2)
    # comparisons are flipped, not negated
    assert any(c.archetype == "inverted_condition" and ">=" in c.new_src for c in c2)


def test_v2_skips_equivalent_null_deref_mutant():
    """`.get(k, None)` -> `.get(k)` is behaviour-identical; it must never enter a sealed key."""
    from sealeval.mutation import catalog
    v2_null = [c for c in catalog.find_candidates(_FP_SRC, version="v2")
               if c.archetype == "null_deref"]
    v1_null = [c for c in catalog.find_candidates(_FP_SRC, version="v1")
               if c.archetype == "null_deref"]
    assert len(v1_null) == 2                      # v1 mutates both .get calls
    assert len(v2_null) == 1                      # v2 skips the `None` default one
    assert '"mode"' in v2_null[0].new_src


def test_seed_result_records_archetype_version():
    from sealeval.mutation import catalog
    assert catalog.DEFAULT_ARCHETYPE_VERSION == "v2"
    assert set(catalog.ARCHETYPE_SETS) == {"v1", "v2"}


def test_replace_span_byte_offsets_survive_nonascii():
    """col_offset is a UTF-8 byte offset: non-ASCII earlier on the line must not shift the edit.
    (External review, 2026-07: char slicing mis-spliced and still parsed -> key/diff desync.)"""
    import ast
    from sealeval.mutation import catalog
    src = 'label = "café"; ok = x > y\n'          # 'é' = 1 char, 2 bytes, before the compare
    cmp = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Compare)][0]
    out = catalog.replace_span(src, cmp, "x < y")
    ast.parse(out)                                        # still valid
    assert out == 'label = "café"; ok = x < y\n'    # edit landed exactly, 'café' intact
