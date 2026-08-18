from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PR_WORKFLOW = WORKFLOWS / "terraform-pr.yml"
DEPLOY_WORKFLOW = WORKFLOWS / "terraform-deploy.yml"
TEARDOWN_WORKFLOW = WORKFLOWS / "terraform-teardown.yml"
ANSIBLE_WORKFLOW = WORKFLOWS / "ansible-pr.yml"
PLAN_POLICY = ROOT / "primary-aws" / "terraform" / "ci" / "plan-policy.jq"
PLAN_SUMMARY = ROOT / "primary-aws" / "terraform" / "ci" / "plan-summary.jq"
TEARDOWN_PREP_POLICY = ROOT / "primary-aws" / "terraform" / "ci" / "teardown-prep-policy.jq"
TEARDOWN_POLICY = ROOT / "primary-aws" / "terraform" / "ci" / "teardown-policy.jq"
SECURITY_GROUPS = ROOT / "primary-aws" / "terraform" / "sg.tf"
RUNTIME_MANIFEST = ROOT / "primary-aws" / "terraform" / "ci" / "runtime-inputs.json"
RUNTIME_RENDERER = ROOT / "shared" / "scripts" / "render-terraform-ci-runtime.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def pipeline_workflow_paths() -> list[Path]:
    """배포 파이프라인 workflow 목록 — teardown은 X1 슬라이스 도착 후에만 검사한다."""
    paths = [DEPLOY_WORKFLOW]
    if TEARDOWN_WORKFLOW.is_file():
        paths.append(TEARDOWN_WORKFLOW)
    return paths


def protected_apply_cases() -> list[tuple[Path, str]]:
    cases = [(DEPLOY_WORKFLOW, "\n  apply:\n")]
    if TEARDOWN_WORKFLOW.is_file():
        cases.append((TEARDOWN_WORKFLOW, "\n  teardown-apply:\n"))
    return cases


def run_jq_filter(
    path: Path,
    payload: dict[str, object],
    *,
    admin_cidrs: list[str] | None = None,
    backup_peer: str | None = "198.51.100.20",
) -> subprocess.CompletedProcess[str]:
    if admin_cidrs is None:
        admin_cidrs = ["203.0.113.10/32"]
    return subprocess.run(
        [
            "jq", "-c", "-e",
            "--argjson", "admin_cidrs", json.dumps(admin_cidrs),
            "--argjson", "backup_peer", json.dumps(backup_peer),
            "-f", str(path),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def run_jq_filter_with_staging(
    path: Path, payload: dict[str, object], enabled: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", "-c", "-e", "--argjson", "staging_enabled", str(enabled).lower(), "-f", str(path)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
