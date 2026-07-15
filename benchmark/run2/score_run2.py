#!/usr/bin/env python3
"""Recompute every run2 number from findings + panel verdicts + revealed key. Stdlib only.

Usage: python score_run2.py   (from benchmark/run2/; prints the scores dict)
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("fable5", "gpt56sol")
JUDGES = ("fable5", "gpt56sol")
LINE_TOLERANCE = 1


def _match(claim: dict, key: list[dict]) -> str | None:
    for rec in key:
        if claim.get("file") == rec["file"] and abs(int(claim.get("line", 0)) - rec["line"]) <= LINE_TOLERANCE:
            return f"{rec['file']}:{rec['line']}"
    return None


def main() -> int:
    key = json.loads((HERE / "key.json").read_text(encoding="utf-8"))
    total_inj = len(key)
    arm_stats: dict[str, dict] = {}
    caught: dict[str, set] = {}
    all_claims: dict[str, list[dict]] = {}
    disagree = {"n": 0, "of": 0}

    for arm in ARMS:
        claims = json.loads((HERE / "findings" / f"{arm}.json").read_text(encoding="utf-8"))
        verdicts = {}
        for judge in JUDGES:
            rep = json.loads((HERE / "findings" / f"{arm}_verdicts_{judge}.json").read_text(encoding="utf-8"))
            verdicts[judge] = {v["claim_id"]: v["verdict"] for v in rep["verdicts"]}
        tp = 0
        arm_caught: set[str] = set()
        for c in claims:
            vs = [verdicts[j].get(c["id"], "REFUTED") for j in JUDGES]
            disagree["of"] += 1
            if len(set(vs)) > 1:
                disagree["n"] += 1
            c["confirmed"] = all(v == "GENUINE_BUG" for v in vs)
            c["matched_injection"] = _match(c, key) if c["confirmed"] else None
            if c["matched_injection"]:
                tp += 1
                arm_caught.add(c["matched_injection"])
        caught[arm], all_claims[arm] = arm_caught, claims
        arm_stats[arm] = {"claims": len(claims), "tp_claims": tp,
                          "precision": round(tp / len(claims), 3) if claims else None,
                          "recall": round(len(arm_caught) / total_inj, 3)}

    merged: dict[tuple, dict] = {}
    for arm in ARMS:
        for c in all_claims[arm]:
            k = (c["file"], c["line"])
            prev = merged.get(k)
            if prev is None or (c["matched_injection"] and not prev["matched_injection"]) \
                    or (c["confirmed"] and not prev["confirmed"]):
                merged[k] = c
    union = list(merged.values())
    u_tp = sum(1 for c in union if c["matched_injection"])
    u_caught = {c["matched_injection"] for c in union if c["matched_injection"]}
    arm_stats["union"] = {"claims": len(union), "tp_claims": u_tp,
                          "precision": round(u_tp / len(union), 3) if union else None,
                          "recall": round(len(u_caught) / total_inj, 3)}

    only_a = sorted(caught["fable5"] - caught["gpt56sol"])
    only_b = sorted(caught["gpt56sol"] - caught["fable5"])
    ra, rb = arm_stats["fable5"]["recall"], arm_stats["gpt56sol"]["recall"]
    rc, pc = arm_stats["union"]["recall"], arm_stats["union"]["precision"]
    h1 = len(only_a) >= 3 and len(only_b) >= 3
    h2 = rc >= max(ra, rb) + 0.10 and (pc or 0) >= 0.50
    kill = (rc - max(ra, rb) < 0.05) or (pc or 0) < 0.40

    out = {
        "total_injections": total_inj,
        "arms": arm_stats,
        "complementarity": {"only_fable5": only_a, "only_gpt56sol": only_b},
        "judge_disagreement_rate": round(disagree["n"] / disagree["of"], 3) if disagree["of"] else None,
        "H1_two_sided_complementarity": h1,
        "H2_union_lift": h2,
        "decision": "GO" if h2 else ("KILL" if kill else "INCONCLUSIVE"),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
