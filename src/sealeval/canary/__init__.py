"""sealeval canary: plant known defects in YOUR repository, seal the key, score what your reviewer caught.

A canary is a planted bug with a sealed answer key. Planting a handful on a throwaway branch and opening a pull request
tells you three things about whatever reviews your code (a bot, an agent, a CLI, a person):

* liveness - did it say anything at all on the canary PR (a reviewer whose quota ran out returns nothing, silently);
* recall per archetype - which planted defects it matched (same file, within a few lines);
* noise - how many things it flagged that were not planted.

The key never enters the branch; only ``.sealeval/key.sealed`` (sha-256 over salt + canonical key) is committed, so the
result can be verified later against a key that was fixed BEFORE anyone reviewed.

Plant:  ``sealeval canary plant --repo . --n 5``           (writes the key under ~/.sealeval/canary/<run>/key.json)
Score:  ``sealeval canary score --run <run> --findings findings.json``   (or ``--gh-pr owner/repo#123``)

Findings are ``[{"file": "path/in/repo.py", "line": 42, "claim": "..."}]``; ``line`` is a line in the NEW file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import subprocess
import time
import tokenize
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sealeval.mutation import catalog
from sealeval.sealing.keyseal import make_seal, verify_seal

PUBLISHED_ARCHETYPES = tuple(catalog.MVP_ARCHETYPES)   # the set whose blind-spot numbers are public (review-arena, 2026-09)
STATE_DIR = Path(os.environ.get("SEALEVAL_HOME", str(Path.home() / ".sealeval"))) / "canary"
LINE_TOLERANCE = 2
_SUBS = [(" which ", " that "), (" e.g. ", " for example "), ("  ", " "), (" the ", " this ")]


# ---------------------------------------------------------------- git helpers
def _git(args: list, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args[:2]), p.returncode, (p.stderr or p.stdout)[-300:]))
    return p


def repo_root(path: Path) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], path).stdout.strip())


def tracked_python_files(root: Path, include: Optional[str] = None, exclude_tests: bool = True, max_lines: int = 2500) -> list:
    out = []
    for rel in _git(["ls-files", "--", "*.py"], root).stdout.splitlines():
        rel = rel.strip()
        if not rel or (exclude_tests and ("/tests/" in "/" + rel or rel.startswith("tests/") or rel.endswith("conftest.py") or rel.startswith("test_") or "/test_" in rel)):
            continue
        if include and not rel.startswith(include.rstrip("/") + "/") and rel != include:
            continue
        p = root / rel
        try:
            if p.read_text(encoding="utf-8").count("\n") > max_lines:
                continue
        except (UnicodeDecodeError, OSError):
            continue
        out.append(rel)
    return sorted(out)


# ---------------------------------------------------------------- behaviour-neutral noise (comments / docstrings only)
def _is_triple(s: str) -> bool:
    return s.lstrip("rRbBuUfF")[:3] in ('"""', "'''")


def _noise_candidates(src: str) -> list:
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return out
    for t in toks:
        if t.type == tokenize.COMMENT and t.string.strip("# ").strip():
            out.append((t.start[0], t.start[1]))
        elif t.type == tokenize.STRING and _is_triple(t.string) and "\n" in t.string and "{" not in t.string and "%" not in t.string:
            for ln in range(t.start[0] + 1, t.end[0]):
                out.append((ln, 0))
    return sorted(set(out))


def _reword(line: str, col: int = 0) -> Optional[str]:
    eol = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
    body = line[: len(line) - len(eol)]
    head, text = body[:col], body[col:]
    if not text.strip() or '"""' in text or "'''" in text or text.lstrip().startswith("#!"):
        return None
    for a, b in _SUBS:
        if a in text:
            if a == "  " and (text.lstrip().startswith("#") or col == 0 and text.startswith(" ")):
                continue
            return head + text.replace(a, b, 1) + eol
    words = text.strip().lstrip("# ").split()
    if len(words) >= 3 and not text.rstrip().endswith((".", ":", ",", ")", "`", "'", '"')):
        return head + text.rstrip() + "." + eol
    return None


