#!/usr/bin/env python3
"""Drive any code-review system over the benchmark corpus (bring-your-own-model).

The FROZEN prompt below is the one all three recorded systems received (sha256 of PROMPT is in
prereg.lock.json). To run YOUR system: set BENCH_SYSTEM_CMD to a command that reads the prompt on
stdin and prints the model's reply, then:

    python reproduce_systems.py --corpus ../bench_corpus --out findings/mysystem.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROMPT = (
    "You are reviewing a single Python source file for REAL, present defects (logic errors: "
    "inverted conditions, off-by-one, wrong operator/comparison, None/null dereference, swallowed "
    "exceptions). Report ONLY defects you are confident are real bugs in THIS file as shown. Do not "
    "report style issues, TODOs, missing features, or hypothetical improvements. Output ONLY a JSON "
    'array: [{"line": <int>, "claim": "<one-sentence defect description>"}]. If there are none, '
    "output []. Line numbers refer to the numbering shown."
)


def numbered(text: str) -> str:
    return "\n".join(f"{i+1}: {ln}" for i, ln in enumerate(text.splitlines()))


def extract_json_array(text: str):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cmd = os.environ.get("BENCH_SYSTEM_CMD", "").strip()
    if not cmd:
        raise SystemExit("Set BENCH_SYSTEM_CMD: a command reading the prompt on stdin, printing the reply.")
    corpus = Path(args.corpus)
    findings = []
    n = 0
    for p in sorted(corpus.rglob("*.py")):
        rel = p.relative_to(corpus).as_posix()
        full = PROMPT + f"\n\nFile: {rel}\n\n" + numbered(p.read_text(encoding="utf-8", errors="replace"))
        r = subprocess.run(shlex.split(cmd), input=full, capture_output=True, text=True,
                           encoding="utf-8", timeout=600)
        for item in extract_json_array(r.stdout or ""):
            try:
                n += 1
                findings.append({"id": f"c{n}", "file": rel,
                                 "line": int(item.get("line", 0)), "claim": str(item.get("claim", ""))[:400]})
            except (TypeError, ValueError):
                continue
        print(f"{rel}: total findings so far {len(findings)}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(findings, indent=2), encoding="utf-8")
    print(f"wrote {args.out} ({len(findings)} findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
