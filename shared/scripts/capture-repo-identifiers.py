#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

API_VERSION = "2026-03-10"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_CHECKS = {"docs-contract", "terraform-static"}


def fail(reason: str) -> None:
    print(f"REPO_IDENTIFIERS=FAIL reason={reason}", file=sys.stderr)
    raise SystemExit(1)


def gh_api(path: str) -> dict:
    cmd = ["gh", "api", "-H", f"X-GitHub-Api-Version: {API_VERSION}", path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        fail(f"gh api failed: {proc.stderr.strip()[:160]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail("gh api returned non-JSON output")
    return {}


def upsert(env_file: Path, name: str, value: str) -> None:
    script = Path(__file__).with_name("upsert-lab-env.py")
    cmd = [sys.executable, str(script), "--file", str(env_file), name, value]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        fail(f"upsert-lab-env.py failed for {name}: {proc.stderr.strip()[:160]}")
    print(proc.stdout.strip())


def do_repository(repo: str, env_file: Path) -> int:
    data = gh_api(f"repos/{repo}")
    owner_id = data.get("owner", {}).get("id")
    repo_id = data.get("id")
    if not isinstance(owner_id, int) or not isinstance(repo_id, int):
        fail("owner id or repository id is not an integer")
    upsert(env_file, "GITHUB_REPOSITORY_OWNER_ID", str(owner_id))
    upsert(env_file, "GITHUB_REPOSITORY_ID", str(repo_id))
    print(f"REPO_IDENTIFIERS=PASS owner_id={owner_id} repository_id={repo_id}")
    return 0


def do_main_sha(repo: str) -> int:
    sha = gh_api(f"repos/{repo}/commits/main").get("sha", "")
    if not SHA40.match(sha):
        fail(f"main sha is not 40-hex: {sha[:48]}")
    print(f"REPO_IDENTIFIERS=PASS main_sha={sha}")
    return 0


def do_checks_app(repo: str, sha: str) -> int:
    if not SHA40.match(sha):
        fail("--sha must be a 40-hex commit sha")
    runs = gh_api(f"repos/{repo}/commits/{sha}/check-runs?filter=latest").get("check_runs", [])
    picked = [
        r for r in runs
        if r.get("name") in REQUIRED_CHECKS and r.get("app", {}).get("slug") == "github-actions"
    ]
    names = sorted({r["name"] for r in picked})
    app_ids = {r["app"]["id"] for r in picked}
    if len(picked) != 2 or set(names) != REQUIRED_CHECKS or len(app_ids) != 1:
        fail("required GitHub Actions checks are missing, duplicated, or use different app ids")
    app_id = app_ids.pop()
    for r in picked:
        print(f"CHECK {r['name']:<18} {r.get('status'):<10} {r.get('conclusion')}")
    print(f"REPO_IDENTIFIERS=PASS checks_app_id={app_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what", required=True, choices=["repository", "main-sha", "checks-app"])
    parser.add_argument("--file", default="./lab.env", help="lab.env path (repository mode only)")
    parser.add_argument("--sha", default="", help="commit sha (checks-app mode only)")
    args = parser.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo or repo.count("/") != 1:
        fail("GITHUB_REPOSITORY is empty or malformed — source lab.env first")

    if args.what == "repository":
        return do_repository(repo, Path(args.file))
    if args.what == "main-sha":
        return do_main_sha(repo)
    return do_checks_app(repo, args.sha)


if __name__ == "__main__":
    raise SystemExit(main())
