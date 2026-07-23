"""`sealeval check` -- ten seconds, zero integration, on numbers you already have.

The rest of this package assumes you will seed a corpus and wire a judge. That is a big ask for
someone who just wants to know whether their eval number means anything. This entry point works
on what a person already has in front of them: a score, or a CSV of judge labels.

    sealeval check --accuracy 8/8
    sealeval check --labels judges.csv
    sealeval check --baseline 0.93

ASCII-only so it renders in any terminal. Zero dependencies.
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import Optional

from sealeval.measure import panel, stats


def _bar(title: str) -> None:
    print("\n" + title)
    print("-" * max(28, len(title)))


def _check_accuracy(spec: str) -> int:
    try:
        k_s, n_s = spec.replace(" ", "").split("/")
        k, n = int(k_s), int(n_s)
    except Exception:
        print("could not read %r -- expected something like 8/8 or 17/20" % spec)
        return 2
    if n <= 0 or k < 0 or k > n:
        print("nonsensical score %d/%d" % (k, n))
        return 2

    lo, hi = stats.wilson_ci(k, n)
    point = k / n
    _bar("Your score: %d/%d = %.0f%%" % (k, n, point * 100))
    print("  Wilson 95%% CI:  [%.2f, %.2f]" % (lo, hi))
    print("  Honest reading: with n=%d you can claim the true rate is at least %.0f%%," % (n, lo * 100))
    print("                  not %.0f%%. The point estimate is the most flattering number" % (point * 100))
    print("                  the data allows, which is why it is the one people quote.")
    if k == n:
        print("\n  Note: a perfect score does NOT give a perfect interval. %d/%d still" % (k, n))
        print("  admits a true rate as low as %.0f%%. Bootstrap would have told you" % (lo * 100))
        print("  [1.00, 1.00] here -- that is the failure Wilson exists to avoid.")
    if lo <= 0.5 <= hi:
        print("\n  WARNING: the interval straddles 0.5 -- this is indistinguishable from")
        print("  a coin flip. Reporting it as evidence would not survive a skeptic.")
    need = None
    for cand in (10, 20, 30, 50, 100, 200, 500):
        if cand > n and stats.wilson_ci(round(point * cand), cand)[0] >= 0.8:
            need = cand
            break
    if need and lo < 0.8:
        print("\n  To support 'at least 80%%' at this rate you need roughly n=%d." % need)
    return 0


def _check_baseline(rate: float) -> int:
    g = stats.metric_discrimination_gate(rate)
    _bar("Baseline on your metric: %.3f" % rate)
    print("  headroom: %.3f   verdict: %s" % (g["headroom"], g["verdict"]))
    print("  %s" % g["note"])
    if g["ceiling_limited"]:
        print("\n  This matters BEFORE you run anything: if your baseline is already near")
        print("  the top of the metric, a null result tells you nothing -- it cannot")
        print("  distinguish 'no effect' from 'no room to show one'. Pick a finer metric.")
    return 0


def _check_labels(path: str, families: Optional[str]) -> int:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        print("cannot read %s: %s" % (path, exc))
        return 2
    if not rows:
        print("%s has no rows" % path)
        return 2

    cols = [c for c in rows[0] if c]
    judges = [c for c in cols if c not in ("truth", "tool", "control")]
    if len(judges) < 2:
        print("need at least 2 judge columns (found: %s)." % ", ".join(judges or ["none"]))
        print("CSV format: one column per judge, one row per case; optional 'truth'/'tool';")
        print("a non-empty 'control' names an obvious case and needs 'truth' filled in.")
        return 2

    # Rows whose `control` cell is non-empty are CONTROLS, not cases: they feed the VOID gate.
    # (Earlier this column was merely excluded from the judge list and then ignored, so the tool
    # kept telling people to add controls and did nothing with them when they did.)
    ctrl_rows = [r for r in rows if (r.get("control") or "").strip()] if "control" in cols else []
    case_rows = [r for r in rows if not (r.get("control") or "").strip()] if ctrl_rows else rows
    ctrl_labels = ctrl_truth = None
    if ctrl_rows:
        if "truth" not in cols or not all((r.get("truth") or "").strip() for r in ctrl_rows):
            print("control rows need a filled 'truth' cell -- that IS the obvious answer.")
            return 2
        ctrl_labels = {j: [r[j].strip() for r in ctrl_rows] for j in judges}
        ctrl_truth = {r["control"].strip(): r["truth"].strip() for r in ctrl_rows}

    rows = case_rows
    if not rows:
        print("all rows are controls -- add at least one real case")
        return 2
    labels = {j: [r[j].strip() for r in rows] for j in judges}
    truth = [r["truth"].strip() for r in rows] if "truth" in cols else None
    tool = [r["tool"].strip() for r in rows] if "tool" in cols else None
    fam = {}
    if families:
        for pair in families.split(","):
            if ":" in pair:
                j, f = pair.split(":", 1)
                fam[j.strip()] = f.strip()
    # unknown vendor -> "?" for everyone, so cross_family reads False (UNCONFIRMED, not proven)
    report = panel.evaluate(n_cases=len(rows), judge_labels=labels,
                            families=fam or {j: "?" for j in judges},
                            tool_labels=tool, ground_truth=truth,
                            control_judge_labels=ctrl_labels, control_truth=ctrl_truth)
    _bar("%d cases, %d judges" % (len(rows), len(judges)))
    for line in panel.report_lines(report):
        print("  " + line)
    if not fam:
        print("\n  No families given, so same-family bias is UNCHECKED. If both judges are")
        print("  from one vendor they share taste, and their agreement overstates confidence.")
        print("  Re-run with:  --families %s" % ",".join("%s:vendor" % j for j in judges))
    if report.get("controls") is None:
        print("\n  No controls in this file -- the VOID gate (step 1 of the standard) did not run.")
        print("  Add rows naming an obvious case in 'control' with its answer in 'truth':")
        print("    %s,truth,control" % ",".join(judges))
        print("    %s,F,obvious_positive" % ",".join(["F"] * len(judges)))
        print("  If the judges miss those, the run is VOID and nothing else counts.")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sealeval",
        description="Check whether an evaluation number actually supports what you want to claim.")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="ten-second sanity check on numbers you already have")
    c.add_argument("--accuracy", metavar="K/N", help="a score, e.g. 8/8 -- prints the honest interval")
    c.add_argument("--baseline", type=float, metavar="RATE",
                   help="baseline rate on your metric -- is there headroom to show an effect?")
    c.add_argument("--labels", metavar="CSV",
                   help="CSV of judge labels (one column per judge; optional truth/tool)")
    c.add_argument("--families", metavar="j:vendor,...", help="judge -> vendor, to check cross-family")
    sub.add_parser("demo", help="the 30-second offline tour")

    args = ap.parse_args(argv)
    if args.cmd == "demo":
        from sealeval.demo import main as demo_main
        return demo_main()
    if args.cmd != "check":
        ap.print_help()
        return 0
    if not (args.accuracy or args.labels or args.baseline is not None):
        c.print_help()
        print("\nExamples:\n  sealeval check --accuracy 8/8\n"
              "  sealeval check --baseline 0.93\n  sealeval check --labels judges.csv")
        return 0
    rc = 0
    if args.accuracy:
        rc |= _check_accuracy(args.accuracy)
    if args.baseline is not None:
        rc |= _check_baseline(args.baseline)
    if args.labels:
        rc |= _check_labels(args.labels, args.families)
    return rc


if __name__ == "__main__":
    sys.exit(main())
