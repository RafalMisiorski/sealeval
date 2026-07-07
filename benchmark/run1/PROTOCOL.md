# sealeval public benchmark — run1 (pre-registered)

**The claim this benchmark exists to test honestly:** how well do AI code reviewers find real,
present, single-line defects in real-world code — scored **blind**, against ground truth that was
**cryptographically sealed and published BEFORE any system ran**. The git history of this very
repository is the pre-registration proof: the commit adding `key.sealed` + `prereg.lock.json`
predates the commits adding any findings or results.

## Corpus (reproducible, not committed)

Apache-2.0 source is not vendored; the corpus is reproducible by recipe, byte-for-byte:

```bash
git clone https://github.com/psf/requests && cd requests
git checkout 4c800e9aea2059660b8306b0fc8f9e9a4232cb3e
python - <<'EOF'
import sealeval as se
from pathlib import Path
res = se.seed_corpus(Path("src/requests"), Path("../bench_corpus"),
                     per_file=3, seed=20260707, per_archetype_cap=10)
print(res.mutations)   # -> 24
EOF
```

Every generated file's sha256 is frozen in `prereg.lock.json` (`corpus_manifest_sha256`) — if your
regenerated corpus hashes differ, stop. **24 injected defects** across 15 of 19 files
(10 inverted_condition, 5 null_deref, 4 swallowed_exception, 4 wrong_operator, 1 off_by_one);
4 files are clean. Single-line edits keep all other line numbers byte-stable.

## Systems (3, all driven identically)

Each system reviews each of the 19 corpus files in a FRESH process (no cross-file contamination),
with the same frozen prompt (verbatim below; sha256 in `prereg.lock.json`):

> You are reviewing a single Python source file for REAL, present defects (logic errors:
> inverted conditions, off-by-one, wrong operator/comparison, None/null dereference, swallowed
> exceptions). Report ONLY defects you are confident are real bugs in THIS file as shown. Do not
> report style issues, TODOs, missing features, or hypothetical improvements. Output ONLY a JSON
> array: [{"line": <int>, "claim": "<one-sentence defect description>"}]. If there are none,
> output []. Line numbers refer to the numbering shown.

| id | system |
|---|---|
| claude | Claude Sonnet via the claude CLI (stream-json interactive mode) |
| codex | OpenAI Codex via `codex exec --json` |
| gemini | Gemini via the Antigravity CLI (`agy --print`) |

Findings land in `findings/<system>.json` as `[{id, file, line, claim}]`. `reproduce_systems.py`
ships the same driver behind a bring-your-own-model stdin/stdout seam.

## Sealing discipline (the point of the exercise)

1. `key.sealed` = sha256(salt‖key) commitment over the 24 injections — committed & pushed BEFORE
   any system ran. The plaintext key stays out of the repo until reveal.
2. `prereg.lock.json` = self-hashing freeze of the whole protocol: corpus recipe + per-file hashes,
   systems, the frozen prompt hash, metric definitions, line tolerance, and the prior (H1). Any
   post-hoc edit breaks `content_sha256`.
3. Reveal is gated: the key is published (`key.json`) only after all three `findings/*.json` are
   committed; `sealeval.reveal` verifies the hash before writing it.
4. Judging is **refute-by-default and blind**: each claim is judged in isolation against a code
   excerpt only (no system identity, no key); the judge's raw replies are published under
   `transcripts/`. Judge model: Claude Sonnet, fresh process per claim.

## Metrics (frozen)

- **TP** = judge says GENUINE_BUG **and** the claim lands on a sealed injection (same file,
  |line − key_line| ≤ 1).
- **precision** = TP / all claims by that system. Claims on nonexistent/out-of-scope files count
  against precision (a reviewer inventing paths is penalized, not excluded).
- **recall** = distinct injections matched / 24.
- Judge-only precision (GENUINE_BUG rate) is reported as a secondary diagnostic.

## Honest limitations (written before results)

- Single run, one corpus, one judge model — a measurement, not a leaderboard. Judge-relativity is
  real (we measured 39% cross-family judge agreement elsewhere); transcripts let you re-judge.
- AST mutations are a specific defect class; a system tuned for security smells may under-perform
  here without being worse in general.
- The corpus generator and the injected-defect catalog are ours — systems were not tuned on them,
  but we built both. The seal prevents US from moving ground truth after seeing results; it cannot
  prevent the defect class itself from favoring some reviewer style.
- H1 (a prior, not a gate): no system exceeds 0.5 recall at ≥0.5 precision. Published either way.
