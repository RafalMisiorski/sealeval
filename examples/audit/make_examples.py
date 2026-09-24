#!/usr/bin/env python3
"""Regenerates the example CSVs in this directory deterministically (seeded, stdlib only).

The examples are SYNTHETIC: they exist so that `sealeval audit <cmd> examples/audit/<file>` runs
offline and prints a number you can reproduce. They are shaped like the real runs the modules were
extracted from, but no number here is a measurement of anything outside this directory.
"""
from __future__ import annotations

import csv
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))


def w(name: str, header: list, rows: list) -> None:
    with open(os.path.join(HERE, name), "w", newline="", encoding="utf-8") as fh:
        cw = csv.writer(fh)
        cw.writerow(header)
        cw.writerows(rows)


def leak() -> None:
    # 20 labelled entities, 2 judges. 7 leak: 6 KNOWN to at least one judge, 2 exact-title notable (one overlaps).
    known_a = {"c01", "c03", "c07", "c15"}
    known_b = {"c01", "c07", "c12", "c19"}
    notable = {"c03", "c11"}
    rows = []
    for i in range(1, 21):
        cid = "c%02d" % i
        rows.append([cid, 1, int(cid in notable), "KNOWN" if cid in known_a else "UNKNOWN", "KNOWN" if cid in known_b else "UNKNOWN"])
    w("leak.csv", ["id", "labelled", "notable", "judge_a", "judge_b"], rows)


def controls() -> None:
    # 9 sample rows in 3 strata + 3 hidden controls. Judge j3 gets all three controls wrong -> VOID.
    rows = [
        ["e001", "sample", "engine_only", "NO", "NO", "YES"],
        ["e002", "control_pos", "engine_only", "YES", "YES", "NO"],
        ["e003", "sample", "search_only", "YES", "YES", "YES"],
        ["e004", "sample", "engine_only", "NO", "UNCLEAR", "YES"],
        ["e005", "sample", "multi", "YES", "YES", "YES"],
        ["e006", "control_neg", "search_only", "NO", "NO", "YES"],
        ["e007", "sample", "engine_only", "NO", "NO", "YES"],
        ["e008", "sample", "search_only", "YES", "NO", "YES"],
        ["e009", "control_pos", "multi", "YES", "YES", "NO"],
        ["e010", "sample", "multi", "YES", "YES", "YES"],
        ["e011", "sample", "engine_only", "YES", "NO", "YES"],
        ["e012", "sample", "search_only", "YES", "YES", ""],
    ]
    w("controls.csv", ["id", "role", "stratum", "j1", "j2", "j3"], rows)


def pairs_csv() -> None:
    rng = random.Random(20260924)
    rows = []
    for i in range(1, 11):                      # 10 real pairs: judge a 7/10, judge b 6/10
        side = rng.choice(["A", "B"])
        other = "B" if side == "A" else "A"
        rows.append(["p%03d" % i, "real", side, side if i <= 7 else other, side if i not in (2, 5, 8, 10) else other])
    for i in range(1, 7):                       # 6 null pairs: pooled 6/12 -> straddles 0.5
        side = rng.choice(["A", "B"])
        other = "B" if side == "A" else "A"
        rows.append(["n%03d" % i, "null", side, side if i % 2 else other, other if i % 2 else side])
    w("pairs.csv", ["pair_id", "kind", "positive_side", "judge_a", "judge_b"], rows)


def finders() -> None:
    # 3 arms over 60 candidate ids: search finds 45, manual 11 (5 shared with search), engine 0.
    rows = []
    for i in range(1, 61):
        cid = "m%02d" % i
        rows.append([cid, 0, int(i <= 45), int(i <= 5 or 46 <= i <= 51)])
    w("finders.csv", ["id", "engine", "search", "manual"], rows)


def temporal_csv() -> None:
    # 90 rows: train 2014-2016 (54), test 2017-2018 (36). `year` defines the split (disjoint supports).
    rng = random.Random(7)
    rows = []
    for i in range(90):
        split = "train" if i < 54 else "test"
        year = (2014 + i % 3) if split == "train" else (2017 + i % 2)
        has_pricing = int(rng.random() < 0.55)
        log_chars = round(rng.uniform(6.0, 9.5), 2)
        p_event = 0.12 + 0.20 * (1 - has_pricing) + 0.02 * (9.0 - log_chars)
        y = int(rng.random() < p_event)
        score = "" if split == "train" else round(min(0.98, max(0.02, 0.55 + 0.25 * (1 - has_pricing) + rng.gauss(0, 0.12))), 3)
        rows.append([split, y, score, year, has_pricing, log_chars])
    w("temporal.csv", ["split", "y", "score", "year", "has_pricing", "log_chars"], rows)