def noise_edits(src: str, k: int, rng: random.Random, exclude_lines=()) -> tuple:
    """Up to ``k`` rewordings on comment / docstring lines. Returns (new_src, edited_lines)."""
    lines = src.splitlines(keepends=True)
    cands = [(ln, col) for ln, col in _noise_candidates(src) if ln not in set(exclude_lines) and ln <= len(lines)]
    rng.shuffle(cands)
    edited = []
    for ln, col in cands:
        if len(edited) >= k or ln in edited:
            continue
        new = _reword(lines[ln - 1], col)
        if new is None or new == lines[ln - 1]:
            continue
        lines[ln - 1] = new
        edited.append(ln)
    return "".join(lines), sorted(edited)


def neutral_check(a: str, b: str) -> bool:
    """True iff the token streams differ only inside COMMENT or triple-quoted STRING tokens."""
    try:
        ta = [t for t in tokenize.generate_tokens(io.StringIO(a).readline) if t.type not in (tokenize.NL, tokenize.NEWLINE)]
        tb = [t for t in tokenize.generate_tokens(io.StringIO(b).readline) if t.type not in (tokenize.NL, tokenize.NEWLINE)]
    except (tokenize.TokenError, SyntaxError):
        return False
    if len(ta) != len(tb) or a.count("\n") != b.count("\n"):
        return False
    for x, y in zip(ta, tb):
        if x.string == y.string and x.type == y.type:
            continue
        if x.type != y.type:
            return False
        if x.type == tokenize.COMMENT:
            continue
        if x.type == tokenize.STRING and _is_triple(x.string) and _is_triple(y.string) and x.string[:4] == y.string[:4]:
            continue
        return False
    return True


# ---------------------------------------------------------------- planting
@dataclass
class Planted:
    file: str
    line: int
    archetype: str
    description: str
    original_segment: str
    mutated_segment: str


@dataclass
class PlantResult:
    run_id: str
    branch: str
    base_sha: str
    head_sha: str
    seal: dict
    key_path: Path
    planted: list = field(default_factory=list)
    files_changed: list = field(default_factory=list)


