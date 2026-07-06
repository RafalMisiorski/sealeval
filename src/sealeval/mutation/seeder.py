"""Seed a clean corpus with AST-injected bugs and emit the sealed injection key.

The seeder copies the holdout file set into ``out_dir`` (preserving relative
paths) and mutates a deterministic, seeded subset. Files that yield no candidate
(or are budget-skipped) are copied verbatim, so a reviewer cannot infer "every
file has exactly one bug" from the corpus shape.

Determinism: identical ``(files, archetypes, per_file, seed, max_total)`` ->
identical mutated bytes and identical injection key. This is what makes the run
reproducible and the pre-registration meaningful.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import random
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from sealeval.mutation import catalog

logger = logging.getLogger(__name__)

_EXCLUDE_DIR_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "build", "dist"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class InjectionRecord:
    """One sealed-key entry: ground truth for recall + CTP matching."""

    file: str  # relative posix path within the corpus
    line: int
    col: int
    archetype: str
    description: str
    original_segment: str
    mutated_segment: str
    original_sha256: str  # of the whole clean file
    mutated_sha256: str  # of the whole mutated file

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "InjectionRecord":
        return cls(
            file=str(d["file"]),
            line=int(d["line"]),
            col=int(d.get("col", 0)),
            archetype=str(d["archetype"]),
            description=str(d.get("description", "")),
            original_segment=str(d.get("original_segment", "")),
            mutated_segment=str(d.get("mutated_segment", "")),
            original_sha256=str(d.get("original_sha256", "")),
            mutated_sha256=str(d.get("mutated_sha256", "")),
        )


@dataclass
class SeedResult:
    records: list[InjectionRecord] = field(default_factory=list)
    files_total: int = 0
    files_mutated: int = 0
    archetype_counts: dict[str, int] = field(default_factory=dict)

    @property
    def mutations(self) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def discover_python_files(root: Path, limit: Optional[int] = None) -> list[Path]:
    """Sorted relative ``.py`` paths under ``root``, skipping vcs/cache/venv dirs."""
    root = Path(root)
    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in _EXCLUDE_DIR_PARTS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        out.append(rel)
    if limit is not None:
        out = out[:limit]
    return out


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _mutate_one_file(
    src: str,
    rel_posix: str,
    archetypes: tuple[str, ...],
    per_file: int,
    rng: random.Random,
    remaining_budget: Optional[int],
    arch_budget: Optional[dict[str, int]] = None,
) -> tuple[str, list[InjectionRecord]]:
    """Apply <= per_file mutations (<=1 per archetype, distinct lines) to one file.

    Each candidate is compile-guarded against the *evolving* source before being
    accepted, so the final file always parses. ``arch_budget`` (if given) caps the
    GLOBAL count per archetype across the corpus and is decremented in place, so the
    seeded key stays balanced across bug classes.
    """
    original_sha = sha256_text(src)
    candidates = catalog.find_candidates(src, archetypes)
    if not candidates:
        return src, []

    by_arch: dict[str, list[catalog.Candidate]] = {}
    for c in candidates:
        by_arch.setdefault(c.archetype, []).append(c)
    arch_order = [a for a in archetypes if a in by_arch]
    rng.shuffle(arch_order)

    current = src
    used_lines: set[int] = set()
    accepted: list[InjectionRecord] = []
    cap = per_file if remaining_budget is None else min(per_file, remaining_budget)

    for arch in arch_order:
        if len(accepted) >= cap:
            break
        if arch_budget is not None and arch_budget.get(arch, 0) <= 0:
            continue  # this archetype has hit its global balance cap
        pool = list(by_arch[arch])
        rng.shuffle(pool)
        for cand in pool:
            if cand.line in used_lines:
                continue
            original_segment = catalog._seg(current, cand.node) or ""
            trial = catalog.replace_span(current, cand.node, cand.new_src)
            if trial == current:
                continue
            try:
                ast.parse(trial)
            except SyntaxError:
                continue
            current = trial
            used_lines.add(cand.line)
            accepted.append(
                InjectionRecord(
                    file=rel_posix,
                    line=cand.line,
                    col=int(getattr(cand.node, "col_offset", 0)),
                    archetype=cand.archetype,
                    description=cand.description,
                    original_segment=original_segment,
                    mutated_segment=cand.new_src,
                    original_sha256=original_sha,
                    mutated_sha256="",  # filled once all edits to this file are done
                )
            )
            if arch_budget is not None:
                arch_budget[arch] = arch_budget.get(arch, 0) - 1
            break  # at most one mutation per archetype per file

    if accepted:
        final_sha = sha256_text(current)
        for rec in accepted:
            rec.mutated_sha256 = final_sha
    return current, accepted


def seed_corpus(
    src_root: Path,
    out_dir: Path,
    *,
    files: Optional[Sequence[Path]] = None,
    archetypes: tuple[str, ...] = catalog.MVP_ARCHETYPES,
    per_file: int = 3,
    seed: int = 0,
    max_total: Optional[int] = None,
    per_archetype_cap: Optional[int] = None,
) -> SeedResult:
    """Copy ``src_root`` file set into ``out_dir`` and inject seeded mutations.

    Parameters
    ----------
    src_root : clean holdout repo root.
    out_dir  : corpus output dir (created; the scope root every system reviews).
    files    : relative ``.py`` paths to include; defaults to discovery under root.
    per_file : max mutations per file (<= 1 per archetype).
    max_total: global cap on injected bugs (e.g. to hit a ~50-bug target exactly).
    per_archetype_cap : global cap PER archetype, to keep the key class-balanced
        (e.g. el_run01 was 17/29 inverted_condition; v2 caps each class).
    """
    src_root = Path(src_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rels = list(files) if files is not None else discover_python_files(src_root)
    # Deterministic file order, then a seeded shuffle so the mutated subset isn't
    # just the alphabetically-first files.
    rels = [Path(r) for r in rels]
    rng = random.Random(seed)
    shuffled = sorted(rels, key=lambda p: p.as_posix())
    rng.shuffle(shuffled)

    result = SeedResult(files_total=len(rels))
    injected = 0
    arch_budget = (
        {a: per_archetype_cap for a in archetypes} if per_archetype_cap is not None else None
    )

    for rel in shuffled:
        src_path = src_root / rel
        dst_path = out_dir / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = src_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("skip unreadable file: %s", src_path)
            continue

        remaining = None if max_total is None else max(0, max_total - injected)
        if remaining == 0:
            dst_path.write_text(content, encoding="utf-8")
            continue

        mutated, records = _mutate_one_file(
            content, rel.as_posix(), archetypes, per_file, rng, remaining, arch_budget
        )
        dst_path.write_text(mutated, encoding="utf-8")
        if records:
            result.files_mutated += 1
            result.records.extend(records)
            injected += len(records)
            for rec in records:
                result.archetype_counts[rec.archetype] = (
                    result.archetype_counts.get(rec.archetype, 0) + 1
                )

    # Stable order for the key: by file then line.
    result.records.sort(key=lambda r: (r.file, r.line))
    logger.info(
        "seeded corpus: %d/%d files mutated, %d injections (%s)",
        result.files_mutated,
        result.files_total,
        result.mutations,
        result.archetype_counts,
    )
    return result


# ---------------------------------------------------------------------------
# Injection-key I/O
# ---------------------------------------------------------------------------


def dump_injection_key(records: Sequence[InjectionRecord], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [r.to_dict() for r in records]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_injection_key(path: Path) -> list[InjectionRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [InjectionRecord.from_dict(d) for d in data]


def copy_clean_corpus(src_root: Path, out_dir: Path, files: Sequence[Path]) -> None:
    """Copy the clean file set verbatim (used to diff/audit against the mutated corpus)."""
    src_root, out_dir = Path(src_root), Path(out_dir)
    for rel in files:
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_root / rel, dst)
