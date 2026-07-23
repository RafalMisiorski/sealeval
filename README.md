# sealeval

### Your eval says 8/8. That is `[0.68, 1.0]` — and you haven't yet checked whether your judge can tell an obvious case apart.

Check the first one right now, on your own number, without installing a framework or wiring a
judge: `sealeval check --accuracy 8/8`. The demo additionally refuses to seal a design whose
baseline sits at the ceiling; controls that VOID a run on a missed obvious case are covered in the
test suite (`test_panel_void_short_circuits`).

> *Why this exists:* we ran our own tool's "8/8" claim through a cross-vendor panel with
> controls and it came back 0.43, CI [0.16, 0.75] — indistinguishable from chance. **That run
> was internal and its raw labels are not shipped in this repo, so treat it as an anecdote,
> not evidence.** The evidence for this library is the code, the tests and the demo below.

**How:** ground truth sealed *before* the run, judges from *different vendors*, an interval
instead of a flattering point estimate, and an apparatus check that refuses to seal a design
which cannot show an effect in the first place.

```bash
pip install -e .
sealeval check --accuracy 8/8        # ten seconds, on a number you already have
sealeval check --labels examples/judges.csv   # kappa, controls, cross-family, on labels you have
sealeval demo                        # 30-second offline tour, no API key, no LLM
```

**Start here, integrate nothing.** `sealeval check` works on what is already in front of you — a
score, or a CSV of judge labels. If it tells you something uncomfortable, the rest of the package
is how you fix it.

The CSV is one column per judge, one row per case. Optional: `truth` (your pre-registered label),
`tool` (the system under test), and `control` — a non-empty control name marks that row as an
OBVIOUS case whose `truth` the judges must get right, or the whole run is VOID. See
[`examples/judges.csv`](examples/judges.csv).

## This is not only for code review

Any time an **LLM judges something and you report a number**, the same four failure modes apply: a
judge that cannot tell obvious cases apart, judges from one vendor sharing a taste, a point
estimate hiding its interval, and a threshold picked after seeing the results. `sealeval.measure`
is **domain-agnostic** — it operates on labels, not on code — so it applies to prompt A/Bs, RAG
answer grading, agent-skill lift, moderation, extraction quality, anything judged.

The AST mutation seeder is the one code-specific piece, and it is **optional**: it manufactures
sealed ground truth when you don't have any. If you already have labels, skip it.

Zero dependencies. You bring the LLM call (or none at all — `check` and the demo need no model).

Most "we evaluated our AI" results are slop: hand-scored rubrics, raw
find-counts, judges that know which system they're grading, thresholds chosen after seeing
the numbers. `sealeval` is the opposite by construction — the one signal that's hard to
fake is a benchmark whose ground truth was **sealed and committed before any system ran**,
adjudicated **blind** and **refute-by-default**, scored on **confirmed** findings.

It is four small, composable primitives:

| primitive | what it does |
|---|---|
| `sealeval.measure` **(start here, domain-agnostic)** | works on labels from ANY judged task: a **Round 0** pilot that validates the apparatus before you seal (controls discriminate? metric has headroom? enough runs parsed?), **cross-family** panels, **Wilson CI**, **Cohen/Fleiss kappa**, a **metric-discrimination gate**, and `match_findings` for confirmed-not-flagged scoring. See [`measure/README.md`](src/sealeval/measure/README.md). |
| `sealeval.mutation` *(optional, code-specific)* | inject **sealed AST bugs** into clean source (off-by-one, inverted-condition, wrong-operator, null-deref, swallowed-exception). Single-line edits keep every other line number byte-stable, so the injected diff is exactly one line and the key's line numbers are exact. Deterministic. |
| `sealeval.sealing` | a **binding commitment**: `sha256(salt‖key)` you commit *before* any system runs (the commit timestamp proves the key was fixed first), revealed only after — plus a self-hashing pre-registration of your whole protocol that `verify()` re-checks for drift. |
| `sealeval.judge` | **refute-by-default** adjudication: each claim is judged in isolation, provenance stripped, against a windowed code excerpt; verdicts are `GENUINE_BUG` / `REAL_NOT_BUG` / `REFUTED`, defaulting to REFUTED on any garbage. The LLM call is a `judge_fn` you supply. |

