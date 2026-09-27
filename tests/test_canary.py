"""canary: plant on a throwaway git repo, key stays out of the branch, seal verifies, score computes recall / liveness / noise."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sealeval.canary import load_findings, plant, score
from sealeval.sealing.keyseal import verify_seal

SRC = (
    "def total(items, extra=None):\n"
    "    # sum the items and the extras\n"
    "    s = 0\n"
    "    for i in range(len(items)):\n"
    "        s += items[i]\n"
    "    if extra is not None:\n"
    "        s += extra\n"
    "    return s\n"
    "\n"
    "\n"
    "def pick(d, k):\n"
    "    \"\"\"Return the value for k.\n"
    "\n"
    "    Falls back to zero when the key is missing.\n"
    "    \"\"\"\n"
    "    return d.get(k, 0)\n"
    "\n"
    "\n"
    "def guard(x):\n"
    "    try:\n"
    "        return int(x)\n"
    "    except ValueError:\n"
    "        raise\n"
)


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root"], cwd=r, check=True)
    for i in range(3):
        (r / ("mod%d.py" % i)).write_text(SRC, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "src"], cwd=r, check=True)
    return r


def test_plant_creates_branch_keeps_key_out_and_seal_verifies(tmp_path):
    r = _repo(tmp_path)
    res = plant(r, n=3, seed=5, state_dir=tmp_path / "state")
    assert len(res.planted) == 3 and len({p.file for p in res.planted}) == 3
    branches = subprocess.run(["git", "branch", "--list"], cwd=r, capture_output=True, text=True).stdout
    assert res.branch in branches
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", res.branch], cwd=r, capture_output=True, text=True).stdout
    assert ".sealeval/key.sealed" in tree and "key.json" not in tree
    sealed = json.loads(subprocess.run(["git", "show", "%s:.sealeval/key.sealed" % res.branch], cwd=r, capture_output=True, text=True).stdout)
    key = json.loads(res.key_path.read_text(encoding="utf-8"))
    assert verify_seal(key, sealed)
    # the working tree is back on main and clean
    assert subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=r, capture_output=True, text=True).stdout.strip() == "main"
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=r, capture_output=True, text=True).stdout.strip()
    # every planted site is a real change on the branch and the file still compiles
    for p in res.planted:
        new = subprocess.run(["git", "show", "%s:%s" % (res.branch, p.file)], cwd=r, capture_output=True, text=True).stdout
        assert new != SRC
        compile(new, p.file, "exec")


def test_plant_is_seeded_and_refuses_dirty_tree(tmp_path):
    r = _repo(tmp_path)
    a = plant(r, n=2, seed=11, state_dir=tmp_path / "s1", branch="canary/a")
    b = plant(r, n=2, seed=11, state_dir=tmp_path / "s2", branch="canary/b")
    assert [(p.file, p.line, p.archetype) for p in a.planted] == [(p.file, p.line, p.archetype) for p in b.planted]
    (r / "dirty.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "dirty.txt"], cwd=r, check=True)
    try:
        plant(r, n=1, seed=1, state_dir=tmp_path / "s3")
        assert False, "should refuse a dirty tree"
    except RuntimeError as e:
        assert "not clean" in str(e)


def test_score_recall_liveness_noise(tmp_path):
    r = _repo(tmp_path)
    res = plant(r, n=3, seed=7, state_dir=tmp_path / "state")
    key = json.loads(res.key_path.read_text(encoding="utf-8"))
    hits = [{"file": key[0]["file"], "line": key[0]["line"] + 1, "claim": "bug"}, {"file": "mod9.py", "line": 3, "claim": "not planted"}]
    out = score(key, res.seal, hits)
    assert out["seal_verified"] and out["liveness"] and out["found"] == 1 and out["noise_findings"] == 1 and len(out["missed"]) == 2
    assert sum(v["found"] for v in out["by_archetype"].values()) == 1
    silent = score(key, res.seal, [])
    assert not silent["liveness"] and silent["found"] == 0
    f = tmp_path / "f.json"
    f.write_text(json.dumps(hits), encoding="utf-8")
    assert len(load_findings(f)) == 2
