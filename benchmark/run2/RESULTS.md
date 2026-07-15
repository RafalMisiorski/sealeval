# sealeval public benchmark — run2 RESULTS

Post-run report. Pre-registration and ordering: see the **provenance disclosure** in
[`PROTOCOL.md`](PROTOCOL.md) — run2 executed in a private repo whose history holds the
seal→findings→reveal ordering proof; everything below is recomputable offline from this export
(`python score_run2.py`, stdlib only; `key.json` verifies against `key.sealed`; the prereg lock
self-hash verifies).

## Headline

- **Both pre-registered hypotheses came back FALSE, so the sealed decision rule fired KILL** on
  the premise being tested (that a two-model union buys coverage): union recall lift over the
  best solo arm = **0.000** (< the 0.05 kill bar), and the union *dilutes* precision.
- **The interesting finding is where the misses live: both frontier models fail in the SAME
  zone.** Complementarity was one-sided and marginal — GPT-5.6-sol caught 2 injections Fable 5
  missed; Fable 5 caught **0** that GPT-5.6-sol missed.
- One run, one corpus, N=24, two systems, panel of two judges. A **measurement, not a
  leaderboard.**

## Primary result — frozen metrics (exactly as pre-registered)

TP = both panel judges say GENUINE_BUG **and** the claim lands on a sealed injection
(same file, |Δline| ≤ 1). Precision over ALL claims; recall over 24 injections.

| system | claims | TP | **precision** | **recall** |
|---|---|---|---|---|
| fable5 (Claude Fable 5) | 23 | 18 | **0.783** | **0.750** (18/24) |
| gpt56sol (GPT-5.6-sol) | 25 | 20 | **0.800** | **0.833** (20/24) |
| union (dedup file,line) | 27 | 20 | 0.741 | 0.833 (20/24) |

- H1 (two-sided complementarity, ≥3 each way): **FALSE** — only-gpt56sol = {`cookies.py:87`
  (null_deref), `models.py:1117` (swallowed_exception)}; only-fable5 = **∅**.
- H2 (union lift ≥ +0.10 recall at ≥ 0.50 precision): **FALSE** — lift 0.000; precision drops
  0.800 → 0.741 (the second model adds claims, not catches).
- Judge panel disagreement rate: **12.5%** (diagnostic; disagreement resolves to not-confirmed,
  which is the refute-by-default posture applied to the panel).

## Where the misses live — shared failure zone (per archetype recall)

| archetype | injected | fable5 | gpt56sol |
|---|---|---|---|
| inverted_condition | 10 | 10 | 10 |
| wrong_operator | 5 | 5 | 5 |
| swallowed_exception | 4 | 2 | 3 |
| null_deref | 5 | 1 | 2 |

Both models are ~perfect on control-flow/operator flips, and both are weak on exactly the same
archetypes. On this evidence the miss-zone is a property of the **task** (subtle single-line
data-flow defects), not of the vendor — which is precisely why the union buys nothing: the
second model re-finds the first model's catches and shares its blind spots.

## Context vs run1

Same clean repo, fresh seed, so injection sets differ — directional context only:
run1's claude arm (Sonnet-tier) scored 0.333R/0.471P; run2's Fable 5 arm scores 0.750R/0.783P.
run1's codex arm scored 0.750R/0.621P; run2's GPT-5.6-sol scores 0.833R/0.800P. The frontier
tier roughly doubles the weaker arm and materially cleans up precision on the stronger one —
while leaving the same archetype blind spots.

## Deviations + limitations (disclosed rather than papered over)

- **Raw judge replies were not persisted** in run2 (run1 published every judge transcript). The
  parsed verdicts — including each judge's mechanism sentence — are in
  `findings/*_verdicts_*.json`; the arm sweep transcripts are in `transcripts/`. A harness
  limitation, fixed forward, not affecting any number.
- **Two post-reveal scoring-script fixes** (analysis code only; frozen definitions untouched):
  a verdict field-name crash, and the union dedup initially keeping an unconfirmed duplicate
  copy — which produced the impossible recall(union) < recall(solo); fixed to "confirmed copy
  wins", the faithful reading of the pre-registered dedup. The KILL decision is unchanged under
  both the broken and fixed scorer.
- N=24, one repo (psf/requests), one task family, one run per arm, panel of exactly two judges.
