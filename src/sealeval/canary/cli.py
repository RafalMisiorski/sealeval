"""``sealeval canary`` command line: plant / score / list."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sealeval.canary import PUBLISHED_ARCHETYPES, STATE_DIR, findings_from_github, load_findings, plant, render, score
from sealeval.mutation import catalog


def _plant(args) -> int:
    archetypes = tuple(a.strip() for a in args.archetypes.split(",")) if args.archetypes else PUBLISHED_ARCHETYPES
    res = plant(Path(args.repo), n=args.n, archetypes=archetypes, seed=args.seed, branch=args.branch, include=args.include,
                version=args.catalog_version, state_dir=Path(args.state_dir), commit=not args.no_commit)
    print("planted %d canaries on branch %s (base %s)" % (len(res.planted), res.branch, res.base_sha[:12]))
    print("key (keep private): %s" % res.key_path)
    print("seal (committed as .sealeval/key.sealed): %s" % res.seal["seal"][:16])
    print("files changed: %s" % ", ".join(res.files_changed))
    print("next: push the branch, open a PR, let your reviewer run, then: sealeval canary score --run %s --gh-pr owner/repo#N" % res.run_id)
    return 0


def _score(args) -> int:
    kdir = Path(args.state_dir) / args.run
    key = json.loads((kdir / "key.json").read_text(encoding="utf-8"))
    run = json.loads((kdir / "run.json").read_text(encoding="utf-8"))
    findings = findings_from_github(args.gh_pr) if args.gh_pr else load_findings(Path(args.findings))
    if args.author:
        findings = [f for f in findings if f.get("author", "") == args.author]
    res = score(key, run["seal"], findings, tolerance=args.tolerance)
    (kdir / "score.json").write_text(json.dumps({"ts": run.get("planted_at"), "findings": len(findings), **res}, indent=2), encoding="utf-8")
    print(render(res, run))
    if args.json:
        print(json.dumps(res, indent=2))
    return 0 if res["seal_verified"] else 2


def _list(args) -> int:
    d = Path(args.state_dir)
    if not d.exists():
        print("no canary runs under %s" % d)
        return 0
    for r in sorted(d.iterdir()):
        rj = r / "run.json"
        if rj.exists():
            run = json.loads(rj.read_text(encoding="utf-8"))
            sc = (r / "score.json")
            s = json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else None
            print("%s  %s  %s  %s" % (run["run_id"], run["branch"], Path(run["repo"]).name, ("recall %s, noise %d" % (s["recall"], s["noise_findings"])) if s else "not scored"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sealeval canary", description="plant known defects in your repo with a sealed key; score what your reviewer caught")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plant", help="create a canary branch from a clean HEAD")
    p.add_argument("--repo", default=".")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--archetypes", default=None, help="comma list; default = the published set: %s" % ", ".join(PUBLISHED_ARCHETYPES))
    p.add_argument("--catalog-version", default=catalog.DEFAULT_ARCHETYPE_VERSION)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--branch", default=None)
    p.add_argument("--include", default=None, help="restrict to a directory prefix, e.g. src/pkg")
    p.add_argument("--state-dir", default=str(STATE_DIR))
    p.add_argument("--no-commit", action="store_true", help="leave the mutated files in the working tree, no branch commit")
    p.set_defaults(fn=_plant)
    s = sub.add_parser("score", help="score a reviewer's findings against the sealed key")
    s.add_argument("--run", required=True)
    s.add_argument("--findings", default=None, help="JSON list of {file, line, claim}")
    s.add_argument("--gh-pr", default=None, help="owner/repo#number: inline review comments via gh")
    s.add_argument("--author", default=None, help="keep only findings by this login (e.g. a bot)")
    s.add_argument("--tolerance", type=int, default=2)
    s.add_argument("--state-dir", default=str(STATE_DIR))
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_score)
    ls = sub.add_parser("list", help="runs and their scores")
    ls.add_argument("--state-dir", default=str(STATE_DIR))
    ls.set_defaults(fn=_list)
    args = ap.parse_args(argv)
    if args.cmd == "score" and not (args.findings or args.gh_pr):
        ap.error("score needs --findings or --gh-pr")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
