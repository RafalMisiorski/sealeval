"""A 30-second, zero-dependency, offline tour of sealeval. Run: python -m sealeval.demo

Everything here is deterministic and runs with no network, no API key and no LLM. The judge
labels in step 4 are FIXTURES, clearly marked -- this demo shows the machinery, it is not a
measurement of anything. ASCII-only so it renders in every terminal.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from sealeval.measure import panel, round0_gate, stats
from sealeval.sealing import prereg


def _rule(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


CLEAN_SRC = '''\
def average(values):
    total = 0
    for v in values:
        total += v
    return total / len(values)


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''


def main() -> int:
    print("sealeval demo -- deterministic, offline, no LLM required.")

    # ---------------------------------------------------------------- 1. SEED
    _rule("1. SEED -- inject sealed ground-truth bugs into clean source")
    from sealeval.mutation import catalog, seeder
    with tempfile.TemporaryDirectory() as td:
        root, out = Path(td) / "clean", Path(td) / "corpus"
        root.mkdir()
        (root / "mod.py").write_text(CLEAN_SRC, encoding="utf-8")
        res = seeder.seed_corpus(root, out, files=[Path("mod.py")],
                                 archetypes=catalog.MVP_ARCHETYPES, per_file=2, seed=11)
        print("injected %d bug(s); the KEY is the ground truth:" % res.mutations)
        for r in res.records:
            print("   %s:L%d  %-20s %r -> %r"
                  % (r.file, r.line, r.archetype, r.original_segment, r.mutated_segment))
        print("Deterministic: same (files, archetypes, seed) -> byte-identical corpus + key.")

    # ------------------------------------------------------- 2. ROUND 0 (fails)
    _rule("2. ROUND 0 -- validate the APPARATUS before sealing (this one FAILS)")
    bad = round0_gate(control_labels={"obvious": "BUG"}, control_truth={"obvious": "BUG"},
                      baseline_rate=0.93, attempted=20, parsed=20, leak_controlled=True,
                      strict=True)
    print("verdict:", bad["verdict"])
    for b in bad["blocking"]:
        print("   BLOCK:", b)
    print("-> the baseline sits at 0.93, so a lift cannot show. A NULL here would be")
    print("   CEILING-LIMITED: uninformative, NOT evidence of no effect.")

    # -------------------------------------------------- 3. ROUND 0 (finer metric)
    _rule("3. Pick a FINER metric, re-run Round 0 -- now it passes")
    good = round0_gate(control_labels={"obvious": "BUG"}, control_truth={"obvious": "BUG"},
                       baseline_rate=0.31, attempted=20, parsed=20, leak_controlled=True,
                       strict=True)
    print("verdict:", good["verdict"], "| metric:", good["checks"]["metric"]["verdict"],
          "headroom", good["checks"]["metric"]["headroom"])

    # ------------------------------------------------------------- 4. MEASURE
    _rule("4. MEASURE -- blind cross-family panel (labels below are FIXTURES)")
    judge_labels = {                       # <- fixtures, not a real measurement
        "vendor_a": ["F", "C", "F", "F", "C", "F", "C", "F"],
        "vendor_b": ["F", "C", "F", "C", "C", "F", "C", "F"],
    }
    report = panel.evaluate(
        n_cases=8, judge_labels=judge_labels,
        families={"vendor_a": "anthropic", "vendor_b": "openai"},
        ground_truth=["F", "C", "F", "F", "C", "F", "C", "C"],
        control_judge_labels={"vendor_a": ["F", "C"], "vendor_b": ["F", "C"]},
        control_truth={"pos": "F", "neg": "C"},
    )
    for line in panel.report_lines(report):
        print("  " + line)
    print("\n  Wilson CI does not degenerate at k==n:  8/8 ->", stats.wilson_ci(8, 8))
    print("  (a point estimate of '8/8 = 100%' would have hidden that uncertainty)")

    # --------------------------------------------------------------- 5. SEAL
    _rule("5. SEAL -- freeze the protocol; Round 0 is bound into the hash")
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "prereg.lock.json"
        content = {"primary": "file-level recall", "keep_iff": "lift >= 0.10 and CI-lower > 0"}
        prereg.freeze(content, lock, round0=good)
        print("frozen. verify() ->", prereg.verify(lock))
        print("round0 receipt inside the sealed content ->",
              prereg.load(lock)["content"]["round0"]["round0_verdict"])

        try:                                   # freezing on a BROKEN apparatus is refused
            prereg.freeze(content, Path(td) / "x.json", round0=bad)
        except prereg.PreRegError as exc:
            print("refused to seal the ceiling'd design ->", str(exc)[:72] + "...")

        data = prereg.load(lock)               # tamper with the frozen content
        data["content"]["keep_iff"] = "lift >= 0.02"     # moved goalpost
        lock.write_text(__import__("json").dumps(data), encoding="utf-8")
        print("after moving the goalpost, verify() ->", prereg.verify(lock), "<- drift caught")

    _rule("What this bought you")
    print("- ground truth fixed BEFORE the run, and provably not edited after")
    print("- a metric that can actually reveal an effect (Round 0 caught the ceiling)")
    print("- controls proving the judge discriminates, kappa proving judges agree")
    print("- an interval instead of a flattering point estimate")
    print("\nDocs: src/sealeval/measure/README.md   Tests: python -m pytest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
