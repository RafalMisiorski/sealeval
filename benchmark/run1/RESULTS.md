# sealeval public benchmark — run1 RESULTS

Post-run report. The pre-registration (`PROTOCOL.md`, `prereg.lock.json`, `key.sealed`) and the
system findings (`findings/*.json`) were committed and pushed **before** the key was revealed —
the git history of this repo is the integrity proof. Reveal verified the plaintext key against the
public seal (`sha256`, salt-prefixed, n=24) before writing `key.json`. Judging was blind and
refute-by-default; every judge reply is published under `transcripts/`.

## Headline

- **The pre-registered prior H1 was FALSIFIED.** H1 (written before results): *"no system exceeds
  0.5 recall at ≥0.5 precision."* **codex** reached **recall 0.75 at precision 0.62** on the full
  sealed corpus — both above 0.5. Published as pre-registered, either way.
- This is one run, one corpus, one judge model, N=24 injected defects. It is a **measurement, not a
  leaderboard.** Read the caveats before quoting a number.

## Primary result — frozen metrics (exactly as pre-registered)

`precision = judged-GENUINE_BUG claims that land on a sealed injection (same file, |Δline| ≤ 1) /
ALL claims by that system` (claims on nonexistent/out-of-scope files count against precision).
`recall = distinct sealed injections matched / 24.`

| system | claims | TP | **precision** | **recall** | judge genuine-rate |
|---|---|---|---|---|---|
| claude (Claude Sonnet via claude CLI) | 17 | 8 | **0.471** | **0.333** (8/24) | 0.471 |
| codex (OpenAI Codex via `codex exec`) | 29 | 18 | **0.621** | **0.750** (18/24) | 0.862 |
| gemini (Gemini via Antigravity `agy --print`) † | 13 | 10 | **0.769** | **0.417** (10/24) | 0.846 |

† gemini reviewed **15 of 19 files**. The 4 largest (`adapters.py`, `models.py`, `sessions.py`,
`utils.py`) could not be delivered — see the transport incident below. Those 4 files hold **10 of
the 24 injections**, so gemini's *structural* recall ceiling here is 14/24 = 0.583. Its recall
above is over the full 24 (the 10 unreachable injections count as misses) — a disclosed
consequence of the transport, **not** a scoring artifact and **not** goalpost-moving: we did not
change the frozen metric to rescue it.

## Fair head-to-head — common subset (secondary diagnostic, not the frozen metric)

To compare per-file detection without the gemini coverage handicap: score only the **11 injected
files every system reviewed identically inline** (14 injections). This is a *secondary* view added
after the run; the frozen metric above is unchanged.

| system | subset claims | subset TP | subset precision | subset recall (/14) |
|---|---|---|---|---|
| claude | 9 | 2 | 0.222 | 0.143 |
| codex | 19 | 11 | 0.579 | 0.786 |
| gemini | 13 | 10 | 0.769 | 0.714 |

On the fair subset, codex and gemini are close (codex trades precision for recall; gemini the
reverse) and claude is weaker on the smaller files — most of claude's full-corpus recall came from
the 4 large files (6 of its 8 matches), which the subset excludes. With N=24 and a single run these
gaps are inside the noise; treat ordering as suggestive, not established.

## The agy transport incident (disclosed in full)

The gemini leg is driven through the sanctioned `agy --print` ConPTY path
(`scripts/llm_call.call_gemini` in the Neural Holding repo). `agy --print` takes the prompt as a
**command-line argument**, and Windows `CreateProcess` caps a command line near 32,767 chars. Four
corpus files, once numbered and wrapped in the prompt, exceed a safe 32,000-char guard
(`models.py` 47,970; `utils.py` 42,392; `sessions.py` 39,102; `adapters.py` 32,125) and fail to
spawn (`"The filename or extension is too long"`). claude and codex read the prompt on **stdin** and
have no such limit.

We deliberately did **not** rescue those files by switching gemini to a different input modality
(e.g. writing the file into agy's workspace and having it read the file with its own tools). That
would review 4 files under a different modality than the other 15 and than the other two systems —
a confound that the very point of this benchmark (an honest, comparable measurement) forbids. The
honest cost is that gemini's frozen recall is penalized for 10 unreachable injections; the
common-subset diagnostic exists precisely so a reader can see gemini's per-file detection without
that penalty. `findings/gemini_coverage.json` records exactly which files were DNF.
`reproduce_systems.py`'s bring-your-own-model **stdin** seam has no argv limit — a stdin-based
gemini wrapper would achieve full coverage.

## What "precision" here does and does not mean

Precision is measured **against the sealed injection key only**. A claim the judge calls a genuine
bug but that is *not* one of our 24 injections counts **against** precision. Breakdown of
judged-GENUINE claims that did **not** match the key:

- claude: **0** — every genuine-judged claim landed on a sealed injection.
- codex: **7** — several describe plausible **pre-existing** behaviors in real `requests` code
  (e.g. `status_codes.py` early-hints mapping, `cookies.py` domain-match logic, an `auth.py` None
  path), not hallucinations. If any are real upstream bugs, codex's *real-world* precision is
  **higher** than the 0.621 shown.
- gemini: **1** (the same `cookies.py` domain-logic observation codex made).

So precision-against-the-sealed-set is a **floor**, not a ceiling, on real-bug precision — a
structural limitation of sealed ground truth (it can only score what we injected). All 59 judge
transcripts are published under `transcripts/` so you can inspect and re-judge every call.

## Integrity chain (auditable in `git log`)

1. `b8c3915` / `6da85cb` — `PROTOCOL.md` + `prereg.lock.json` + `key.sealed` (the sha256
   commitment over the 24 injections). **Before any system ran.**
2. `bc5a745` — `findings/{claude,codex,gemini}.json` + `gemini_coverage.json`. Systems' outputs,
   produced while the key was still sealed. **Before reveal.**
3. this commit — `key.json` (revealed plaintext, hash-verified against the seal), `transcripts/`,
   `*_verdicts.json`, `scores.json`, this file.

The plaintext key never lived in the repo before step 3, and never touched the systems (it sat in a
local `.secret`-class path). The seal makes it impossible for **us** to have moved ground truth
after seeing results; it cannot make the injected-defect class representative of all bugs.

## Honest limitations (restated from the pre-registration, plus what the run surfaced)

- **N=24, single run, one judge model.** Judge-relativity is real (≈39% cross-family judge
  agreement measured elsewhere in sealeval's calibration work); transcripts let you re-judge with
  another model.
- **AST mutations are a specific defect class** (inverted conditions, null-deref, swallowed
  exceptions, wrong operator, off-by-one). A system tuned for security smells could under-perform
  here without being worse in general.
- **We built both the corpus generator and the injection catalog.** The seal prevents post-hoc
  ground-truth edits; it does not prevent the defect class from favoring some reviewer style.
- **Precision is a floor** (see above): pre-existing real bugs in `requests` are not in the key.
- **The gemini transport handicap** is disclosed, not corrected; the subset diagnostic isolates it.

## Reproduce

Corpus (byte-identical) + systems: see `PROTOCOL.md`. Verify the seal:

```bash
python - <<'EOF'
import json, sealeval as se
key  = json.load(open("benchmark/run1/key.json"))
seal = se.sealing.read_seal("benchmark/run1/key.sealed")
print("seal verifies:", se.verify_seal(key, seal))   # -> True
EOF
```

Bring your own model via the stdin seam (no argv limit):

```bash
BENCH_SYSTEM_CMD="your-cli --stdin" python benchmark/run1/reproduce_systems.py \
  --corpus ../bench_corpus --out findings/yoursystem.json
```
