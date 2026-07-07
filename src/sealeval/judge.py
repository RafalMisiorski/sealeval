"""Independent blind judge core.

A reusable, model-pluggable adjudicator: each claim is one synchronous LLM call through
the ``judge_fn`` you supply (sealeval ships no backend), so it runs headless from the CLI
and in CI against whatever model you wire in.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

# The project under evaluation. Defaults to the caller's cwd; override per-call via
# the ``scope`` argument to judge_claims (a dir or a {"root","files"} manifest).
PROJECT_ROOT = Path.cwd()

VERDICTS = ("GENUINE_BUG", "REAL_NOT_BUG", "REFUTED")

# Model id passed to your judge_fn by default. Any string your backend understands.
_DEFAULT_MODEL = "claude-opus-4-6"
_CALIBRATION_PATH = PROJECT_ROOT / "sealeval_calibration.jsonl"

# Fields that would leak provenance/answer to the judge — stripped before judging.
_STRIP_KEYS = frozenset(
    {
        "category",
        "severity",
        "src",
        "source",
        "contestant",
        "provenance",
        "confidence",
        "is_genuine_bug",
        "real",
        "verdict",
    }
)

JUDGE_SYSTEM = """You are an independent, skeptical senior engineer verifying a \
code-review claim. You are shown a CLAIM and a CODE EXCERPT from the file it refers \
to. Judge the claim ON ITS MERITS against the code shown. Try HARD to REFUTE it: \
default to REFUTED unless the described defect is concretely present and triggerable.

Respond with ONLY a JSON object — no prose, no code fences:
{
  "verdict": "GENUINE_BUG" | "REAL_NOT_BUG" | "REFUTED",
  "confidence": <number 0..1>,
  "mechanism": "<= 1 sentence citing the specific code that justifies the verdict"
}

Verdict meaning:
- GENUINE_BUG : a real correctness / security / data-loss defect, concretely present and triggerable.
- REAL_NOT_BUG: the described thing is real in the code but it is a style / design / latent-only issue, not a present defect.
- REFUTED     : the claim's mechanism or its consequence is not actually present in the code."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class JudgeVerdict:
    claim_id: str
    verdict: str
    confidence: float
    mechanism: str
    file: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JudgeReport:
    judge_model: str
    claims_judged: int
    genuine_bug: int
    real_not_bug: int
    refuted: int
    errored: int
    verdicts: list = field(default_factory=list)

    @property
    def precision_genuine_bug(self) -> Optional[float]:
        n = self.claims_judged - self.errored
        return round(self.genuine_bug / n, 3) if n else None

    def to_dict(self) -> dict:
        d = {
            "judge_model": self.judge_model,
            "claims_judged": self.claims_judged,
            "genuine_bug": self.genuine_bug,
            "real_not_bug": self.real_not_bug,
            "refuted": self.refuted,
            "errored": self.errored,
            "precision_genuine_bug": self.precision_genuine_bug,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }
        return d


# ---------------------------------------------------------------------------
# Scope + excerpt
# ---------------------------------------------------------------------------


def load_scope(manifest: Optional[str]) -> tuple[Path, Optional[set]]:
    """Return ``(root, allowed_files_or_None)``.

    ``manifest`` may be: None (root = project root, all files allowed), a directory
    (root = that dir, all files under it allowed), or a JSON file
    ``{"root": "...", "files": [...]}`` (root + an explicit allow-list).
    """
    if manifest is None:
        return PROJECT_ROOT, None
    p = Path(manifest)
    if p.is_dir():
        return p, None
    data = json.loads(p.read_text(encoding="utf-8"))
    root = Path(data.get("root", PROJECT_ROOT))
    files = data.get("files")
    allowed: Optional[set] = None
    if files:
        allowed = set()
        for f in files:
            fp = Path(f)
            allowed.add(str((fp if fp.is_absolute() else root / fp).resolve()))
    return root, allowed


def _resolve_file(file: str, root: Path) -> Optional[Path]:
    if not file:
        return None
    p = Path(file)
    if not p.is_absolute():
        p = root / file
    return p if p.exists() else None


