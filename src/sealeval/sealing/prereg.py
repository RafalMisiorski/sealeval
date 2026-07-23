"""Freeze the whole pre-registration bundle behind a self-hash.

The lock file (``prereg.lock.json``) stores the frozen ``content`` plus a
``content_sha256`` over its canonical form. ``analyze`` calls :func:`verify` and
aborts on any drift, so no hypothesis, baseline command, threshold, corpus hash,
or seed can be changed after results are seen without breaking the hash (and the
git history of the committed lock makes the change auditable).

``content`` is assembled by the caller (the freeze CLI) and typically contains:
    hypotheses        H1/H2/H3 + the pre-registered KILL condition (text)
    baselines         per-system exact command + model id + version + config
    corpus            {relative_path: sha256} manifest of the sealed corpus
    injection_seal    the keyseal dict (hash commitment over the injection key)
    metrics           metric definitions + the cost formula reference
    price_snapshot    the caller's per-model token price table at freeze time (optional)
    shuffle_seed      RNG seed for the cross-system blinding shuffle
    mutation_config   {archetypes, per_file, seed, max_total}
    thresholds        {go_ratio, precision_rule, ...} GO/KILL/PARTIAL gates
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from sealeval.sealing.keyseal import canonical_json


class PreRegError(RuntimeError):
    """Raised when the pre-registration lock fails to verify."""


def _content_sha(content: dict) -> str:
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def corpus_manifest(corpus_root: Path, files: Sequence[Path]) -> dict[str, str]:
    """Map each relative file path -> sha256 of its (sealed-corpus) bytes."""
    corpus_root = Path(corpus_root)
    out: dict[str, str] = {}
    for rel in files:
        rel = Path(rel)
        data = (corpus_root / rel).read_bytes()
        out[rel.as_posix()] = hashlib.sha256(data).hexdigest()
    return out


def freeze(content: dict, path: Path, *, frozen_at: Optional[str] = None,
           round0: Optional[dict] = None, require_round0: bool = False) -> dict:
    """Write ``prereg.lock.json`` = ``{frozen_at, content_sha256, content}``.

    Round-0 integration (see ``sealeval.measure.pilot``): pass the ``round0_gate()`` result as
    ``round0`` and the seal will (a) REFUSE to freeze on unvalidated apparatus, and (b) bind the
    Round-0 receipt into the hashed content, so the lock itself records that controls, metric
    headroom, parse-rate and leakage were checked BEFORE the run. ``require_round0=True`` makes
    supplying it mandatory.

    Backwards compatible: with no ``round0`` argument the content and hash are bit-identical to
    the previous behaviour, so existing locks (e.g. skillsbench prereg_v1..v27) still verify.
    """
    if round0 is not None:
        if not round0.get("ready_to_seal"):
            raise PreRegError(
                "Round 0 did not pass -- refusing to seal on unvalidated apparatus: "
                + "; ".join(round0.get("blocking", ["(no reason given)"])))
        from sealeval.measure.pilot import round0_receipt  # local import: avoids import cycle
        content = {**content, "round0": round0_receipt(round0)}
    elif require_round0:
        raise PreRegError(
            "require_round0=True but no Round-0 result supplied -- run measure.round0_gate() "
            "and pass it as round0=; do not seal an unvalidated apparatus")
    when = frozen_at or datetime.now(timezone.utc).isoformat()
    lock = {
        "frozen_at": when,
        "content_sha256": _content_sha(content),
        "content": content,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    return lock


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify(path: Path) -> bool:
    """True iff the stored content still hashes to the stored ``content_sha256``."""
    lock = load(path)
    if "content" not in lock or "content_sha256" not in lock:
        raise PreRegError("malformed lock: missing content or content_sha256")
    return _content_sha(lock["content"]) == lock["content_sha256"]


def require_verified(path: Path) -> dict:
    """Return the frozen content, or raise ``PreRegError`` if the lock drifted."""
    if not verify(path):
        raise PreRegError(f"pre-registration drift detected in {path}")
    return load(path)["content"]
