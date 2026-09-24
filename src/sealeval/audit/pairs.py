"""Matched pairs with a seeded side flip, and the NULL control that proves the panel does not
manufacture differences.

Why this exists. "Which of these two survived?" is a clean binary task, but two things break it
silently. First, the positive item must not sit on a predictable side (always A) or in a
predictable order; the flip is seeded and recorded. Second, a panel asked to pick a winner will
pick one even when there is no difference. NULL pairs -- two items with the SAME outcome, one
side arbitrarily declared "positive" -- measure that: over enough null pairs the panel's
accuracy must straddle 0.5. In an internal run (2026-09) the null control came out at 0.564 with
a Wilson interval [0.41, 0.71]: the panel was not inventing differences, so the (failed) result on
real pairs could be read as a result about the judgment, not about the instrument.

Zero dependencies. You bring the judge call; this module builds pairs and scores picks.
"""
from __future__ import annotations

import random
from typing import Callable, Mapping, Optional, Sequence

from sealeval.measure.stats import wilson_ci


def matched_pairs(positives: Sequence[Mapping], negatives: Sequence[Mapping], *, key: Callable[[Mapping], object],
                  seed: int, cap: Optional[int] = None, fallback: Optional[Callable[[Mapping, Mapping], bool]] = None,
                  id_key: str = "id") -> list:
    """Pair each positive with an unused negative that matches ``key`` (exact); then, for the
    positives still unpaired, with a negative accepted by ``fallback(pos, neg)`` if given.

    Deterministic for a seed: inputs are sorted by id, then shuffled once. Each pair records
    which side holds the positive (``positive_side``) after a seeded coin flip.
    """
    rng = random.Random(int(seed))
    pos = sorted(positives, key=lambda e: str(e[id_key]))
    neg = sorted(negatives, key=lambda e: str(e[id_key]))
    rng.shuffle(pos)
    rng.shuffle(neg)
    used, pairs = set(), []
    limit = cap if cap is not None else len(pos)

    def take(p, n):
        pairs.append((p, n))
        used.update({p[id_key], n[id_key]})

    for p in pos:
        if len(pairs) >= limit:
            break
        for n in neg:
            if n[id_key] in used:
                continue
            if key(p) == key(n):
                take(p, n)
                break
    if fallback is not None:
        for p in pos:
            if len(pairs) >= limit or p[id_key] in used:
                continue
            for n in neg:
                if n[id_key] in used:
                    continue
                if fallback(p, n):
                    take(p, n)
                    break
    rows = []
    for i, (p, n) in enumerate(pairs):
        flip = rng.random() < 0.5
        a, b = (n, p) if flip else (p, n)
        rows.append({"pair_id": "p%03d" % (i + 1), "A": a[id_key], "B": b[id_key],
                     "positive_side": "B" if flip else "A", "kind": "real", "match": str(key(p))})
    return rows


def null_pairs(items: Sequence[Mapping], *, n: int, seed: int, id_key: str = "id") -> list:
    """Pairs of items with the SAME outcome; ``positive_side`` is a seeded coin flip with no
    meaning. Use the items left over after ``matched_pairs`` (or any same-outcome pool)."""
    rng = random.Random(int(seed) + 1)
    pool = sorted(items, key=lambda e: str(e[id_key]))
    rng.shuffle(pool)
    rows = []
    for i in range(0, min(len(pool) - 1, 2 * int(n)), 2):
        a, b = pool[i], pool[i + 1]
        rows.append({"pair_id": "n%03d" % (len(rows) + 1), "A": a[id_key], "B": b[id_key],
                     "positive_side": rng.choice(["A", "B"]), "kind": "null"})
    return rows


def score_pairs(pairs: Sequence[Mapping], picks_by_judge: Mapping[str, Mapping[str, Optional[str]]]) -> dict:
    """Per-judge accuracy on ``pairs`` (pick == positive_side) with Wilson intervals, the mean,
    and the pooled interval. A missing pick is not counted (and is reported)."""
    per = {}
    pk = pn = 0
    for j, picks in picks_by_judge.items():
        k = n = 0
        for p in pairs:
            pick = picks.get(p["pair_id"])
            if pick not in ("A", "B"):
                continue
            n += 1
            k += int(pick == p["positive_side"])
        per[j] = {"correct": k, "judged": n, "missing": len(pairs) - n, "accuracy": round(k / n, 4) if n else None,
                  "ci95": wilson_ci(k, n)}
        pk += k
        pn += n
    accs = [v["accuracy"] for v in per.values() if v["accuracy"] is not None]
    return {"n_pairs": len(pairs), "per_judge": per, "mean_accuracy": round(sum(accs) / len(accs), 4) if accs else None,
            "pooled": {"correct": pk, "judged": pn, "accuracy": round(pk / pn, 4) if pn else None, "ci95": wilson_ci(pk, pn)}}


def null_control(nulls: Sequence[Mapping], picks_by_judge: Mapping[str, Mapping[str, Optional[str]]]) -> dict:
    """Pooled accuracy on NULL pairs. ``ok`` iff the Wilson interval straddles 0.5 (the panel does
    not manufacture differences). With no judged null pairs ``ok`` is None -- unverified, not passed."""
    k = n = 0
    for picks in picks_by_judge.values():
        for p in nulls:
            pick = picks.get(p["pair_id"])
            if pick not in ("A", "B"):
                continue
            n += 1
            k += int(pick == p["positive_side"])
    ci = wilson_ci(k, n)
    ok = None if not n else bool(ci and ci[0] <= 0.5 <= ci[1])
    return {"k": k, "n": n, "accuracy": round(k / n, 4) if n else None, "ci95": ci, "ok": ok,
            "reason": None if ok else ("no null pairs judged" if not n else "panel picks a side on identical-outcome pairs")}


def agreement(pairs: Sequence[Mapping], picks_by_judge: Mapping[str, Mapping[str, Optional[str]]]) -> Optional[float]:
    """Share of pairs on which every judge who answered picked the same side (None if < 2 judges)."""
    js = list(picks_by_judge)
    if len(js) < 2:
        return None
    n = same = 0
    for p in pairs:
        picks = [picks_by_judge[j].get(p["pair_id"]) for j in js]
        picks = [x for x in picks if x in ("A", "B")]
        if len(picks) < 2:
            continue
        n += 1
        same += int(len(set(picks)) == 1)
    return round(same / n, 4) if n else None


def report_lines(scored: Mapping, null: Optional[Mapping] = None) -> list:
    L = ["pairs: %d judged; mean accuracy %s; pooled %s %s" % (
        scored.get("n_pairs", 0), _fmt(scored.get("mean_accuracy")), _fmt(scored["pooled"]["accuracy"]),
        _ci(scored["pooled"]["ci95"]))]
    for j, r in sorted(scored.get("per_judge", {}).items()):
        L.append("  judge %s: %d/%d correct %s%s" % (j, r["correct"], r["judged"], _ci(r["ci95"]),
                                                     " (%d missing)" % r["missing"] if r["missing"] else ""))
    if null is not None:
        L.append("  null control: %d/%d %s -> %s" % (null["k"], null["n"], _ci(null["ci95"]),
                                                    "UNVERIFIED" if null["ok"] is None else ("OK" if null["ok"] else "FAIL: " + str(null["reason"]))))
    return L


def _fmt(x) -> str:
    return "n/a" if x is None else "%.3f" % x


def _ci(ci) -> str:
    return "" if not ci else "[%.2f, %.2f]" % (ci[0], ci[1])
