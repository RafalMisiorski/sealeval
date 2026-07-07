# FIXES.md — prioritized work list (external code scan, 2026-07-07)

Findings verified empirically (tests run, mutations exercised on live ASTs). The
`benchmark/run1` integrity chain was independently verified (seal → findings → reveal commit
order, cryptographic seal check, tamper detection) — do not touch `benchmark/run1/` history.
**Out of scope for now: PyPI publication** — the owner will handle the release at the end.
Preparing metadata (`py.typed`, `[project.urls]`) is in scope; uploading is not.

Already fixed, no action needed: hallucinated-path claims now count against precision
(`1de126d`); `_off_by_one` now narrows the stop bound, never the step (`a66b2e7`).

## P1 — dead code + private-project residue

1. **`src/sealeval/judge.py` — dead code and phantom parameters.**
   - `_judge_call` (lines ~173-194) is unreachable: `judge_claims` raises `ValueError` at
     line ~262 before it could ever be called. Delete it, or wire it in for real.
   - `judge_claims` accepts and documents `cwd` and `purpose` ("cwd pinned to the scope root")
     but never uses either. Remove them, or implement the documented behavior. A documented
     no-op parameter is worse than no parameter.

2. **References to the private "Neural Holding" project leak into the public library.** Remove or
   rewrite each so the repo stands alone:
   - `src/sealeval/__init__.py:14` — "Harvested from the Neural Holding code-review benchmark"
   - `src/sealeval/judge.py:4-7` — `opus_client`, `call_gemini`, "NH's own backends"
   - `src/sealeval/sealing/prereg.py:15` — `opus_client._PRICE_PER_M_TOKENS`

## P2 — CI + robustness

3. **CI.** Add `.github/workflows/ci.yml`: Python 3.10–3.13 matrix, `pytest -q`, plus a smoke run
   of `examples/verify_seal_breaks.py` (it's offline and is the repo's best demo — keep it green).

4. **`src/sealeval/sealing/keyseal.py:78-85` — the reveal gate only checks file existence.** An
   empty or non-JSON `findings/<system>.json` passes the gate. Tighten: require the file to parse
   as JSON and contain a non-empty claims list. Update the corresponding test.

5. **`src/sealeval/mutation/seeder.py:151` uses the private `catalog._seg`.** Promote `_seg` to a
   public helper (e.g. `catalog.source_segment`) or move it to a shared util — no cross-module
   private access.

## P3 — coverage + packaging prep (no upload)

6. Missing test coverage worth adding: `max_total`, `copy_clean_corpus`, the JSON manifest path of
   `load_scope`, `_excerpt` windowing, multibyte source files.

7. Add `py.typed` and `[project.urls]` in `pyproject.toml`. README's `pip install sealeval`
   (README.md, Install section) stays as-is only if the owner publishes; until then prefer
   `pip install git+https://github.com/RafalMisiorski/sealeval`. Do **not** publish.

8. Longer-term (not this pass): grow the mutation catalog beyond the current archetypes — the
   niche is credible but thinly covered.
