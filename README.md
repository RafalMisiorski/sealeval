# sealeval

**Pre-registered, sealed-ground-truth evaluation of code-analysis systems.** Zero
dependencies. You bring the LLM call.

Most "we evaluated our AI code reviewer" results are slop: hand-scored rubrics, raw
find-counts, judges that know which system they're grading, thresholds chosen after seeing
the numbers. `sealeval` is the opposite by construction — the one signal that's hard to
fake is a benchmark whose ground truth was **sealed and committed before any system ran**,
adjudicated **blind** and **refute-by-default**, scored on **confirmed** findings.

It is three small, composable primitives:

| primitive | what it does |
|---|---|
| `sealeval.mutation` | inject **sealed AST bugs** into clean source (off-by-one, inverted-condition, wrong-operator, null-deref, swallowed-exception). Single-line edits keep every other line number byte-stable, so the injected diff is exactly one line and the key's line numbers are exact. Deterministic. |
| `sealeval.sealing` | a **binding commitment**: `sha256(salt‖key)` you commit *before* any system runs (the commit timestamp proves the key was fixed first), revealed only after — plus a self-hashing pre-registration of your whole protocol that `verify()` re-checks for drift. |
| `sealeval.judge` | **refute-by-default** adjudication: each claim is judged in isolation, provenance stripped, against a windowed code excerpt; verdicts are `GENUINE_BUG` / `REAL_NOT_BUG` / `REFUTED`, defaulting to REFUTED on any garbage. The LLM call is a `judge_fn` you supply. |

## Verify in 90 seconds (zero setup)

Zero dependencies, no API key, no network. Every claim below runs offline on a clean clone.

```bash
python -m pytest -q                        # 13 tests pass (seal roundtrip, tamper, reveal-gate, prereg drift, judge)
python examples/verify_seal_breaks.py      # the differentiator, deterministic: the seal verifies the exact
                                           # ground truth and rejects it the instant one byte moves; the prereg
                                           # lock catches a GO/KILL threshold edited after the fact
```

The judge primitive needs *your* LLM call (`judge_fn`) so it can't run offline — but the two things
that make a verdict defensible (the sealed key can't move; the goalposts can't move) are pure stdlib
and prove themselves above with no model at all.

## Install

```bash
pip install sealeval        # or, from a clone:  pip install -e .
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
- **Confirmed, not flagged.** Pair the judge verdict with a sealed-key match: a true
  positive is `GENUINE_BUG` **and** it lands on an injected site. Raw "found more files" is
  exactly the metric this design exists to debunk (more flags usually means lower precision).

MIT licensed. Extracted from a real pre-registered benchmark that returned a documented
KILL — the methodology is the deliverable, not a win.
