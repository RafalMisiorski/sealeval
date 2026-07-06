"""sealeval — pre-registered, sealed-ground-truth evaluation of code-analysis systems.

Zero dependencies. Three composable primitives (you supply the LLM call):

    mutation : inject sealed AST bugs into a clean codebase (deterministic ground truth)
    sealing  : commit-ahead hash of the injection key + pre-registration (tamper-evident)
    judge    : refute-by-default, provenance-blind adjudication of claims (pluggable backend)

The pipeline they compose: seed sealed bugs -> commit the seal BEFORE any system runs ->
let systems flag findings -> reveal the key -> judge findings blind against the sealed
ground truth. The integrity (you cannot move the key after seeing results) is what makes a
verdict survive an adversarial reviewer.

Harvested from the Neural Holding code-review benchmark; the adjudication logic is intact,
the NH-specific LLM wiring is removed so you bring your own ``judge_fn``.
"""

from sealeval import judge, mutation, sealing
from sealeval.judge import (
    JUDGE_SYSTEM,
    VERDICTS,
    JudgeReport,
    JudgeVerdict,
    append_calibration,
    judge_claims,
    load_scope,
)
from sealeval.mutation import (
    ARCHETYPES,
    InjectionRecord,
    dump_injection_key,
    find_candidates,
    load_injection_key,
    seed_corpus,
)
from sealeval.sealing import SealError, canonical_json, make_seal, reveal, verify_seal
from sealeval.sealing.prereg import corpus_manifest
from sealeval.sealing.prereg import freeze as prereg_freeze
from sealeval.sealing.prereg import verify as prereg_verify

__version__ = "0.1.0"

__all__ = [
    "judge", "mutation", "sealing",
    "judge_claims", "JudgeReport", "JudgeVerdict", "JUDGE_SYSTEM", "VERDICTS",
    "load_scope", "append_calibration",
    "seed_corpus", "find_candidates", "InjectionRecord", "ARCHETYPES",
    "dump_injection_key", "load_injection_key",
    "make_seal", "verify_seal", "reveal", "canonical_json", "SealError",
    "prereg_freeze", "prereg_verify", "corpus_manifest",
]
