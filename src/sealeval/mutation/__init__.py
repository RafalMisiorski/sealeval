"""AST mutation seeding — sealed ground-truth bug injection."""

from sealeval.mutation.catalog import (
    ARCHETYPES,
    MVP_ARCHETYPES,
    Candidate,
    find_candidates,
    replace_span,
)
from sealeval.mutation.seeder import (
    InjectionRecord,
    SeedResult,
    copy_clean_corpus,
    discover_python_files,
    dump_injection_key,
    load_injection_key,
    seed_corpus,
    sha256_text,
)

__all__ = [
    "ARCHETYPES", "MVP_ARCHETYPES", "Candidate", "find_candidates", "replace_span",
    "InjectionRecord", "SeedResult", "copy_clean_corpus", "discover_python_files",
    "dump_injection_key", "load_injection_key", "seed_corpus", "sha256_text",
]