def review_dir() -> None:
    d = os.path.join(HERE, "review")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "RESULT.md"), "w", encoding="utf-8").write(
        "# Sealed result (example)\n\nVerdict NOT_SHOWN under R6. test AUC 0.5682 [0.4262, 0.7199], permutation p 0.2886, "
        "Brier 0.4213 vs base-rate 0.1728, dead in test 19 (rule requires 30). Dumb heuristic AUC 0.6182.\n")
    open(os.path.join(d, "PREREG.md"), "w", encoding="utf-8").write(
        "# Pre-registration (example)\n\nR6: WORTH_USING iff test AUC >= 0.70 AND bootstrap lower bound > 0.60 AND permutation p < 0.01 "
        "AND Brier below base rate AND >= 30 events in test. Decision link: NOT_SHOWN -> capture tests only.\n")
    open(os.path.join(d, "GUARDS.md"), "w", encoding="utf-8").write(
        "RIGOR-INVERSION GUARD: a non-pre-registered result may never overturn a sealed verdict; it may only raise a new sealable test.\n"
        "PLATFORM-ABSORPTION GUARD: no multi-week build on an unmeasured premise.\n")
    good = {"measurement": "review",
            "verdict_restated": "Sealed NOT_SHOWN: test AUC 0.5682 [0.4262, 0.7199], permutation p 0.2886, 19 events in test against the required 30.",
            "decides": [{"claim": "Under the frozen R6 the model is NOT_SHOWN: AUC 0.5682 is below 0.70 and p 0.2886 is above 0.01.", "evidence": "RESULT.md"},
                        {"claim": "The decision link fires: capture tests only.", "evidence": "PREREG.md"}],
            "does_not_decide": [{"claim": "That the signals carry no information in general.", "why": "19 events is an underpowered null, not a demonstrated absence."},
                                {"claim": "That the heuristic at 0.6182 is usable.", "why": "It is a descriptive readout on the same underpowered cohort."}],
            "moves": [{"rank": 1, "title": "Record the verdict and close the question", "cost": "2 hours", "decides": "the durable record",
                       "needs_prereg": False, "owner": "team", "blocked_by": ""},
                      {"rank": 2, "title": "Registry-label feasibility probe", "cost": "1 day", "decides": "whether authoritative labels are reachable at zero cost",
                       "needs_prereg": False, "owner": "team", "blocked_by": "owner decision 1"}],
            "operator_decisions": [{"question": "Close the question permanently, or authorize the free label probe first?",
                                    "options": ["close", "probe first"], "default_if_silent": "probe first",
                                    "why_operator": "spending another cycle on the same question is a priority call", "default_move_rank": 2}],
            "guards": {"rigor_inversion": "the sealed NOT_SHOWN stands; the heuristic is inadmissible for any KEEP",
                       "platform_absorption": "no build proposed; a one-day probe gated on an owner decision"},
            "confidence": 0.85}
    bad = json.loads(json.dumps(good))
    bad["verdict_restated"] = "Sealed NOT_SHOWN: AUC roughly 0.57, about 20% of the required events."   # 0.57 and 20 are invented
    bad["decides"][0]["evidence"] = "analysis/notes.md"                                                   # not in the bundle
    bad["moves"].append({"rank": 3, "title": "Rebuild the pipeline as a platform", "cost": "3 weeks", "decides": "everything",
                         "needs_prereg": "later", "owner": "ceo", "blocked_by": ""})                       # bad flag + owner
    json.dump(good, open(os.path.join(d, "review_ok.json"), "w", encoding="utf-8"), indent=2)
    json.dump(bad, open(os.path.join(d, "review_bad.json"), "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    leak()
    controls()
    pairs_csv()
    finders()
    temporal_csv()
    review_dir()
    print("examples written to", HERE)
