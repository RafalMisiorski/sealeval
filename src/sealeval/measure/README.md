# sealeval.measure — raised standard for blind-panel evaluation

Point estimates from a same-family panel ("8/8 vs blind consensus") are not evidence a
skeptical reviewer accepts. This module adds the three disciplines that make a blind-panel
verdict survive: **controls**, **cross-family judges**, and **confidence intervals +
chance-corrected agreement**. Zero dependencies; operates on already-collected labels, so
it is deterministic and testable without any model call.

## Round 0 — validate the APPARATUS before you seal (`round0_gate`)
The first pancake goes in the bin. Run a cheap pilot whose ONLY job is to find broken apparatus:
controls that don't discriminate, a metric with no headroom, runs that don't parse, uncontrolled
leakage. Then repair, THEN seal, THEN run for real.

**Hard rule: hypothesis results from Round 0 are INADMISSIBLE** — diagnostics only, never cited,
never decided on. Enforced structurally: `round0_gate` takes no lift/effect parameter, so a
Round-0 report cannot carry one.

**Repairs allowed between Round 0 and Round 1** must pass the OUTCOME-BLIND test: *would I accept
this fix if it worked against me?* ("17 must not match 117" — yes. "Use the finer metric because
the coarse one gave NULL" — no, that is RIGOR-INVERSION.) Record each repair before unblinding.

Replayed against this project's own spoiled runs, Round 0 catches all three apparatus defects
(low parse-rate, ceiling'd metric, void controls) and passes the corrected configuration.

**Wired into the seal** (`sealing.prereg.freeze`):

```python
r0 = measure.round0_gate(control_labels=..., control_truth=..., baseline_rate=...,
                         attempted=18, parsed=18, leak_controlled=True)
prereg.freeze(content, lock_path, round0=r0)        # refuses to seal unless r0 passed
prereg.freeze(content, lock_path, require_round0=True)  # makes Round 0 mandatory
```

`freeze(..., round0=r0)` **refuses to seal on unvalidated apparatus** (raises `PreRegError` with
the blocking reasons) and binds the receipt into the *hashed* content — so the lock itself proves
the apparatus was checked before the run. Backwards compatible: with no `round0` argument the
content and hash are bit-identical to before, so existing locks still verify (checked against a
real 2026-07-09 lock).

## The standard, in order
0. **Metric first (prereg-time, `metric_discrimination_gate`).** Before sealing a metric as
   primary, check the baseline has HEADROOM on it. If the baseline is already near the ceiling
   (e.g. module-recall baseline 0.90 -> headroom 0.10), the metric cannot reveal a lift and a
   NULL is CEILING-LIMITED, not evidence of no effect -- pick a finer metric (file-level
   baseline 0.30 -> ample headroom). Controls prove the JUDGE discriminates; this proves the
   METRIC does. (Learned from the meta-skill A/B: a sealed-but-ceiling'd primary produced an
   uninformative NULL.)
1. **Controls first (VOID-gate).** Include >=1 obvious positive and >=1 obvious negative
   control. If the panel misranks either, the run is VOID — a panel that cannot rank an
   obvious case is not a measuring instrument. Nothing downstream is interpreted.
2. **Cross-family judges.** Use >=2 model families (e.g. Anthropic + OpenAI). Same-family
   panels share taste bias; `evaluate` sets `cross_family=False` so a same-family run is
   flagged, never silently accepted.
3. **Interval + kappa, not a point.** `wilson_ci` for accuracy (does not degenerate to
   [1,1] at a perfect score the way bootstrap does), `cohens_kappa`/`fleiss_kappa` for
   chance-corrected inter-judge agreement. Low kappa is reported as a weak-conclusion flag.
4. **Rubric independence (caller's job).** The judge prompt must NOT reuse the tool's own
   vocabulary. A result that only holds under a prompt sharing the tool's words is a
   construction artifact (this is how a fake edge is manufactured). Ask the neutral question, not the tool's leading one.

## Usage
```python
from sealeval.measure import panel

r = panel.evaluate(
    n_cases=8,
    judge_labels={"fable5": [...], "gpt56sol": [...]},   # you collect these, any backend
    families={"fable5": "anthropic", "gpt56sol": "openai"},
    tool_labels=[...],                 # system under test (optional)
    ground_truth=[...],                # pre-registered labels (optional)
    control_judge_labels={"fable5": [...], "gpt56sol": [...]},
    control_truth={"pos_obvious": "F", "neg_obvious": "C"},
)
for line in panel.report_lines(r):
    print(line)
```
`evaluate` returns `void`, `controls`, `inter_judge_kappa_*`, `panel_vs_ground_truth`,
`tool_vs_panel` (with `ci95`), `no_consensus_cases`, `interpretation`.

The LLM collection that produces the labels stays in your app (it needs a model backend); this
package deliberately ships none — you call two vendors however you like and hand `evaluate` the
resulting label lists.

> **Caveat:** the run below was internal; its raw labels are NOT shipped in this repo and the
> paths referenced are not part of this package. Treat it as an anecdote explaining the design,
> not as evidence you can check here.

## First dogfood
Re-running Show-Me v3's "8/8" through this standard turned it into
`tool-vs-cross-family-panel = 0.43, CI95 [0.16, 0.75]` ("indistinguishable from chance"),
controls passing and inter-judge kappa 0.71 — i.e. the instrument works and the "8/8" was a
same-family + shared-vocabulary artifact.
