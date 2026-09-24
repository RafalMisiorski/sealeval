"""Post-seal review: a bounded reader that cannot invent a number.

Why this exists. A sealed result is an event, not a file somebody may happen to read. The useful
thing to do with it is small and specific: say what the verdict decides, what people will
wrongly read into it, which cheap next moves the evidence supports, and which decisions belong
to the owner. An LLM is good at that -- and also at quietly computing a new percentage, citing a
file that is not there, or proposing the two-week build the result did not earn.

So the reader is bounded by construction:

* the input is a deterministic BUNDLE (labelled files, each sha-256'd, one bundle sha);
* the output is one JSON object; a deterministic validator rejects it when any number in the
  verdict or the "decides" claims does not appear verbatim in the bundle, when evidence is not a
  bundle path, when a move lacks cost/owner/prereg flag, or when an owner decision lacks a default;
* it cannot change the verdict; a rejected review is a rejected review, not a retry loop.

You bring the model call (``review_fn(prompt) -> str``). Zero dependencies.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

OWNERS = ("operator", "team", "both")
REQUIRED_GUARDS = ("rigor_inversion", "platform_absorption")
REQUIRED = ("measurement", "verdict_restated", "decides", "does_not_decide", "moves",
            "operator_decisions", "guards", "confidence")
NUM_RE = re.compile(r"(?<![\w.\-])\d+(?:[.,]\d+)*(?![\w])")

PROMPT_TEMPLATE = """You are the post-measurement reviewer. A pre-registered measurement has just been sealed and
its frozen rule applied. Your job is NOT to re-run the analysis and NOT to write the next plan. It is
to separate what the result decides from what it does not, rank the cheapest evidence-backed next
moves, and hand the owner the decisions only they can make.

Hard rules (a deterministic validator REJECTS the review on any violation):
1. Use ONLY the bundle below. Every number you write in `verdict_restated`, `decides[].claim` and
   `does_not_decide[].claim` must appear verbatim in the bundle (same digits, same decimal form).
   Do not compute new statistics, percentages or ratios.
2. A sealed verdict cannot be overturned by anything you write. You may only propose a NEW
   pre-registered test at the same bar (rigor-inversion guard).
3. Do not propose a build longer than 2 days on an unmeasured premise (platform-absorption guard).
   Cheap, on-path, evidence-backed moves first; a sealed A/B before any build.
4. Name the decisions that belong to the owner (policy, credentials, people, money, publishing).
   Prepare them with options and a default.
5. Output ONE JSON object and nothing else: no markdown fences, no prose before or after.

JSON schema (all keys required):
{
  "measurement": "<id>",
  "verdict_restated": "<one sentence; numbers only from the bundle>",
  "decides": [{"claim": "<what the result settles>", "evidence": "<a bundle file label>"}],
  "does_not_decide": [{"claim": "<what people will wrongly read into it>", "why": "<one sentence>"}],
  "moves": [{"rank": 1, "title": "<move>", "cost": "<hours or days>", "decides": "<what running it settles>",
             "needs_prereg": true, "owner": "%(owners)s", "blocked_by": "<owner decision or empty>"}],
  "operator_decisions": [{"question": "<one question>", "options": ["<a>", "<b>"], "default_if_silent": "<option>",
                          "why_operator": "<one sentence>", "default_move_rank": <rank or null>}],
  "guards": {%(guards)s},
  "confidence": 0.0
}
Limits: 1-6 decides, 1-6 does_not_decide, 1-5 moves, 0-4 operator_decisions. confidence in [0, 1].

