#!/usr/bin/env python3
"""Release-toolchain and publisher-authenticity contracts."""

from pathlib import Path
import hashlib
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseSupplyChainContract(unittest.TestCase):
    def test_ansible_core_is_exactly_pinned_to_verified_supported_release(self) -> None:
        expected = "ansible-core==2.21.2"
        for relative in (
            "primary-aws/ansible/ci-requirements.txt",
            "standby-aws/ansible/ci-requirements.txt",
        ):
            lines = [
                line.strip()
                for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            self.assertEqual(lines, [expected], relative)

    def test_lighthouse_lock_uses_verified_v822_assets(self) -> None:
        expected = {
            "version": "8.2.2",
            "archive_url": "https://github.com/sigp/lighthouse/releases/download/v8.2.2/lighthouse-v8.2.2-x86_64-unknown-linux-gnu.tar.gz",
            "sha256": "334922e4b55075fbe86acaef3ce2a8e55699d2c647443e83cffed00f3babfaa8",
            "signature_url": "https://github.com/sigp/lighthouse/releases/download/v8.2.2/lighthouse-v8.2.2-x86_64-unknown-linux-gnu.tar.gz.asc",
            "signature_sha256": "7dade3cf0db3a0929532b9580d6e05f8caf15b90295286033aef4111c2c970a6",
            "signer_fingerprint": "15E66D941F697E28F49381F426416DC3F30674B0",
        }
        lock = json.loads(
            (ROOT / "shared/config/client-release-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(lock["lighthouse"], expected)

    def test_deposit_cli_lock_uses_attested_ethstaker_v130_asset(self) -> None:
        expected = {
            "schema_version": 1,
            "repository": "ethstaker/ethstaker-deposit-cli",
            "version": "1.3.0",
            "build_commit": "d8016bc",
            "platform": "linux-amd64",
            "archive_name": "ethstaker_deposit-cli-d8016bc-linux-amd64.tar.gz",
            "archive_url": "https://github.com/ethstaker/ethstaker-deposit-cli/releases/download/v1.3.0/ethstaker_deposit-cli-d8016bc-linux-amd64.tar.gz",
            "sha256": "89ecdfd5bb312c723b1feb7e09762be2510fd75df03d91876fad7f247b7238f2",
            "executable": "ethstaker_deposit-cli-d8016bc-linux-amd64/deposit",
        }
        lock = json.loads(
            (ROOT / "shared/config/deposit-cli-release-lock.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock, expected)

        reference = (ROOT / "shared/README.md").read_text(encoding="utf-8")
        self.assertIn("gh attestation verify", reference)
        self.assertIn("--repo ethstaker/ethstaker-deposit-cli", reference)
        self.assertIn(expected["sha256"], reference)
        self.assertNotIn("ethereum/staking-deposit-cli", reference)

    def test_release_keys_and_ansible_roles_enforce_openpgp_verification(self) -> None:
        expected_keys = {
            "lighthouse": "b62cceafadb1c6ffb324f9f18500aefa49a9a5757691bbbd9e85e9315dc88cd0",
            "nethermind": "326110e770cfdfe92d26471f754c21900d481612c5dd3f03eafdba3f73cf9e2c",
        }
        for client, expected_sha256 in expected_keys.items():
            key_path = ROOT / f"shared/keys/{client}-release-key.asc"
            self.assertTrue(key_path.is_file(), key_path)
            self.assertEqual(hashlib.sha256(key_path.read_bytes()).hexdigest(), expected_sha256)
            role = (
                ROOT
                / f"primary-aws/ansible/roles/{client if client == 'nethermind' else 'lighthouse_bn'}/tasks/main.yml"
            ).read_text(encoding="utf-8")
            for token in (
                "signature_url",
                "signature_sha256",
                "signer_fingerprint",
                "--show-keys",
                "gpgv",
            ):
                self.assertIn(token, role, f"{client}: {token}")
            self.assertNotIn("part-08", role)

    def test_site_playbooks_pass_signature_contract_to_both_roles(self) -> None:
        required = (
            "archive_url",
            "sha256",
            "signature_url",
            "signature_sha256",
            "signer_fingerprint",
        )
        for relative in (
            "primary-aws/ansible/playbooks/install-clients.yml",
            "standby-aws/ansible/playbooks/install-clients.yml",
        ):
            playbook = (ROOT / relative).read_text(encoding="utf-8")
            for client in ("nethermind", "lighthouse"):
                for field in required:
                    self.assertIn(
                        f"client_release_lock.{client}.{field}",
                        playbook,
                        f"{relative}: {client}.{field}",
                    )

    def test_github_rest_workflows_use_one_supported_api_version(self) -> None:
        workflows = ROOT / ".github/workflows"
        old_header = "X-GitHub-Api-Version: 2022-11-28"
        current_header = "X-GitHub-Api-Version: 2026-03-10"
        rest_call_files = []
        for path in workflows.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(old_header, text, path)
            if "GITHUB_API_URL" in text:
                rest_call_files.append(path)
                self.assertIn(current_header, text, path)
        self.assertTrue(rest_call_files, "GitHub REST workflow contract was not exercised")


if __name__ == "__main__":
    unittest.main()