def _excerpt(path: Path, line: Optional[int], *, window: int = 60, max_full: int = 220) -> str:
    """Return numbered source. Whole file if small; otherwise a window around ``line``."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # pragma: no cover - IO guard
        return f"(could not read {path}: {exc})"
    if not lines:
        return "(empty file)"
    if len(lines) <= max_full or not line:
        body = lines[:max_full]
        numbered = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(body))
        if len(lines) > max_full:
            numbered += f"\n... ({len(lines) - max_full} more lines)"
        return numbered
    lo = max(0, int(line) - window)
    hi = min(len(lines), int(line) + window)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


# ---------------------------------------------------------------------------
# Pluggable judge backend
# ---------------------------------------------------------------------------


def _parse_verdict(text: str) -> tuple[str, float, str]:
    """Parse the judge's JSON. Defaults to REFUTED (skeptical) on any garbage."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip("` \n")
    data: dict = {}
    try:
        data = json.loads(t)
    except Exception:
        match = re.search(r"\{.*\}", t, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = {}
    verdict = str(data.get("verdict", "")).upper().strip()
    if verdict not in VERDICTS:
        verdict = "REFUTED"
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return verdict, conf, str(data.get("mechanism", ""))[:400]


def _strip_provenance(claim: dict) -> dict:
    """Drop fields that would leak the answer or who produced the claim."""
    return {k: v for k, v in claim.items() if k not in _STRIP_KEYS}


def _claim_user_msg(claim: dict, excerpt: str) -> str:
    return (
        f"FILE: {claim.get('file', '?')}  (focus near line {claim.get('line', '?')})\n\n"
        f"CODE EXCERPT:\n```\n{excerpt}\n```\n\n"
        f"CLAIM: {claim.get('claim', '')}"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def judge_claims(
    claims: Sequence[dict],
    *,
    scope: Optional[str] = None,
    model: str = _DEFAULT_MODEL,
    judge_fn: Optional[Callable[[str, str, str], str]] = None,
) -> JudgeReport:
    """Judge each claim against its (in-scope) source file, blind to provenance.

    You supply the LLM call as ``judge_fn(system, user, model) -> str`` (sealeval ships no
    backend). It is called once per claim with the refute-by-default system prompt and a
    windowed code excerpt; anything the backend needs (its own cwd, timeouts, cost logging)
    lives inside your ``judge_fn``.
    """
    root, allowed = load_scope(scope)
    if judge_fn is None:
        raise ValueError(
            "judge_claims requires a judge_fn: a callable (system_prompt, user_prompt, "
            "model) -> str that calls your LLM/CLI. sealeval ships no LLM backend (zero "
            "deps by design). See the README for a wiring example."
        )
    verdicts: list[JudgeVerdict] = []
    counts = {"GENUINE_BUG": 0, "REAL_NOT_BUG": 0, "REFUTED": 0}
    errored = 0

    for raw in claims:
        claim = _strip_provenance(dict(raw))
        cid = str(claim.get("id", "?"))
        file = claim.get("file", "")
        path = _resolve_file(file, root)
        if path is None:
            # The SYSTEM's failure, not the harness's: a claim about a file that does not
            # exist is a hallucination and MUST count against precision (REFUTED in counts),
            # not vanish into `errored` (which would reward inventing paths). `errored` is
            # reserved for backend/infrastructure failures below.
            counts["REFUTED"] += 1
            verdicts.append(JudgeVerdict(cid, "REFUTED", 0.0, "", file=file, error="file not found"))
            continue
        if allowed is not None and str(path.resolve()) not in allowed:
            counts["REFUTED"] += 1
            verdicts.append(JudgeVerdict(cid, "REFUTED", 0.0, "", file=file, error="file not in scope"))
            continue
        excerpt = _excerpt(path, claim.get("line"))
        try:
            text = judge_fn(JUDGE_SYSTEM, _claim_user_msg(claim, excerpt), model)
            verdict, conf, mech = _parse_verdict(text)
        except Exception as exc:  # noqa: BLE001 — one bad claim must not abort the run
            errored += 1
            verdicts.append(
                JudgeVerdict(cid, "REFUTED", 0.0, "", file=file, error=str(exc)[:200])
            )
            continue
        counts[verdict] += 1
        verdicts.append(JudgeVerdict(cid, verdict, conf, mech, file=file))

    return JudgeReport(
        judge_model=model,
        claims_judged=len(claims),
        genuine_bug=counts["GENUINE_BUG"],
        real_not_bug=counts["REAL_NOT_BUG"],
        refuted=counts["REFUTED"],
        errored=errored,
        verdicts=verdicts,
    )


# ---------------------------------------------------------------------------
# Calibration ledger
# ---------------------------------------------------------------------------


def append_calibration(
    report: JudgeReport,
    *,
    run_id: str,
    when: str,
    claims_path: str = "",
    scope_path: str = "",
    path: Path = _CALIBRATION_PATH,
) -> dict:
    """Append one calibration entry (used by Phase 3 cross-family agreement)."""
    entry = {
        "run_id": run_id,
        "when": when,
        "judge_model": report.judge_model,
        "claims_path": str(claims_path),
        "scope_path": str(scope_path),
        "claims_judged": report.claims_judged,
        "errored": report.errored,
        "genuine_bug": report.genuine_bug,
        "real_not_bug": report.real_not_bug,
        "refuted": report.refuted,
        "precision_genuine_bug": report.precision_genuine_bug,
        # per-claim verdicts -> enables judge-vs-judge agreement scoring in Phase 3
        "verdicts": {v.claim_id: v.verdict for v in report.verdicts},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
