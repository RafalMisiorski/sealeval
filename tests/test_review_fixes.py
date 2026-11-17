"""Regression tests for the defects found in the 2026-09-29 code review.

Each test reproduces the failure first described in the review (the input that used to go wrong),
then asserts the fixed behaviour.
"""
import ast
import json
import re
from pathlib import Path

import sealeval
from sealeval import canary, judge
from sealeval.mutation import catalog, seeder
from sealeval.sealing import make_seal


def _v2_off_by_one(code):
    return [c.new_src for c in catalog.find_candidates(code, ("off_by_one",), version="v2")]


# ---------------------------------------------------------------------------- mutation: range stop, not step
def test_v2_off_by_one_narrows_stop_not_step():
    # used to be `range(a, b, 2 - 1)`: a STEP change recorded as "narrowed range upper bound by 1"
    assert _v2_off_by_one("x = range(a, b, 2)\n") == ["range(a, b - 1, 2)"]
    assert _v2_off_by_one("x = range(a, b)\n") == ["range(a, b - 1)"]
    assert _v2_off_by_one("x = range(n)\n") == ["range(n - 1)"]
    assert _v2_off_by_one("x = range(len(rows))\n") == ["range(len(rows) - 1)"]


def test_v2_off_by_one_keeps_parens_only_where_precedence_needs_them():
    assert _v2_off_by_one("x = range(a, b + c, 2)\n") == ["range(a, b + c - 1, 2)"]   # `b + c - 1` is fine
    assert _v2_off_by_one("x = range(a, b if f else c)\n") == ["range(a, (b if f else c) - 1)"]


def test_v2_off_by_one_skips_equivalent_literal_ranges():
    # range(0, 10, 2) and range(0, 9, 2) are the same sequence: not a bug, must never enter a sealed key
    assert _v2_off_by_one("x = range(0, 10, 2)\n") == []
    assert _v2_off_by_one("x = range(0, 10, 3)\n") == ["range(0, 10 - 1, 3)"]   # 0,3,6,9 -> 0,3,6
    assert _v2_off_by_one("x = range(10)\n") == ["range(10 - 1)"]


# ---------------------------------------------------------------------------- mutation: byte offsets
def test_replace_span_uses_byte_offsets_on_multibyte_lines():
    src = 's = "zażółć"; y = a + b\n'
    cand = catalog.find_candidates(src, ("wrong_operator",), version="v2")[0]
    out = catalog.replace_span(src, cand.node, cand.new_src)
    # used to produce `y = a + a - b`, which still parses, so the compile-guard let it through
    assert out == 's = "zażółć"; y = a - b\n'


def test_seeded_key_matches_the_file_on_multibyte_lines(tmp_path):
    src = tmp_path / "clean"
    src.mkdir()
    body = 'def f(a, b):\n    s = "łódź"; y = a + b\n    return s, y\n'
    (src / "m.py").write_text(body, encoding="utf-8")
    res = seeder.seed_corpus(src, tmp_path / "corpus", archetypes=("wrong_operator",), per_file=1, seed=0)
    assert res.records, "the site should be seeded"
    rec = res.records[0]
    line = (tmp_path / "corpus" / "m.py").read_text(encoding="utf-8").splitlines()[rec.line - 1]
    ast.parse((tmp_path / "corpus" / "m.py").read_text(encoding="utf-8"))
    assert line == '    s = "łódź"; y = ' + rec.mutated_segment   # the key describes exactly what is in the file
    assert rec.original_segment == "a + b"


# ---------------------------------------------------------------------------- seeder: non-UTF-8 files
def test_seed_corpus_copies_non_utf8_files_verbatim(tmp_path):
    src = tmp_path / "clean"
    src.mkdir()
    (src / "ok.py").write_text("x = range(n)\n", encoding="utf-8")
    raw = "x = 'caf\xe9'\n".encode("latin-1")
    (src / "legacy.py").write_bytes(raw)
    seeder.seed_corpus(src, tmp_path / "corpus", seed=0)
    assert (tmp_path / "corpus" / "legacy.py").read_bytes() == raw   # used to be missing from the corpus


# ---------------------------------------------------------------------------- judge: directory scope
def _corpus_with_secret(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "key.json").write_text(json.dumps([{"file": "a.py", "archetype": "SEALED-KEY"}]))
    return tmp_path / "corpus"


