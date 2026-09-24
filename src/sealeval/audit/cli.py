"""`sealeval audit` -- run the five audit questions on CSVs you already have. ASCII-only output.

    sealeval audit leak      examples/audit/leak.csv       [--max-share 0.20]
    sealeval audit controls  examples/audit/controls.csv   [--max-wrong 2] [--max-unparsed 0.20]
    sealeval audit pairs     examples/audit/pairs.csv
    sealeval audit recapture --n1 45 --n2 11 --m 5   |   sealeval audit recapture examples/audit/finders.csv
    sealeval audit temporal  examples/audit/temporal.csv   [--permutation-p 0.29] [--auc 0.70 --ci-lo 0.60 --p 0.01 --min-events 30]
    sealeval audit review    examples/audit/review --review examples/audit/review/review_ok.json

CSV shapes (header row; every column not named below is a judge / arm / feature column):
    leak:      id, [labelled 1|0], [notable 1|0], <judge>=KNOWN|UNKNOWN|blank
    controls:  id, role=sample|control_pos|control_neg, [stratum], <judge>=YES|NO|UNCLEAR|blank
    pairs:     pair_id, kind=real|null, positive_side=A|B, <judge>=A|B|blank
    finders:   id, <arm>=1|0
    temporal:  split=train|test, y=0|1, [score], <feature>...   (score = your model's P(event) on TEST rows)
    review:    a directory of bundle files (GUARDS.md, if present, becomes the guards) + a review JSON
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Optional

from sealeval.audit import controls, leakage, pairs, recapture, review, temporal


def _rows(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _judges(rows: list, fixed: tuple) -> list:
    return [c for c in (rows[0].keys() if rows else []) if c not in fixed]


def _p(lines) -> None:
    for line in lines:
        print(line)


def cmd_leak(path: str, max_share: float) -> int:
    rows = _rows(path)
    judges = _judges(rows, ("id", "labelled", "notable"))
    labelled = [r["id"] for r in rows if str(r.get("labelled", "1")).strip() in ("", "1", "true", "True")]
    notable = [r["id"] for r in rows if str(r.get("notable", "0")).strip() in ("1", "true", "True")]
    known = {}
    for j in judges:
        known[j] = {r["id"]: {"verdict": r[j].strip().upper()} for r in rows if r.get(j, "").strip().upper() in ("KNOWN", "UNKNOWN")}
    rep = leakage.leak_report(labelled, known, notable=notable)
    gate = leakage.leakage_gate(rep, max_share=max_share)
    _p(leakage.report_lines(rep, gate))
    return 0 if gate["ok"] else 1


def cmd_controls(path: str, max_wrong: int, max_unparsed: float) -> int:
    rows = _rows(path)
    judges = _judges(rows, ("id", "role", "stratum"))
    sample = [{"id": r["id"], "role": r["role"].strip(), "stratum": r.get("stratum", "") or "all"} for r in rows]
    jb = {j: {r["id"]: (r[j].strip().upper() or None) for r in rows} for j in judges}
    panel = controls.panel_void(jb, sample, max_wrong=max_wrong, max_unparsed=max_unparsed)
    _p(controls.report_lines(panel))
    if panel["run_void"]:
        return 1
    m = controls.members(jb, sample, panel["live"])
    for s, r in sorted(controls.stratum_precision(sample, m).items()):
        ci = "" if not r["ci95"] else " [%.2f, %.2f]" % r["ci95"]
        print("  stratum %s: %d/%d members, precision %s%s" % (s, r["members"], r["judged"],
                                                              "n/a" if r["precision"] is None else "%.3f" % r["precision"], ci))
    return 0


def cmd_pairs(path: str) -> int:
    rows = _rows(path)
    judges = _judges(rows, ("pair_id", "kind", "positive_side"))
    real = [r for r in rows if r["kind"].strip() == "real"]
    nulls = [r for r in rows if r["kind"].strip() == "null"]
    picks = {j: {r["pair_id"]: (r[j].strip().upper() or None) for r in rows} for j in judges}
    scored = pairs.score_pairs(real, picks)
    nc = pairs.null_control(nulls, picks)
    _p(pairs.report_lines(scored, nc))
    ag = pairs.agreement(real, picks)
    if ag is not None:
        print("  agreement between judges: %.3f" % ag)
    return 0 if nc["ok"] else 1


def cmd_recapture(path: Optional[str], n1: Optional[int], n2: Optional[int], m: Optional[int]) -> int:
    if path:
        rows = _rows(path)
        arms = _judges(rows, ("id",))
        found = {a: {r["id"] for r in rows if str(r.get(a, "0")).strip() in ("1", "true", "True")} for a in arms}
        _p(recapture.report_lines(recapture.all_pairs_recapture(found)))
        return 0
    if n1 is None or n2 is None or m is None:
        print("recapture: give a finders CSV or --n1 --n2 --m")
        return 2
    print("capture-recapture (Chapman): n1=%d n2=%d m=%d -> N_hat=%s" % (n1, n2, m, recapture.chapman(n1, n2, m)))
    return 0


def cmd_temporal(path: str, p_perm: Optional[float], thresholds: dict, min_epf: int) -> int:
    rows = _rows(path)
    feats = _judges(rows, ("split", "y", "score"))
    tr = [r for r in rows if r["split"].strip() == "train"]
    te = [r for r in rows if r["split"].strip() == "test"]

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v

    trn = [{f: num(r[f]) for f in feats} for r in tr]
    ten = [{f: num(r[f]) for f in feats} for r in te]
    guard = temporal.split_defining_features(trn, ten, feats)
    y_tr = [int(float(r["y"])) for r in tr]
    y_te = [int(float(r["y"])) for r in te]
    epf = temporal.events_per_feature(sum(y_tr), len(feats), min_epf=min_epf)
    print("temporal split: train %d rows / %d events, test %d rows / %d events, %d feature columns" % (
        len(tr), sum(y_tr), len(te), sum(y_te), len(feats)))
    if not epf["ok"]:
        print("  events per feature: %s" % epf["note"])
    if guard["refused"]:
        print("  REFUSED (split-defining, disjoint supports): %s" % ", ".join(guard["refused"]))
    scores = [float(r["score"]) for r in te if str(r.get("score", "")).strip() != ""]
    drift = None
    if len(scores) == len(te) and te:
        a = temporal.auc(y_te, scores)
        ci = temporal.bootstrap_auc_ci(y_te, scores, iters=1000, seed=7)
        bins = temporal.calibration_bins(scores, y_te, bins=5)
        drift = temporal.class_weight_drift(bins)
        base = sum(y_tr) / len(y_tr) if y_tr else 0.0
        b, bb = temporal.brier(scores, y_te), temporal.base_rate_brier(base, y_te)
        print("  test AUC %s, bootstrap 95%% %s, Brier %s vs base-rate %s, permutation p %s" % (
            _f(a), "n/a" if not ci else "[%.3f, %.3f]" % ci, _f(b), _f(bb), "not supplied (run your refits, pass --permutation-p)" if p_perm is None else _f(p_perm)))
        for bn in bins:
            print("    bin n=%d mean_p=%.3f observed=%.3f" % (bn["n"], bn["mean_p"], bn["observed"]))
        gate = temporal.temporal_gate(auc_test=a, ci_lo=None if not ci else ci[0], p_perm=p_perm, brier_test=b, brier_base=bb,
                                      n_events_test=sum(y_te), refused_features=guard["refused"], thresholds=thresholds)
    else:
        print("  no score on every test row -> the gate cannot be applied (supply your model's P(event) for TEST rows)")
        gate = temporal.temporal_gate(auc_test=None, ci_lo=None, p_perm=p_perm, brier_test=None, brier_base=None,
                                      n_events_test=sum(y_te), refused_features=guard["refused"], thresholds=thresholds)
    _p(temporal.report_lines(gate, drift=drift, shift=guard["smd"]))
    return 0 if gate["verdict"] == "WORTH_USING" else 1


def cmd_review(bundle_dir: str, review_path: str, owners: str) -> int:
    files = {}
    guards = ""
    for name in sorted(os.listdir(bundle_dir)):
        p = os.path.join(bundle_dir, name)
        if not os.path.isfile(p) or name.endswith(".json"):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        if name.upper() == "GUARDS.MD":
            guards = text
        else:
            files[name] = text
    b = review.build_bundle(os.path.basename(os.path.normpath(bundle_dir)), files, guards=guards)
    obj = json.loads(open(review_path, encoding="utf-8").read())
    if "measurement" in obj and str(obj["measurement"]).lower() != b.measurement.lower():
        b.measurement = str(obj["measurement"])        # the directory name is only a default label
    errs = review.validate(obj, b, owners=tuple(o.strip() for o in owners.split(",")))
    res = {"status": "ACCEPTED" if not errs else "REJECTED", "errors": errs, "review": obj, "bundle_sha256": b.sha256}
    print("bundle: %d files, sha256 %s" % (len(b.files), b.sha256[:12]))
    _p(review.report_lines(res))
    return 0 if not errs else 1


def _f(x) -> str:
    return "n/a" if x is None else "%.4f" % x


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="sealeval audit", description="The five questions a skeptic asks before your number counts.")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("leak", help="did the judge already know the answer?")
    a.add_argument("csv")
    a.add_argument("--max-share", type=float, default=0.20)
    c = sub.add_parser("controls", help="can the panel tell an obvious case apart?")
    c.add_argument("csv")
    c.add_argument("--max-wrong", type=int, default=2)
    c.add_argument("--max-unparsed", type=float, default=0.20)
    p = sub.add_parser("pairs", help="does the panel invent differences?")
    p.add_argument("csv")
    r = sub.add_parser("recapture", help="how much of the class did you find?")
    r.add_argument("csv", nargs="?")
    r.add_argument("--n1", type=int)
    r.add_argument("--n2", type=int)
    r.add_argument("--m", type=int)
    t = sub.add_parser("temporal", help="does an out-of-time model earn its AUC?")
    t.add_argument("csv")
    t.add_argument("--permutation-p", type=float, default=None)
    t.add_argument("--auc", type=float, default=0.70)
    t.add_argument("--ci-lo", type=float, default=0.60)
    t.add_argument("--p", type=float, default=0.01)
    t.add_argument("--min-events", type=int, default=30)
    t.add_argument("--min-epf", type=int, default=10)
    v = sub.add_parser("review", help="validate a post-seal review against its bundle")
    v.add_argument("bundle_dir")
    v.add_argument("--review", required=True)
    v.add_argument("--owners", default="operator,team,both")
    args = ap.parse_args(argv)
    if args.cmd == "leak":
        return cmd_leak(args.csv, args.max_share)
    if args.cmd == "controls":
        return cmd_controls(args.csv, args.max_wrong, args.max_unparsed)
    if args.cmd == "pairs":
        return cmd_pairs(args.csv)
    if args.cmd == "recapture":
        return cmd_recapture(args.csv, args.n1, args.n2, args.m)
    if args.cmd == "temporal":
        return cmd_temporal(args.csv, args.permutation_p, {"auc": args.auc, "ci_lo": args.ci_lo, "p": args.p, "min_events": args.min_events}, args.min_epf)
    if args.cmd == "review":
        return cmd_review(args.bundle_dir, args.review, args.owners)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
