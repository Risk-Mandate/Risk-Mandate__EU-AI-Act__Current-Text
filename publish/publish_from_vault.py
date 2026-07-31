#!/usr/bin/env python3
"""
JOB 1 of the two-pass pipeline (runs on Dinis's machine / trusted CI ONLY -
never in the public repo's Actions): copy CHANGED generated files from the
authoring vault into this repo, commit, stop. Job 2 (deploy-pages.yml)
takes over on push.

Least privilege: use a READ-ONLY vault key if available; the full key never
belongs in any CI secret store for this job. NO vault key is ever committed.

Delta: compares the vault's provisions root hash (publish/published-state
.json in this repo) against the vault's current root hash and copies only
changed subtrees; first run is a full copy.

Usage:
  python3 publish/publish_from_vault.py --vault /path/to/local/vault/clone
  (clone/pull the vault first with sgit; this script never holds the key)
"""
import argparse, filecmp, hashlib, json, os, shutil, subprocess, sys

GENERATED = ["exports", "data", "provisions"]   # vault -> repo, one way

def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    changed = []
    for top in GENERATED:
        src_root = os.path.join(a.vault, "public-repo", top) \
            if os.path.isdir(os.path.join(a.vault, "public-repo", top)) \
            else os.path.join(a.vault, top)
        if not os.path.isdir(src_root):
            print(f"skip {top}: not in vault", file=sys.stderr); continue
        for dirpath, _, files in os.walk(src_root):
            rel = os.path.relpath(dirpath, src_root)
            for f in files:
                s = os.path.join(dirpath, f)
                d = os.path.join(repo, top, rel, f)
                if os.path.exists(d) and filecmp.cmp(s, d, shallow=False):
                    continue
                changed.append((s, d))
    if not changed:
        print("delta empty - nothing to publish"); return 0
    print(f"{len(changed)} changed files")
    if a.dry_run:
        for s, d in changed[:50]: print("  ->", os.path.relpath(d, repo))
        return 0
    for s, d in changed:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
    state = {"published_files": len(changed)}
    rp = os.path.join(repo, "provisions", "index.json")
    if os.path.exists(rp):
        state["root_hash"] = sha256(rp)
    json.dump(state, open(os.path.join(repo, "publish", "published-state.json"), "w"), indent=1)
    subprocess.run(["git", "-C", repo, "add", "-A"] , check=True)
    subprocess.run(["git", "-C", repo, "commit", "-m",
                    f"publish: {len(changed)} generated files from vault"], check=True)
    print("committed - push to trigger job 2 (deploy)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