=== BUNDLE (measurement %(measurement)s, sha256 %(bundle_sha)s) ===
%(bundle)s
=== END BUNDLE ===
"""


@dataclass
class Bundle:
    measurement: str
    files: list = field(default_factory=list)   # {"label", "sha256", "bytes"}
    text: str = ""
    sha256: str = ""

    @property
    def labels(self) -> list:
        return [f["label"] for f in self.files]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_bundle(measurement: str, files: Mapping[str, str], *, guards: str = "") -> Bundle:
    """Deterministic input pack: the same labelled contents in the same order give the same sha.
    ``files`` maps a label (a path is a good label) to its text; ``guards`` is the text of the
    rules the reviewer must respect (bound into the bundle so the reviewer and the validator see
    the same words)."""
    b = Bundle(measurement=measurement)
    parts = []

    def add(label: str, content: str) -> None:
        h = _sha(content)
        b.files.append({"label": label, "sha256": h, "bytes": len(content.encode("utf-8"))})
        parts.append("### FILE: %s (sha256 %s)\n%s\n" % (label, h[:12], content.strip()))

    for label, content in files.items():
        add(str(label).replace("\\", "/"), content)
    if guards:
        add("GUARDS", guards)
    b.text = "\n".join(parts)
    b.sha256 = _sha(b.text)
    return b


def render_prompt(bundle: Bundle, *, owners: Sequence[str] = OWNERS, required_guards: Sequence[str] = REQUIRED_GUARDS,
                  template: str = PROMPT_TEMPLATE) -> str:
    return template % {
        "owners": '" | "'.join(owners), "measurement": bundle.measurement, "bundle_sha": bundle.sha256,
        "bundle": bundle.text, "guards": ", ".join('"%s": "<how this review respects it>"' % g for g in required_guards)}


def numbers(text) -> set:
    return {m.group(0).replace(",", ".") for m in NUM_RE.finditer(str(text))}


def parse_review(raw: str) -> dict:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in output")
    obj = json.loads(t[i:j + 1])
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON is not an object")
    return obj


def validate(review: Mapping, bundle: Bundle, *, owners: Sequence[str] = OWNERS,
             required_guards: Sequence[str] = REQUIRED_GUARDS) -> list:
    """Deterministic checks. Returns the list of violations (empty = ACCEPTED)."""
    errs = [("missing key: %s" % k) for k in REQUIRED if k not in review]
    if errs:
        return errs
    if str(review["measurement"]).lower() != bundle.measurement.lower():
        errs.append("measurement mismatch: %r vs %r" % (review["measurement"], bundle.measurement))
    bnums = numbers(bundle.text)

    def check_nums(label: str, text) -> None:
        bad = sorted(numbers(text) - bnums)
        if bad:
            errs.append("%s: numbers not in bundle: %s" % (label, ", ".join(bad)))

    check_nums("verdict_restated", review["verdict_restated"])
    dec = review["decides"]
    if not isinstance(dec, list) or not 1 <= len(dec) <= 6:
        errs.append("decides: need 1-6 items")
    else:
        for i, d in enumerate(dec):
            if not isinstance(d, dict) or not d.get("claim") or not d.get("evidence"):
                errs.append("decides[%d]: claim + evidence required" % i)
                continue
            check_nums("decides[%d]" % i, d["claim"])
            ev = str(d["evidence"]).replace("\\", "/").strip()
            if not any(ev == p or ev.endswith(p) or p.endswith(ev) for p in bundle.labels):
                errs.append("decides[%d]: evidence is not a bundle label: %s" % (i, ev))
    dnd = review["does_not_decide"]
    if not isinstance(dnd, list) or not 1 <= len(dnd) <= 6:
        errs.append("does_not_decide: need 1-6 items")
    else:
        for i, d in enumerate(dnd):
            if not isinstance(d, dict) or not d.get("claim") or not d.get("why"):
                errs.append("does_not_decide[%d]: claim + why required" % i)
            else:
                check_nums("does_not_decide[%d]" % i, d["claim"])
    mv = review["moves"]
    if not isinstance(mv, list) or not 1 <= len(mv) <= 5:
        errs.append("moves: need 1-5 items")
    else:
        for i, m in enumerate(mv):
            if not isinstance(m, dict):
                errs.append("moves[%d]: not an object" % i)
                continue
            for k in ("title", "cost", "decides", "owner"):
                if not m.get(k):
                    errs.append("moves[%d]: %s required" % (i, k))
            if m.get("owner") not in owners:
                errs.append("moves[%d]: owner must be one of %s" % (i, "/".join(owners)))
            if not isinstance(m.get("needs_prereg"), bool):
                errs.append("moves[%d]: needs_prereg must be true/false" % i)
    od = review["operator_decisions"]
    if not isinstance(od, list) or len(od) > 4:
        errs.append("operator_decisions: list of 0-4 items")
    else:
        ranks = {m.get("rank") for m in (review.get("moves") or []) if isinstance(m, dict)}
        for i, d in enumerate(od):
            if (not isinstance(d, dict) or not d.get("question") or not isinstance(d.get("options"), list)
                    or not d["options"] or not d.get("default_if_silent")):
                errs.append("operator_decisions[%d]: question, options[], default_if_silent required" % i)
                continue
            dmr = d.get("default_move_rank")
            if dmr is not None and dmr not in ranks:
                errs.append("operator_decisions[%d]: default_move_rank %r is not a move rank" % (i, dmr))
    g = review["guards"]
    if not isinstance(g, dict) or any(not g.get(k) for k in required_guards):
        errs.append("guards: %s required" % " + ".join(required_guards))
    try:
        c = float(review["confidence"])
        if not 0.0 <= c <= 1.0:
            errs.append("confidence outside [0, 1]")
    except (TypeError, ValueError):
        errs.append("confidence is not a number")
    return errs


def review(bundle: Bundle, review_fn: Callable[[str], str], *, owners: Sequence[str] = OWNERS,
           required_guards: Sequence[str] = REQUIRED_GUARDS) -> dict:
    """One call, one JSON object, validated. ``status`` is ACCEPTED or REJECTED; a rejected review
    carries its violations and the raw output, never a second attempt."""
    prompt = render_prompt(bundle, owners=owners, required_guards=required_guards)
    raw = review_fn(prompt)
    try:
        obj = parse_review(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"status": "REJECTED", "errors": ["unparseable: %s" % exc], "review": None, "raw": raw,
                "bundle_sha256": bundle.sha256}
    errs = validate(obj, bundle, owners=owners, required_guards=required_guards)
    return {"status": "ACCEPTED" if not errs else "REJECTED", "errors": errs, "review": obj, "raw": raw,
            "bundle_sha256": bundle.sha256}


def report_lines(result: Mapping) -> list:
    L = ["review: %s (bundle %s)" % (result.get("status"), str(result.get("bundle_sha256", ""))[:12])]
    for e in result.get("errors") or []:
        L.append("  violation: %s" % e)
    rv = result.get("review") or {}
    if result.get("status") == "ACCEPTED":
        L.append("  verdict: %s" % rv.get("verdict_restated"))
        for m in rv.get("moves") or []:
            L.append("  move %s: %s (%s, owner %s%s)" % (m.get("rank"), m.get("title"), m.get("cost"), m.get("owner"),
                                                         ", prereg first" if m.get("needs_prereg") else ""))
        for d in rv.get("operator_decisions") or []:
            L.append("  decision: %s -> default: %s" % (d.get("question"), d.get("default_if_silent")))
    return L