## Install

Not on PyPI yet — install from source:

```bash
git clone <this repo> && cd sealeval
pip install -e ".[test]"    # zero RUNTIME deps; [test] adds pytest only
sealeval demo               # the 30-second tour
python -m pytest            # 60 tests
```

## The pipeline

```python
from pathlib import Path
import sealeval as se

# 1. SEED — inject sealed ground-truth bugs into a clean copy of the corpus
res = se.seed_corpus(Path("clean_repo"), Path("corpus"), per_file=3, seed=7,
                     per_archetype_cap=8)               # balanced across bug classes
se.dump_injection_key(res.records, Path("run/.secret/key.json"))

# 2. SEAL — commit this BEFORE running any system (the timestamp is the proof)
seal = se.make_seal([r.to_dict() for r in res.records], count=res.mutations)
se.sealing.write_seal(seal, Path("run/key.sealed"))     # git add + commit this file now

# 3. ... your systems review `corpus/` and emit findings: {id, file, line, claim} ...

# 4. REVEAL (only after all systems ran) + JUDGE blind against the sealed key
se.reveal([r.to_dict() for r in res.records], seal, Path("run/key.json"))
report = se.judge_claims(findings, scope="corpus", model="your-model", judge_fn=my_judge)
print(report.genuine_bug, report.precision_genuine_bug)
```

## Wiring `judge_fn` (you own the LLM call — sealeval ships none)

`judge_fn(system_prompt, user_prompt, model) -> str` returns the judge's raw reply.

```python
# Anthropic
import anthropic
client = anthropic.Anthropic()
def my_judge(system, user, model):
    r = client.messages.create(model=model, max_tokens=600, temperature=0,
                               system=system, messages=[{"role": "user", "content": user}])
    return r.content[0].text

# OpenAI
from openai import OpenAI
oai = OpenAI()
def my_judge(system, user, model):
    r = oai.chat.completions.create(model=model, temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return r.choices[0].message.content

# A CLI via subprocess — pipe the prompt on stdin (not a one-shot -p flag)
import subprocess
def my_judge(system, user, model):
    return subprocess.run([model], input=f"{system}\n\n{user}",
                          capture_output=True, text=True, timeout=300).stdout
```

For a non-self-preferring panel, call `judge_claims` once per model and require agreement
(e.g. count a finding confirmed only if every panelist returns `GENUINE_BUG`).

## Why each piece matters

- **Sealed key, not memory.** `verify_seal(revealed, seal)` fails if a single byte of the
  key moved — so you cannot quietly re-pick ground truth after seeing results. Commit
  `key.sealed` to git before the run and the history is the proof.
- **Refute-by-default.** A finding counts only if a skeptic, shown just the code excerpt,
  cannot refute it — which kills the plausible-but-wrong findings that inflate naive scores.
- **Confirmed, not flagged.** `measure.match_findings(findings, key, line_tolerance=2)` does
  this for you: a true positive is `GENUINE_BUG` **and** it lands within the tolerance of an
  injected site, and each injection can be claimed only once (so ten findings shotgunned at one
  bug score one hit). Pre-register `line_tolerance` — widening it after seeing results is the
  goalpost-move the seal exists to prevent. Raw "found more files" is
  exactly the metric this design exists to debunk (more flags usually means lower precision).

## Benchmark runs in this repo

## A public run you can audit — `benchmark/run1`
whole point. Every judge reply is in `benchmark/run1/transcripts/`; the metric is a *floor* (a
[`benchmark/run1/RESULTS.md`](benchmark/run1/RESULTS.md) and
[`PROTOCOL.md`](benchmark/run1/PROTOCOL.md).
pre-registered, either way — see [`benchmark/run2/RESULTS.md`](benchmark/run2/RESULTS.md).

MIT licensed. Extracted from a private pre-registered benchmark whose own verdict was a KILL;
that write-up is not shipped here, so treat this line as provenance, not evidence. The
methodology is the deliverable, not a win.
