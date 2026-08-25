from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "shared/scripts/render-codeowners.py"
SOURCE = ROOT / ".github/CODEOWNERS"
TEMPLATE = ROOT / "shared/templates/CODEOWNERS"
OWNER = re.compile(
    r"^@(?P<org>[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38}))/(?P<team>platform-approvers|security-approvers)$"
)
EXPECTED = (
    ("/.github/workflows/", "platform-approvers"),
    ("/.github/CODEOWNERS", "security-approvers"),
    ("/primary-aws/terraform/", "platform-approvers"),
    ("/primary-aws/terraform/ci/runtime-inputs.json", "security-approvers"),
    ("/primary-aws/bootstrap/", "security-approvers"),
    ("/primary-aws/bootstrap/cicd/parameters.json", "security-approvers"),
    ("/primary-aws/ansible/", "platform-approvers"),
    ("/standby-aws/terraform/", "platform-approvers"),
    ("/standby-aws/terraform/ci/runtime-inputs.json", "security-approvers"),
    ("/standby-aws/ansible/", "platform-approvers"),
    ("/shared/", "security-approvers"),
    ("/drills/", "security-approvers"),
)


def placeholder_source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def parse_template_rules(body: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        tuple(line.split())
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


class CodeownersRenderTests(unittest.TestCase):
    def run_helper(self, file: Path, repository: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(HELPER),
                "--file",
                str(file),
                "--repository",
                repository,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_template_uses_only_the_expected_placeholder_rules(self) -> None:
        body = placeholder_source()
        self.assertEqual(parse_template_rules(body), tuple(
            (path, f"@YOUR_GITHUB_ORG/{team}") for path, team in EXPECTED
        ))
        self.assertEqual(body.count("@YOUR_GITHUB_ORG/platform-approvers"), 5)
        self.assertEqual(body.count("@YOUR_GITHUB_ORG/security-approvers"), 7)

    def test_source_uses_one_exact_org_owner_namespace(self) -> None:
        body = SOURCE.read_text(encoding="utf-8")
        rules = []
        organizations = set()
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            self.assertEqual(len(fields), 2)
            match = OWNER.fullmatch(fields[1])
            self.assertIsNotNone(match, fields[1])
            organizations.add(match.group("org"))
            rules.append((fields[0], match.group("team")))
        self.assertEqual(tuple(rules), EXPECTED)
        self.assertEqual(len(organizations), 1)
        organization = next(iter(organizations))
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if repository:
            match = re.fullmatch(
                r"([A-Za-z0-9][A-Za-z0-9_.-]{0,38})/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
                repository,
            )
            self.assertIsNotNone(match, repository)
            self.assertEqual(organizations, {match.group(1)})
            organization = match.group(1)
        self.assertIn(
            "/.github/CODEOWNERS", body, "CODEOWNERS must protect its own owner map"
        )
        self.assertIn("/shared/", body)
        self.assertIn("/drills/", body)
        self.assertIn(
            f"/primary-aws/terraform/ci/runtime-inputs.json @{organization}/security-approvers",
            body,
            "the authority-binding manifest must require an exact security CODEOWNER",
        )
        self.assertIn(
            f"/primary-aws/bootstrap/cicd/parameters.json @{organization}/security-approvers",
            body,
            "the actual bootstrap parameter evidence must require an exact security CODEOWNER",
        )
        self.assertIn(
            f"/standby-aws/terraform/ci/runtime-inputs.json @{organization}/security-approvers",
            body,
            "the Standby authority-binding manifest must require an exact security CODEOWNER",
        )
        self.assertNotIn("@platform-approvers", body)
        self.assertNotIn("@security-approvers", body)
        self.assertNotIn("@release-approvers", body)

    def test_renders_exact_organization_teams_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "CODEOWNERS"
            target.write_text(placeholder_source(), encoding="utf-8")

            first = self.run_helper(target, "play-builder/ethereum-validator-infra")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn(
                "CODEOWNERS_RENDER=OK org=play-builder changed=true", first.stdout
            )
            rendered = target.read_text(encoding="utf-8")
            self.assertEqual(rendered.count("@play-builder/platform-approvers"), 5)
            self.assertEqual(rendered.count("@play-builder/security-approvers"), 7)
            self.assertNotIn("YOUR_GITHUB_ORG", rendered)

            second = self.run_helper(target, "play-builder/ethereum-validator-infra")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn(
                "CODEOWNERS_RENDER=OK org=play-builder changed=false", second.stdout
            )
            self.assertEqual(target.read_text(encoding="utf-8"), rendered)

    def test_rejects_invalid_repository_foreign_owner_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_repo = root / "invalid-repo"
            invalid_repo.write_text(placeholder_source(), encoding="utf-8")
            result = self.run_helper(invalid_repo, "not-owner-repository")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reason=invalid_repository", result.stderr)

            foreign = root / "foreign"
            foreign.write_text(
                placeholder_source().replace(
                    "@YOUR_GITHUB_ORG/platform-approvers",
                    "@other-org/platform-approvers",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_helper(foreign, "play-builder/repository")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reason=unexpected_content", result.stderr)

            link = root / "link"
            link.symlink_to(invalid_repo)
            result = self.run_helper(link, "play-builder/repository")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reason=symlink_rejected", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
