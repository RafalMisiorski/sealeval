"""Binding commitment over the sealed injection key.

Flow:
    freeze : compute ``seal = sha256(salt || canonical_json(key))``; write the seal
             dict and git-commit it. The commit's hash + UTC timestamp prove the key
             was fixed BEFORE any system ran. The plaintext key is kept out of the
             run dir (a sibling ``.secret`` path, gitignored) until reveal.
    reveal : after every system has produced findings AND been judged, copy the
             plaintext key into the run dir and verify ``sha256`` matches the seal.
             ``analyze`` refuses to run unless this verification passes.

Binding (cannot swap the key after committing the hash) is the property that makes
the result defensible. Salt is committed alongside the hash — binding does not
require salt secrecy; hiding comes from simply not publishing the plaintext.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any, Optional, Sequence

ALGO = "sha256"


class SealError(RuntimeError):
    """Raised when a seal fails to verify, or reveal is attempted out of order."""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 preserved."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(salt: str, obj: Any) -> str:
    return hashlib.sha256((salt + canonical_json(obj)).encode("utf-8")).hexdigest()


def make_seal(plaintext: Any, *, salt: Optional[str] = None, count: Optional[int] = None) -> dict:
    """Return a seal dict ``{algo, salt, seal, n}`` for ``plaintext``.

    ``salt`` is generated with ``secrets`` when not supplied (tests pass a fixed
    salt for determinism). ``count`` annotates the number of sealed items.
    """
    salt = salt if salt is not None else secrets.token_hex(16)
    return {
        "algo": ALGO,
        "salt": salt,
        "seal": _digest(salt, plaintext),
        "n": int(count) if count is not None else (len(plaintext) if hasattr(plaintext, "__len__") else None),
    }


def verify_seal(plaintext: Any, seal: dict) -> bool:
    if seal.get("algo") != ALGO:
        raise SealError(f"unsupported seal algo {seal.get('algo')!r}")
    return _digest(str(seal["salt"]), plaintext) == seal.get("seal")


def write_seal(seal: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seal, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_seal(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Reveal gate
# ---------------------------------------------------------------------------


def reveal_gate_ok(findings_dir: Path, systems: Sequence[str]) -> bool:
    """True only when every system has a ``findings/<system>.json`` on disk.

    This is the mechanical guarantee that no system can be (re-)run after the key
    is exposed: reveal is blocked until all systems have committed their findings.
    """
    findings_dir = Path(findings_dir)
    return all((findings_dir / f"{s}.json").exists() for s in systems)


def reveal(
    plaintext: Any,
    seal: dict,
    out_path: Path,
    *,
    findings_dir: Optional[Path] = None,
    systems: Optional[Sequence[str]] = None,
) -> Path:
    """Verify ``plaintext`` against ``seal`` and write it to ``out_path``.

    If ``findings_dir`` + ``systems`` are given, enforce the reveal gate first.
    Raises ``SealError`` on a gate failure or hash mismatch.
    """
    if findings_dir is not None and systems is not None:
        if not reveal_gate_ok(findings_dir, systems):
            raise SealError("reveal blocked: not every system has produced findings yet")
    if not verify_seal(plaintext, seal):
        raise SealError("seal mismatch: plaintext does not match the committed hash")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plaintext, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
