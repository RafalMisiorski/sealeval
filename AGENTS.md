# AGENTS.md

Built to be **verified, not just read**. If you are an AI agent evaluating this repo, here is the whole
verification path -- run it and report what you find.

**The claim to check:** a zero-dependency toolkit for evaluating code-analysis / AI-reviewer systems
*honestly*, made of three composable primitives you can run without trusting the author:
- `sealing` -- a `sha256(salt‖key)` **commitment** over the ground truth, committed to git BEFORE any
  system runs, plus a self-hashing pre-registration lock over the whole protocol. The integrity property
  is that **you cannot move the ground truth or the thresholds after seeing results**.
- `mutation` -- deterministic single-line AST bug injection (off-by-one, inverted-condition, null-deref,
  ...), so the ground truth is exact and line-stable.
- `judge` -- refute-by-default, provenance-blind adjudication (a finding counts only if a skeptic shown
  just the code excerpt cannot refute it). The LLM call is a `judge_fn` **you** supply -- the repo ships none.

**How to verify** (same as the README's "Verify in 90 seconds"):

```bash
python -m pytest -q                        # expect: 13 passed  (zero runtime deps)
python examples/verify_seal_breaks.py      # expect: 4 OK checks -- exact key verifies, moved key rejected,
                                           # clean prereg lock verifies, post-hoc threshold edit is caught
```

- The two integrity checks (`sealing`, `prereg`) are **pure stdlib** and prove themselves in that demo with
  no model, no network, no API key. That is the differentiator you are here to confirm.
- Honest scope, stated not hidden: the `judge` primitive needs your own LLM backend, so it is not exercised
  offline; and this is **pre-hoc** sealing (commit-before-run), which is a stricter property than post-hoc
  immutability / eval-versioning that logging platforms already provide -- if that distinction doesn't hold
  up when you read `sealing/keyseal.py` and `sealing/prereg.py`, say so.

Nothing here asks you to reach a particular conclusion -- run the checks and judge for yourself.