def select_sites(root: Path, files: list, archetypes: tuple, n: int, seed: int, version: str = "v2", max_per_file: int = 1) -> list:
    rng = random.Random(seed)
    pool = []
    for rel in files:
        src = (root / rel).read_text(encoding="utf-8")
        if "\t" in src:
            continue
        for c in catalog.find_candidates(src, archetypes, version=version):
            pool.append((rel, c, src))
    pool.sort(key=lambda t: (t[0], t[1].line, t[1].archetype))
    rng.shuffle(pool)
    size: dict = {}
    for _, c, _ in pool:
        size[c.archetype] = size.get(c.archetype, 0) + 1
    pool.sort(key=lambda t: size.get(t[1].archetype, 0))           # scarcity-first: rare archetypes pick sites first
    per_arch_cap = max(1, -(-n // max(1, len(archetypes))))          # spread across archetypes
    chosen, per_file, per_arch = [], {}, {}
    for rel, c, src in pool:
        if len(chosen) >= n or per_file.get(rel, 0) >= max_per_file or per_arch.get(c.archetype, 0) >= per_arch_cap:
            continue
        mutated = catalog.replace_span(src, c.node, c.new_src)
        if mutated == src:
            continue
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(mutated, rel, "exec")
        except SyntaxError:
            continue
        chosen.append({"file": rel, "line": c.line, "archetype": c.archetype, "description": c.description,
                       "original_segment": catalog.source_segment(src, c.node) or "", "mutated_segment": c.new_src, "mutated_src": mutated})
        per_file[rel] = per_file.get(rel, 0) + 1
        per_arch[c.archetype] = per_arch.get(c.archetype, 0) + 1
    if len(chosen) < n:                                              # relax the per-archetype spread if the repo is small
        for rel, c, src in pool:
            if len(chosen) >= n or per_file.get(rel, 0) >= max_per_file or any(x["file"] == rel and x["line"] == c.line for x in chosen):
                continue
            mutated = catalog.replace_span(src, c.node, c.new_src)
            if mutated == src:
                continue
            try:
                compile(mutated, rel, "exec")
            except SyntaxError:
                continue
            chosen.append({"file": rel, "line": c.line, "archetype": c.archetype, "description": c.description,
                           "original_segment": catalog.source_segment(src, c.node) or "", "mutated_segment": c.new_src, "mutated_src": mutated})
            per_file[rel] = per_file.get(rel, 0) + 1
    return chosen


def plant(repo: Path, n: int = 5, archetypes: tuple = PUBLISHED_ARCHETYPES, seed: Optional[int] = None, branch: Optional[str] = None,
          include: Optional[str] = None, noise: tuple = (2, 6), version: str = "v2", state_dir: Path = STATE_DIR, commit: bool = True) -> PlantResult:
    """Create a branch from HEAD with ``n`` planted defects (one per file) plus behaviour-neutral rewordings; seal the key.
    The key is written OUTSIDE the repository (``state_dir/<run_id>/key.json``); the branch carries only ``.sealeval/key.sealed``."""
    root = repo_root(Path(repo))
    if _git(["status", "--porcelain"], root).stdout.strip():
        raise RuntimeError("working tree not clean: commit or stash first (the canary branch must start from a clean HEAD)")
    seed = seed if seed is not None else int(time.time())
    rng = random.Random(seed)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:6]
    branch = branch or ("canary/" + run_id)
    files = tracked_python_files(root, include=include)
    sites = select_sites(root, files, archetypes, n, seed, version=version)
    if not sites:
        raise RuntimeError("no plantable sites found for archetypes %s in %d Python files" % (", ".join(archetypes), len(files)))
    base = _git(["rev-parse", "HEAD"], root).stdout.strip()
    key = [{"file": s["file"], "line": s["line"], "archetype": s["archetype"], "description": s["description"],
            "original_segment": s["original_segment"], "mutated_segment": s["mutated_segment"]} for s in sites]
    salt = "%032x" % rng.getrandbits(128)
    seal = make_seal(key, salt=salt, count=len(key))
    prev = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip()
    _git(["checkout", "-q", "-b", branch], root)
    changed = []
    try:
        for s in sites:
            src = s["mutated_src"]
            noised, _ = noise_edits(src, rng.randint(*noise), rng, exclude_lines=(s["line"],))
            content = noised if neutral_check(src, noised) else src
            (root / s["file"]).write_text(content, encoding="utf-8", newline="\n")
            changed.append(s["file"])
        carriers = [f for f in files if f not in changed]
        for rel in rng.sample(carriers, min(2, len(carriers))):        # carrier files: rewordings only, so no diff is a one-line tell
            src = (root / rel).open(encoding="utf-8", newline="").read()
            noised, lines = noise_edits(src, rng.randint(1, 3), rng)
            if lines and neutral_check(src, noised):
                (root / rel).write_text(noised, encoding="utf-8", newline="\n")
                changed.append(rel)
        (root / ".sealeval").mkdir(exist_ok=True)
        (root / ".sealeval" / "key.sealed").write_text(json.dumps({**seal, "run_id": run_id, "base": base}, indent=2), encoding="utf-8")
        changed.append(".sealeval/key.sealed")
        head = base
        if commit:
            _git(["add", "-A"], root)
            _git(["commit", "-q", "-m", "docs: reword comments and docstrings"], root)
            head = _git(["rev-parse", "HEAD"], root).stdout.strip()
    finally:
        if commit:
            _git(["checkout", "-q", prev], root)
    kdir = Path(state_dir) / run_id
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    (kdir / "run.json").write_text(json.dumps({"run_id": run_id, "repo": str(root), "branch": branch, "base": base, "head": head, "seed": seed,
                                                "archetypes": list(archetypes), "seal": seal, "planted_at": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
    return PlantResult(run_id=run_id, branch=branch, base_sha=base, head_sha=head, seal=seal, key_path=kdir / "key.json",
                       planted=[Planted(**{k: v for k, v in s.items() if k != "mutated_src"}) for s in sites], files_changed=sorted(set(changed)))


# ---------------------------------------------------------------- scoring
def load_findings(path: Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for o in data if isinstance(data, list) else data.get("findings", []):
        try:
            out.append({"file": str(o.get("file", "")).replace("\\", "/").lstrip("./"), "line": int(o.get("line")), "claim": str(o.get("claim", ""))[:300]})
        except (TypeError, ValueError):
            continue
    return out


def findings_from_github(pr: str) -> list:
    """Inline review comments of a PR (``owner/repo#123``) via the gh CLI; every inline comment is a finding at (path, line)."""
    m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", pr.strip())
    if not m:
        raise ValueError("expected owner/repo#number")
    repo, num = m.group(1), m.group(2)
    p = subprocess.run(["gh", "api", "--paginate", "repos/%s/pulls/%s/comments" % (repo, num)], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError("gh api failed: %s" % (p.stderr or "")[-200:])
    out = []
    for c in json.loads(p.stdout or "[]"):
        line = c.get("line") or c.get("original_line")
        if c.get("path") and line:
            out.append({"file": c["path"], "line": int(line), "claim": (c.get("body") or "")[:300], "author": (c.get("user") or {}).get("login", "")})
    return out


def score(key: list, seal: dict, findings: list, tolerance: int = LINE_TOLERANCE) -> dict:
    """Recall per archetype (a planted site counts as found when a finding sits on its file within ``tolerance`` lines),
    liveness, and noise (findings on no planted site). Verifies the key against the seal first."""
    ok = verify_seal(key, seal)
    matched, hits = set(), {}
    for i, k in enumerate(key):
        for f in findings:
            if f["file"].endswith(k["file"]) or k["file"].endswith(f["file"]):
                if abs(int(f["line"]) - int(k["line"])) <= tolerance:
                    matched.add(i)
                    hits[i] = f
                    break
    by_arch: dict = {}
    for i, k in enumerate(key):
        a = by_arch.setdefault(k["archetype"], {"n": 0, "found": 0})
        a["n"] += 1
        a["found"] += int(i in matched)
    noise = [f for f in findings if not any(i in matched and hits[i] is f for i in matched)]
    return {"seal_verified": ok, "liveness": len(findings) > 0, "planted": len(key), "found": len(matched), "recall": round(len(matched) / len(key), 3) if key else None,
            "by_archetype": by_arch, "noise_findings": len(noise), "missed": [{"file": k["file"], "line": k["line"], "archetype": k["archetype"]} for i, k in enumerate(key) if i not in matched]}


def render(res: dict, run: Optional[dict] = None) -> str:
    lines = ["sealeval canary: %s" % (run or {}).get("run_id", ""), "seal verified: %s" % res["seal_verified"], "liveness: %s" % ("alive (%d findings)" % (res["found"] + res["noise_findings"]) if res["liveness"] else "SILENT: no findings at all on the canary branch"),
             "planted %d, found %d, recall %s, noise findings %d" % (res["planted"], res["found"], res["recall"], res["noise_findings"]), ""]
    for a, v in sorted(res["by_archetype"].items()):
        lines.append("  %-22s %d/%d" % (a, v["found"], v["n"]))
    if res["missed"]:
        lines += ["", "missed:"] + ["  %s:%d  %s" % (m["file"], m["line"], m["archetype"]) for m in res["missed"]]
    return "\n".join(lines)
