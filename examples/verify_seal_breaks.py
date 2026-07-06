#!/usr/bin/env python3
"""The differentiator in ~40 lines: a sealed ground truth you cannot move after
seeing results. Zero dependencies, deterministic, <1s. Run:  python examples/verify_seal_breaks.py

Two independent tamper-checks:
  1. keyseal   -- the sha256 commitment over the injected-bug key. Verifies True on the
                  exact key, False the instant a single byte moves (line 3 -> line 4).
  2. prereg    -- the self-hashing lock over your whole protocol (thresholds, hypotheses).
                  Detects a post-hoc edit to a GO/KILL threshold after results are in.

If either check can be made to pass on tampered data, the seal is worthless -- so the
whole point is that they CAN'T. That is what a verdict has to survive to be defensible.
"""
import sys
import tempfile
from pathlib import Path

# Zero-dep: run straight from the source tree without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sealeval import make_seal, verify_seal          # noqa: E402
from sealeval.sealing import prereg                   # noqa: E402


def check(label, got, want):
    status = "OK " if got == want else "FAIL"
    print(f"  [{status}] {label}: got {got!r}, want {want!r}")
    return got == want


def main():
    ok = True
    print("1. keyseal -- sha256 commitment over the sealed bug key")
    # The ground truth: one injected off-by-one bug at a.py:3. Sealed BEFORE any system runs.
    key = [{"file": "a.py", "line": 3, "archetype": "off_by_one"}]
    seal = make_seal(key, salt="deadbeef")            # fixed salt => reproducible digest
    print(f"     committed seal: {seal['seal'][:16]}...  (this is what you git-commit pre-run)")
    ok &= check("exact key verifies", verify_seal(key, seal), True)
    moved = [{"file": "a.py", "line": 4, "archetype": "off_by_one"}]  # moved one line
    ok &= check("key moved 1 line is rejected", verify_seal(moved, seal), False)

    print("2. prereg -- self-hashing lock over the protocol (catches moved goalposts)")
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "prereg.lock.json"
        content = {"hypotheses": {"H1": "system beats baseline"},
                   "thresholds": {"go_ratio": 0.33}}     # GO iff >=33% confirmed
        prereg.freeze(content, lock, frozen_at="2026-06-20T00:00:00Z")
        ok &= check("untampered lock verifies", prereg.verify(lock), True)
        # Post-hoc goalpost move: quietly relax the GO threshold after seeing the numbers.
        d = prereg.load(lock)
        d["content"]["thresholds"]["go_ratio"] = 0.99
        lock.write_text(__import__("json").dumps(d), encoding="utf-8")
        ok &= check("threshold edited after the fact is caught", prereg.verify(lock), False)

    print()
    if ok:
        print("ALL CHECKS PASSED -- the seal holds the ground truth and rejects every tamper.")
        return 0
    print("A CHECK FAILED -- the seal did not behave as claimed. Do not trust the verdict.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