def test_directory_scope_refuses_parent_and_absolute_paths(tmp_path):
    corpus = _corpus_with_secret(tmp_path)
    seen = []

    def spy(system, user, model):
        seen.append(user)
        return '{"verdict": "GENUINE_BUG", "confidence": 1, "mechanism": "x"}'

    claims = [{"id": "up", "file": "../secret/key.json", "line": 1, "claim": "bug"},
              {"id": "abs", "file": str(tmp_path / "secret" / "key.json"), "line": 1, "claim": "bug"},
              {"id": "sib", "file": "../corpus-evil/../secret/key.json", "line": 1, "claim": "bug"},
              {"id": "ok", "file": "a.py", "line": 1, "claim": "bug"}]
    rep = judge.judge_claims(claims, scope=str(corpus), judge_fn=spy)
    assert len(seen) == 1 and "SEALED-KEY" not in seen[0]            # only a.py reached the judge
    errors = {v.claim_id: v.error for v in rep.verdicts}
    assert errors["up"] == errors["abs"] == "file not in scope"
    assert (rep.genuine_bug, rep.refuted, rep.errored) == (1, 3, 0)   # out-of-scope counts against precision


def test_directory_scope_allows_normalised_inner_paths(tmp_path):
    corpus = _corpus_with_secret(tmp_path)
    (corpus / "pkg").mkdir()
    rep = judge.judge_claims([{"id": "c", "file": "pkg/../a.py", "line": 1, "claim": "bug"}], scope=str(corpus),
                             judge_fn=lambda s, u, m: '{"verdict": "REFUTED", "confidence": 0.5, "mechanism": "-"}')
    assert rep.verdicts[0].error is None and rep.refuted == 1


# ---------------------------------------------------------------------------- judge: unparsed replies are visible
def test_unparsed_replies_count_as_refuted_but_are_reported(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    claims = [{"id": str(i), "file": "a.py", "line": 1, "claim": "bug"} for i in range(3)]
    replies = iter(["Sorry, I cannot help",
                    '{"verdict": "maybe"}',
                    '{"verdict": "GENUINE_BUG", "confidence": 0.9, "mechanism": "line 1"}'])
    rep = judge.judge_claims(claims, scope=str(tmp_path), judge_fn=lambda s, u, m: next(replies))
    assert (rep.genuine_bug, rep.refuted, rep.errored, rep.unparsed) == (1, 2, 0, 2)
    assert rep.to_dict()["unparsed"] == 2
    assert [v.error is not None for v in rep.verdicts] == [True, True, False]
    assert judge._parse_verdict("[1, 2]") == ("REFUTED", 0.0, "")          # a JSON list is garbage, not a crash


# ---------------------------------------------------------------------------- canary: file matching + paths
def test_canary_score_does_not_match_a_file_by_bare_suffix():
    key = [{"file": "a.py", "line": 10, "archetype": "off_by_one"}]
    seal = make_seal(key, salt="s")
    # used to count: "src/data.py".endswith("a.py")
    miss = canary.score(key, seal, [{"file": "src/data.py", "line": 10, "claim": "x"}])
    assert miss["found"] == 0 and miss["noise_findings"] == 1
    hit = canary.score(key, seal, [{"file": "repo/a.py", "line": 11, "claim": "x"}])
    assert hit["found"] == 1


def test_canary_same_file_rules():
    assert canary.same_file("pkg/a.py", "pkg/a.py")
    assert canary.same_file("./pkg/a.py", "pkg\\a.py")
    assert canary.same_file("repo/pkg/a.py", "pkg/a.py")
    assert not canary.same_file("src/data.py", "a.py")
    assert not canary.same_file("pkg/xa.py", "a.py")


def test_canary_load_findings_keeps_dot_directories(tmp_path):
    p = tmp_path / "findings.json"
    p.write_text(json.dumps([{"file": ".github/ci.py", "line": 3}, {"file": "./a.py", "line": 1},
                             {"file": "../up.py", "line": 2}]), encoding="utf-8")
    got = [f["file"] for f in canary.load_findings(p)]
    assert got == [".github/ci.py", "a.py", "../up.py"]    # lstrip("./") used to give github/ci.py and up.py


# ---------------------------------------------------------------------------- metadata
def test_package_version_matches_pyproject():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == sealeval.__version__
