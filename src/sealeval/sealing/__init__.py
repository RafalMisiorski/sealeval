"""Tamper-evident sealing + pre-registration."""

from sealeval.sealing.keyseal import (
    ALGO,
    SealError,
    canonical_json,
    make_seal,
    read_seal,
    reveal,
    reveal_gate_ok,
    verify_seal,
    write_seal,
)
from sealeval.sealing.prereg import (
    PreRegError,
    corpus_manifest,
    freeze,
    load,
    require_verified,
    verify,
)

__all__ = [
    "ALGO", "SealError", "canonical_json", "make_seal", "verify_seal", "write_seal",
    "read_seal", "reveal_gate_ok", "reveal", "PreRegError", "corpus_manifest",
    "freeze", "load", "verify", "require_verified",
]
