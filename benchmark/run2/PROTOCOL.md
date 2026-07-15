# sealeval public benchmark — run2 (pre-registered; cross-vendor panel)

**The question this run exists to test honestly:** do two frontier models (Claude Fable 5,
GPT-5.6-sol) have *disjoint failure zones* on real single-line defects — and does running both
(the union) buy recall that either alone cannot, at acceptable precision? run2 also upgrades the
judging from run1's single judge to a **symmetric cross-vendor panel**: every claim is judged by
BOTH vendors' models, blind, and counts only if both say GENUINE_BUG — the non-self-preferring
panel mode the README describes.

## Provenance disclosure (read this first)

run2 was **executed in a private repository** (the operator's workbench); this directory is a
faithful export. The wall-clock seal→findings→reveal commit ordering proof therefore lives in
that private history (commits `e2becb5e` → `88bfb772` → `8367da60`, 2026-07-15), not in this
repo's — unlike run1, where this repo's own history is the proof. What remains fully checkable
offline from this export alone:

- `prereg.lock.json` self-hash verifies (`sealeval.prereg_verify`) — the protocol, thresholds and
  hypotheses cannot have drifted after the fact without breaking it;
- `key.json` verifies against the committed `key.sealed` (`sealeval.verify_seal`);
- the corpus recipe below regenerates byte-identical files (per-file sha256 frozen in the lock);
- `score_run2.py` (stdlib only) recomputes every number in RESULTS.md from findings + verdicts + key.

## Corpus (reproducible, not committed)

Same clean repo and recipe family as run1, **fresh seed** (fresh injection set):

```bash
git clone https://github.com/psf/requests && cd requests
git checkout 4c800e9aea2059660b8306b0fc8f9e9a4232cb3e
python - <<'EOF'
import sealeval as se
from pathlib import Path
res = se.seed_corpus(Path("src/requests"), Path("../bench_corpus"),
                     per_file=3, seed=20260715, per_archetype_cap=10)
print(res.mutations)   # -> 24
EOF
```

**24 injected defects** across 19 files (10 inverted_condition, 5 null_deref, 5 wrong_operator,
4 swallowed_exception). Single-line edits; all other line numbers byte-stable.

## Systems (2 real + 1 derived)

Both arms get run1's verbatim frozen prompt (sha256 in `prereg.lock.json`), one file per fresh
CLI process, cwd pinned to the corpus:

| id | system |
|---|---|
| fable5 | Claude Fable 5 via the claude CLI (stream-json interactive, model `claude-fable-5`) |
| gpt56sol | GPT-5.6-sol via `codex exec --json` (model `gpt-5.6-sol`, sandbox read-only) |
| union | derived arm: concat of both findings, dedup on (file, line); a confirmed copy wins |

## Judging — blind cross-vendor panel

Each claim (provenance stripped, windowed excerpt, refute-by-default, fresh process per claim) is
judged **independently by both** `claude-fable-5` and `gpt-5.6-sol`. **CONFIRMED = both return
GENUINE_BUG.** Self-preference control is symmetry: both arms face the same two judges, and
neither judge knows which system authored the claim.

## Frozen metrics + pre-registered hypotheses

- **TP** = CONFIRMED ∧ lands on a sealed injection (same file, |Δline| ≤ 1)
- **precision** = TP / all claims of the arm (hallucinated paths count against precision)
- **recall** = distinct injections matched / 24
- **H1 (two-sided complementarity):** each arm catches ≥ 3 injections the other misses
- **H2 (union lift):** recall(union) ≥ max(solo recalls) + 0.10 at precision(union) ≥ 0.50
- **Decision rule:** GO iff H2; KILL iff recall(union) − max(solo) < 0.05 or precision(union)
  < 0.40; otherwise inconclusive. Published either way.

Prior (run1, Sonnet-tier claude arm): codex 0.75R/0.62P, claude 0.33R/0.47P.
