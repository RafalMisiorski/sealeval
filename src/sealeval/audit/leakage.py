"""Leakage probe -- ask the judge whether it already KNOWS the answer, before you pair or score.

Why this exists. A retrodiction test ("could the model have predicted, in 2019, which of these
companies would survive?") is only a test of judgment on entities the judge cannot remember. In
an internal run (2026-09) the pre-registered leakage probe found that 77% of the LABELLED
entities were KNOWN to at least one frontier judge: the "predictions" were memories, and one
clean pair out of ten was left. The number reported afterwards would have looked excellent.

Two rules this module encodes:

1. Probe BEFORE pairing, and let the probe define the population. Excluding "known" items after
   the judgments exist invites a silent post-hoc choice; excluding them before is a design.
2. Leak share is measured over the LABELLED population (the items that could enter the test),
   not over the items you happened to pair. The denominator is the whole class you claim to
   have tested.

You bring the judge call. This module builds the probe prompt, parses the answers, and computes
the leak report from whatever notability signal you also have (for example an exact-title hit
in an encyclopedia search). Zero dependencies.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Iterable, Mapping, Optional, Sequence

KNOWN = "KNOWN"
UNKNOWN = "UNKNOWN"

DEFAULT_RUBRIC = (
    "For each item, say whether you already KNOW this specific entity from your training data "
    "(its name, what happened to it, who ran it). Answer KNOWN only if you can recall something "
    "specific about this exact entity; answer UNKNOWN if you would be guessing from the name alone. "
    "This is a memory check, not a prediction task."
)


def normalize(s: str) -> str:
    """Case-fold, strip diacritics, drop everything that is not a letter or digit (title comparison:
    "Foo Bank S.A." and "foo bank sa" are the same title; "Foo Bank" and "Foo" are not)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def exact_title_hit(titles: Iterable[str], name: str) -> bool:
    """True when a search result title equals the entity name after normalisation.

    An exact-title hit in a general encyclopedia is a cheap, deterministic notability signal:
    a judge trained on the web has very likely seen this entity. Substring matches are NOT
    hits (``"Foo Bank" != "Foo"``) -- looser matching over-excludes and hides the leak share
    behind a smaller population.
    """
    target = normalize(name)
    if not target:
        return False
    return any(normalize(t) == target for t in titles)


def probe_prompt(items: Sequence[Mapping], rubric: str = DEFAULT_RUBRIC, *, fields: Sequence[str] = ("id", "name")) -> str:
    """The prompt a judge receives. ``items`` carry only public identifying fields (id, name,
    optionally a domain or launch month). Nothing about outcomes, arms or labels."""
    rows = [{k: it.get(k) for k in fields if k in it} for it in items]
    return ("%s\n\nItems (JSON):\n%s\n\nAnswer with a JSON array ONLY, one object per item id: "
            "[{\"id\": \"...\", \"verdict\": \"KNOWN\"|\"UNKNOWN\", \"note\": \"one line\"}]"
            % (rubric, json.dumps(rows, ensure_ascii=True)))


def _json_array(raw: str) -> list:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        arr = json.loads(t[i:j + 1])
    except ValueError:
        return []
    return arr if isinstance(arr, list) else []


def parse_probe(raw: str, ids: Iterable[str]) -> dict:
    """{id: {"verdict": KNOWN|UNKNOWN, "note": str}} for the ids answered; anything else is dropped.

    A missing id is a missing answer, never a default: refute-by-default would turn silence into
    UNKNOWN and shrink the leak share for free.
    """
    allowed = set(ids)
    out = {}
    for o in _json_array(raw):
        if not isinstance(o, dict) or o.get("id") not in allowed:
            continue
        v = str(o.get("verdict", "")).strip().upper()
        if v in (KNOWN, UNKNOWN):
            out[o["id"]] = {"verdict": v, "note": str(o.get("note", ""))[:200]}
    return out


def leak_report(labelled: Iterable[str], known_by_judge: Mapping[str, Mapping[str, Mapping]], *,
                notable: Iterable[str] = ()) -> dict:
    """The leak share over the LABELLED population and the clean population that may be paired.

    ``labelled``       ids of every item that could enter the test (has an outcome).
    ``known_by_judge`` judge -> parse_probe() output.
    ``notable``        ids with an exact-title notability hit (excluded regardless of the judges).

    An item leaks when ANY judge says KNOWN or it is notable. Items no judge answered are
    reported as ``unanswered`` and are NOT clean (unanswered is not unknown).
    """
    lab = list(dict.fromkeys(labelled))
    notable_set = set(notable)
    per_judge = {}
    known_any: set = set()
    answered_any: set = set()
    for j, answers in known_by_judge.items():
        k = {i for i, a in answers.items() if i in lab and a.get("verdict") == KNOWN}
        per_judge[j] = {"answered": sum(1 for i in answers if i in lab), "known": len(k)}
        known_any |= k
        answered_any |= {i for i in answers if i in lab}
    leaked = [i for i in lab if i in known_any or i in notable_set]
    unanswered = [i for i in lab if i not in answered_any and i not in notable_set]
    clean = [i for i in lab if i not in leaked and i not in unanswered]
    n = len(lab)
    share = round(len(leaked) / n, 4) if n else None
    return {
        "labelled": n,
        "leaked": len(leaked),
        "leak_share": share,
        "known_by_any_judge": len(known_any),
        "notable": len([i for i in lab if i in notable_set]),
        "unanswered": len(unanswered),
        "clean": len(clean),
        "clean_ids": clean,
        "leaked_ids": leaked,
        "per_judge": per_judge,
    }


def leakage_gate(report: Mapping, *, max_share: float = 0.20, min_clean: int = 1) -> dict:
    """Pre-register ``max_share`` and ``min_clean``; this only applies them.

    The gate is on the SHARE, not on the clean count alone: a run with 200 labelled items and
    150 leaked still has 50 clean items, but the claim "the judge predicted the class" is then
    about a quarter of the class the judge did not remember -- say so.
    """
    share = report.get("leak_share")
    reasons = []
    if share is None:
        reasons.append("no labelled items")
    else:
        if share > max_share:
            reasons.append("leak share %.2f exceeds the pre-registered %.2f: the judge remembers this class" % (share, max_share))
        if report.get("clean", 0) < min_clean:
            reasons.append("clean population %d below the minimum %d" % (report.get("clean", 0), min_clean))
    return {"ok": not reasons, "leak_share": share, "clean": report.get("clean"), "reasons": reasons}


def report_lines(report: Mapping, gate: Optional[Mapping] = None) -> list:
    L = ["leakage probe: %d labelled, %d leaked (share %s), %d clean, %d unanswered" % (
        report.get("labelled", 0), report.get("leaked", 0),
        "n/a" if report.get("leak_share") is None else "%.2f" % report["leak_share"],
        report.get("clean", 0), report.get("unanswered", 0))]
    for j, pj in sorted(report.get("per_judge", {}).items()):
        L.append("  judge %s: answered %d, KNOWN %d" % (j, pj["answered"], pj["known"]))
    if report.get("notable"):
        L.append("  notable (exact-title hit): %d" % report["notable"])
    if gate is not None:
        L.append("  gate: %s%s" % ("OK" if gate["ok"] else "FAIL", "" if gate["ok"] else " -- " + "; ".join(gate["reasons"])))
    return L
