#!/usr/bin/env python3
"""Contract tests for the root client release lock and site snapshots."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPOSITORY_ROOT / "shared/config/client-release-lock.json"
RENDERER_PATH = REPOSITORY_ROOT / "shared/scripts/render-client-release-snapshots.py"
VALIDATOR_PATH = REPOSITORY_ROOT / "shared/scripts/validate-client-release-lock.py"
PRIMARY_SNAPSHOT_PATH = (
    REPOSITORY_ROOT / "primary-aws/ansible/group_vars/client-release-lock.generated.json"
)
STANDBY_SNAPSHOT_PATH = (
    REPOSITORY_ROOT
    / "standby-aws/configs"
    / "client-release-lock.generated.json"
)

EXPECTED_LOCK = {
    "schema_version": 1,
    "network": "hoodi",
    "nethermind": {
        "version": "1.39.3",
        "build_commit": "28cbe2a0",
        "archive_url": "https://github.com/NethermindEth/nethermind/releases/download/1.39.3/nethermind-1.39.3-28cbe2a0-linux-x64.zip",
        "sha256": "8766fd72642b5b4238db48d34eb3f77aaf14c918dc88a1e7014252b0b829270c",
        "signature_url": "https://github.com/NethermindEth/nethermind/releases/download/1.39.3/nethermind-1.39.3-28cbe2a0-linux-x64.zip.asc",
        "signature_sha256": "1fcc8dcd5633ac08c91662b2599b703cc0b1d13081de224aa18e2089a4da8869",
        "signer_fingerprint": "AD1279765093C6759CD8A40024A774616F1E617E",
    },
    "lighthouse": {
        "version": "8.2.2",
        "archive_url": "https://github.com/sigp/lighthouse/releases/download/v8.2.2/lighthouse-v8.2.2-x86_64-unknown-linux-gnu.tar.gz",
        "sha256": "334922e4b55075fbe86acaef3ce2a8e55699d2c647443e83cffed00f3babfaa8",
        "signature_url": "https://github.com/sigp/lighthouse/releases/download/v8.2.2/lighthouse-v8.2.2-x86_64-unknown-linux-gnu.tar.gz.asc",
        "signature_sha256": "7dade3cf0db3a0929532b9580d6e05f8caf15b90295286033aef4111c2c970a6",
        "signer_fingerprint": "15E66D941F697E28F49381F426416DC3F30674B0",
    },
    "approved_skew": {
        "enabled": False,
        "site": None,
        "change_record": None,
        "expires_at": None,
    },
}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ClientReleaseLockTests(unittest.TestCase):
    maxDiff = None

    def run_helper(self, helper, *arguments):
        return subprocess.run(
            ["python3", str(helper), *map(str, arguments)],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def write_lock(self, path, value):
        path.write_text(canonical_json(value), encoding="utf-8")

    def render_into(self, directory, lock_path=LOCK_PATH):
        primary = directory / "primary.generated.json"
        standby = directory / "standby.generated.json"
        result = self.run_helper(
            RENDERER_PATH,
            "--lock",
            lock_path,
            "--primary-output",
            primary,
            "--standby-output",
            standby,
        )
        return result, primary, standby

    def load_renderer_module(self):
        specification = importlib.util.spec_from_file_location(
            "client_release_lock_renderer_test", RENDERER_PATH
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module

    def test_root_lock_has_exact_reviewed_pins(self):
        self.assertEqual(json.loads(LOCK_PATH.read_text(encoding="utf-8")), EXPECTED_LOCK)
        self.assertEqual(LOCK_PATH.read_text(encoding="utf-8"), canonical_json(EXPECTED_LOCK))
        self.assertEqual(stat.S_IMODE(LOCK_PATH.stat().st_mode), 0o644)

    def test_renderer_outputs_two_equal_derived_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result, primary, standby = self.render_into(Path(temporary_directory))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["lock_sha256"], sha256_file(LOCK_PATH))
            self.assertEqual(primary.read_bytes(), standby.read_bytes())
            self.assertEqual(stat.S_IMODE(primary.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(standby.stat().st_mode), 0o644)

            snapshot = json.loads(primary.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source_lock_sha256"], sha256_file(LOCK_PATH))
            self.assertEqual(snapshot["client_release_lock"], EXPECTED_LOCK)
            self.assertEqual(primary.read_text(encoding="utf-8"), canonical_json(snapshot))

            linked_output = Path(temporary_directory) / "linked-output.json"
            linked_output.symlink_to(primary)
            rejected = self.run_helper(
                RENDERER_PATH,
                "--lock",
                LOCK_PATH,
                "--primary-output",
                linked_output,
                "--standby-output",
                Path(temporary_directory) / "other.json",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("CLIENT_RELEASE_RENDER=FAIL reason=OUTPUT_NOT_REGULAR", rejected.stderr)

            preserved_primary = Path(temporary_directory) / "preserved-primary.json"
            preserved_primary.write_text('{"before":"render"}\n', encoding="utf-8")
            symlink_target = Path(temporary_directory) / "symlink-target.json"
            symlink_target.write_text("not a snapshot\n", encoding="utf-8")
            blocked_standby = Path(temporary_directory) / "blocked-standby.json"
            blocked_standby.symlink_to(symlink_target)
            transaction = self.run_helper(
                RENDERER_PATH,
                "--lock",
                LOCK_PATH,
                "--primary-output",
                preserved_primary,
                "--standby-output",
                blocked_standby,
            )
            self.assertNotEqual(transaction.returncode, 0)
            self.assertIn("CLIENT_RELEASE_RENDER=FAIL reason=OUTPUT_NOT_REGULAR", transaction.stderr)
            self.assertEqual(preserved_primary.read_text(encoding="utf-8"), '{"before":"render"}\n')

    def test_checked_in_snapshots_are_fresh(self):
        result = self.run_helper(
            VALIDATOR_PATH,
            "--lock",
            LOCK_PATH,
            "--primary-snapshot",
            PRIMARY_SNAPSHOT_PATH,
            "--standby-snapshot",
            STANDBY_SNAPSHOT_PATH,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "PASS", "lock_sha256": sha256_file(LOCK_PATH)},
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            primary = temporary_directory / "primary.json"
            standby = temporary_directory / "standby.json"
            shutil.copyfile(PRIMARY_SNAPSHOT_PATH, primary)
            shutil.copyfile(STANDBY_SNAPSHOT_PATH, standby)
            stale_primary = json.loads(primary.read_text(encoding="utf-8"))
            stale_primary["source_lock_sha256"] = "0" * 64
            primary.write_text(canonical_json(stale_primary), encoding="utf-8")

            stale = self.run_helper(
                VALIDATOR_PATH,
                "--lock",
                LOCK_PATH,
                "--primary-snapshot",
                primary,
                "--standby-snapshot",
                standby,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("CLIENT_RELEASE_LOCK=FAIL reason=SNAPSHOT_STALE", stale.stderr)

    def test_validator_rejects_latest_url_and_signature_drift(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            primary = temporary_directory / "primary.json"
            standby = temporary_directory / "standby.json"
            shutil.copyfile(PRIMARY_SNAPSHOT_PATH, primary)
            shutil.copyfile(STANDBY_SNAPSHOT_PATH, standby)

            latest_lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            latest_lock["nethermind"]["archive_url"] = (
                "https://github.com/NethermindEth/nethermind/releases/latest/download/nethermind-linux-x64.zip"
            )
            latest_path = temporary_directory / "latest.json"
            self.write_lock(latest_path, latest_lock)
            latest = self.run_helper(
                VALIDATOR_PATH,
                "--lock",
                latest_path,
                "--primary-snapshot",
                primary,
                "--standby-snapshot",
                standby,
            )
            self.assertNotEqual(latest.returncode, 0)
            self.assertIn("CLIENT_RELEASE_LOCK=FAIL reason=ARCHIVE_URL_NOT_PINNED", latest.stderr)

            signature_drift = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            signature_drift["lighthouse"]["signature_sha256"] = "0" * 64
            drift_path = temporary_directory / "signature-drift.json"
            self.write_lock(drift_path, signature_drift)
            drift = self.run_helper(
                VALIDATOR_PATH,
                "--lock",
                drift_path,
                "--primary-snapshot",
                primary,
                "--standby-snapshot",
                standby,
            )
            self.assertNotEqual(drift.returncode, 0)
            self.assertIn("CLIENT_RELEASE_LOCK=FAIL reason=SNAPSHOT_STALE", drift.stderr)

    def test_validator_rejects_unapproved_or_expired_skew(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            primary = temporary_directory / "primary.json"
            standby = temporary_directory / "standby.json"
            shutil.copyfile(PRIMARY_SNAPSHOT_PATH, primary)
            shutil.copyfile(STANDBY_SNAPSHOT_PATH, standby)

            for name, skew, reason in (
                (
                    "unapproved",
                    {
                        "enabled": True,
                        "site": None,
                        "change_record": None,
                        "expires_at": None,
                    },
                    "SKEW_NOT_APPROVED",
                ),
                (
                    "expired",
                    {
                        "enabled": True,
                        "site": "primary-aws",
                        "change_record": "CHG-123",
                        "expires_at": "2000-01-01T00:00:00Z",
                    },
                    "SKEW_EXPIRED",
                ),
            ):
                with self.subTest(name=name):
                    mutated = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
                    mutated["approved_skew"] = skew
                    lock_path = temporary_directory / f"{name}.json"
                    self.write_lock(lock_path, mutated)
                    result = self.run_helper(
                        VALIDATOR_PATH,
                        "--lock",
                        lock_path,
                        "--primary-snapshot",
                        primary,
                        "--standby-snapshot",
                        standby,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"CLIENT_RELEASE_LOCK=FAIL reason={reason}", result.stderr)

    def test_snapshots_expose_exact_nested_client_release_lock(self):
        lock_sha256 = sha256_file(LOCK_PATH)
        for snapshot_path in (PRIMARY_SNAPSHOT_PATH, STANDBY_SNAPSHOT_PATH):
            with self.subTest(snapshot=snapshot_path):
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                self.assertEqual(set(snapshot), {"client_release_lock", "source_lock_sha256"})
                self.assertEqual(snapshot["source_lock_sha256"], lock_sha256)
                self.assertEqual(snapshot["client_release_lock"], EXPECTED_LOCK)
                self.assertEqual(snapshot_path.read_text(encoding="utf-8"), canonical_json(snapshot))

    def test_site_playbooks_consume_only_the_generated_snapshots(self):
        standby_playbook = (
            REPOSITORY_ROOT
            / "standby-aws/ansible/playbooks"
            / "install-clients.yml"
        ).read_text(encoding="utf-8")
        primary_playbook = (
            REPOSITORY_ROOT / "primary-aws/ansible/playbooks/install-clients.yml"
        ).read_text(encoding="utf-8")
        manual_primary_vars = (
            REPOSITORY_ROOT / "primary-aws/ansible/group_vars/all.example.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("../../configs/client-release-lock.generated.json", standby_playbook)
        self.assertIn("../group_vars/client-release-lock.generated.json", primary_playbook)
        for alias, source in {
            "nethermind_version": "client_release_lock.nethermind.version",
            "nethermind_sha256": "client_release_lock.nethermind.sha256",
            "nethermind_archive_url": "client_release_lock.nethermind.archive_url",
            "lighthouse_version": "client_release_lock.lighthouse.version",
            "lighthouse_sha256": "client_release_lock.lighthouse.sha256",
        }.items():
            self.assertIn(f'{alias}: "{{{{ {source} }}}}"', primary_playbook)
            self.assertNotRegex(manual_primary_vars, rf"(?m)^\\s*{alias}:")

    def test_renderer_does_not_modify_site_playbooks_or_manual_vars(self):
        protected_paths = [
            REPOSITORY_ROOT / "primary-aws/ansible/playbooks/install-clients.yml",
            REPOSITORY_ROOT
            / "standby-aws/ansible/playbooks"
            / "install-clients.yml",
        ]
        protected_paths.extend(
            path
            for directory in (
                REPOSITORY_ROOT / "primary-aws/ansible/group_vars",
                REPOSITORY_ROOT
                / "standby-aws/configs",
            )
            if directory.exists()
            for path in directory.rglob("*")
            if path.is_file() and not path.name.endswith("client-release-lock.generated.json")
        )
        before = {path: sha256_file(path) for path in protected_paths}

        with tempfile.TemporaryDirectory() as temporary_directory:
            result, _, _ = self.render_into(Path(temporary_directory))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path: sha256_file(path) for path in protected_paths}, before)

    def test_validator_accepts_structurally_valid_future_pins_with_fresh_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            future_lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            future_lock["nethermind"] = {
                "version": "1.40.0",
                "build_commit": "1234abcd",
                "archive_url": "https://github.com/NethermindEth/nethermind/releases/download/1.40.0/nethermind-1.40.0-1234abcd-linux-x64.zip",
                "sha256": "a" * 64,
                "signature_url": "https://github.com/NethermindEth/nethermind/releases/download/1.40.0/nethermind-1.40.0-1234abcd-linux-x64.zip.asc",
                "signature_sha256": "b" * 64,
                "signer_fingerprint": "A" * 40,
            }
            future_lock["lighthouse"] = {
                "version": "8.3.0",
                "archive_url": "https://github.com/sigp/lighthouse/releases/download/v8.3.0/lighthouse-v8.3.0-x86_64-unknown-linux-gnu.tar.gz",
                "sha256": "c" * 64,
                "signature_url": "https://github.com/sigp/lighthouse/releases/download/v8.3.0/lighthouse-v8.3.0-x86_64-unknown-linux-gnu.tar.gz.asc",
                "signature_sha256": "d" * 64,
                "signer_fingerprint": "B" * 40,
            }
            future_lock_path = temporary_directory / "future-lock.json"
            self.write_lock(future_lock_path, future_lock)
            rendered, primary, standby = self.render_into(temporary_directory, future_lock_path)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

            validated = self.run_helper(
                VALIDATOR_PATH,
                "--lock",
                future_lock_path,
                "--primary-snapshot",
                primary,
                "--standby-snapshot",
                standby,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_renderer_rolls_back_primary_when_second_replace_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            primary = temporary_directory / "primary.json"
            standby = temporary_directory / "standby.json"
            primary.write_bytes(b"primary-before\n")
            standby.write_bytes(b"standby-before\n")
            os.chmod(primary, 0o600)
            os.chmod(standby, 0o640)
            expected_primary = (primary.read_bytes(), stat.S_IMODE(primary.stat().st_mode))
            expected_standby = (standby.read_bytes(), stat.S_IMODE(standby.stat().st_mode))
            renderer = self.load_renderer_module()
            original_replace = os.replace

            def fail_standby_replace(source, destination):
                if Path(destination) == standby:
                    raise PermissionError("forced second replace failure")
                return original_replace(source, destination)

            with mock.patch.object(
                renderer.os, "replace", side_effect=fail_standby_replace
            ), mock.patch.object(
                sys, "argv", [
                    str(RENDERER_PATH),
                    "--lock", str(LOCK_PATH),
                    "--primary-output", str(primary),
                    "--standby-output", str(standby),
                ]
            ):
                with self.assertRaises(renderer.ClientReleaseError) as raised:
                    renderer.main()

            self.assertEqual(raised.exception.reason, "OUTPUT_WRITE_FAILED")
            self.assertEqual((primary.read_bytes(), stat.S_IMODE(primary.stat().st_mode)), expected_primary)
            self.assertEqual((standby.read_bytes(), stat.S_IMODE(standby.stat().st_mode)), expected_standby)

    def test_helpers_map_io_errors_to_one_line_contracts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            unreadable_lock = temporary_directory / "unreadable-lock.json"
            shutil.copyfile(LOCK_PATH, unreadable_lock)
            os.chmod(unreadable_lock, 0o000)
            try:
                validator_result = self.run_helper(
                    VALIDATOR_PATH,
                    "--lock", unreadable_lock,
                    "--primary-snapshot", PRIMARY_SNAPSHOT_PATH,
                    "--standby-snapshot", STANDBY_SNAPSHOT_PATH,
                )
            finally:
                os.chmod(unreadable_lock, 0o600)
            self.assertNotEqual(validator_result.returncode, 0)
            self.assertEqual(
                validator_result.stderr,
                "CLIENT_RELEASE_LOCK=FAIL reason=LOCK_READ_FAILED\n",
            )
            self.assertNotIn("Traceback", validator_result.stderr)

            unwritable_parent = temporary_directory / "unwritable"
            unwritable_parent.mkdir()
            os.chmod(unwritable_parent, 0o500)
            try:
                renderer_result = self.run_helper(
                    RENDERER_PATH,
                    "--lock", LOCK_PATH,
                    "--primary-output", temporary_directory / "primary.json",
                    "--standby-output", unwritable_parent / "standby.json",
                )
            finally:
                os.chmod(unwritable_parent, 0o700)
            self.assertNotEqual(renderer_result.returncode, 0)
            self.assertEqual(
                renderer_result.stderr,
                "CLIENT_RELEASE_RENDER=FAIL reason=OUTPUT_WRITE_FAILED\n",
            )
            self.assertNotIn("Traceback", renderer_result.stderr)


UPGRADE_SCHEMA_PATH = REPOSITORY_ROOT / "shared/schemas/client-upgrade-change-record-v1.json"
UPGRADE_VALIDATOR_PATH = REPOSITORY_ROOT / "shared/scripts/validate-client-upgrade-window.py"
UPGRADE_RENDERER_PATH = REPOSITORY_ROOT / "shared/scripts/render-client-upgrade-package.py"
UPGRADE_SCHEMA_MIRROR = (
    REPOSITORY_ROOT
    / "standby-aws/configs"
    / "client-upgrade-change-record-v1.json"
)
UPGRADE_VALIDATOR_MIRROR = (
    REPOSITORY_ROOT
    / "standby-aws/scripts"
    / "validate-client-upgrade-window.py"
)


class ClientUpgradePackageRendererTests(unittest.TestCase):
    """The upgrade schema/validator mirrors are generated, never hand-edited."""

    @staticmethod
    def load_renderer():
        specification = importlib.util.spec_from_file_location(
            "client_upgrade_package_renderer", UPGRADE_RENDERER_PATH
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module

    def run_renderer(self, schema_output, validator_output):
        return subprocess.run(
            [
                sys.executable,
                str(UPGRADE_RENDERER_PATH),
                "--schema", str(UPGRADE_SCHEMA_PATH),
                "--validator", str(UPGRADE_VALIDATOR_PATH),
                "--schema-output", str(schema_output),
                "--validator-output", str(validator_output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_checked_in_upgrade_mirrors_match_root_authority(self):
        self.assertEqual(UPGRADE_SCHEMA_MIRROR.read_bytes(), UPGRADE_SCHEMA_PATH.read_bytes())
        self.assertEqual(UPGRADE_VALIDATOR_MIRROR.read_bytes(), UPGRADE_VALIDATOR_PATH.read_bytes())
        for mirror in (UPGRADE_SCHEMA_MIRROR, UPGRADE_VALIDATOR_MIRROR):
            self.assertFalse(mirror.is_symlink())
        self.assertEqual(stat.S_IMODE(UPGRADE_SCHEMA_MIRROR.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(UPGRADE_VALIDATOR_MIRROR.stat().st_mode), 0o755)

    def test_renderer_writes_byte_identical_outputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            schema_output = temporary_directory / "schema.json"
            validator_output = temporary_directory / "validator.py"
            result = self.run_renderer(schema_output, validator_output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(schema_output.read_bytes(), UPGRADE_SCHEMA_PATH.read_bytes())
            self.assertEqual(validator_output.read_bytes(), UPGRADE_VALIDATOR_PATH.read_bytes())
            self.assertEqual(stat.S_IMODE(schema_output.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(validator_output.stat().st_mode), 0o755)
            expected_schema_sha = hashlib.sha256(UPGRADE_SCHEMA_PATH.read_bytes()).hexdigest()
            self.assertIn(expected_schema_sha, result.stdout)

    def test_renderer_rejects_symlink_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            real_target = temporary_directory / "real-schema.json"
            real_target.write_text("{}", encoding="utf-8")
            schema_link = temporary_directory / "schema-link.json"
            schema_link.symlink_to(real_target)
            validator_output = temporary_directory / "validator.py"
            result = self.run_renderer(schema_link, validator_output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                result.stderr, "CLIENT_UPGRADE_RENDER=FAIL reason=OUTPUT_NOT_REGULAR\n"
            )
            self.assertEqual(real_target.read_text(encoding="utf-8"), "{}")
            self.assertFalse(validator_output.exists())

    def test_renderer_rolls_back_schema_when_validator_replace_fails(self):
        renderer = self.load_renderer()
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            schema_output = temporary_directory / "schema.json"
            validator_output = temporary_directory / "validator.py"
            schema_output.write_bytes(b"prior schema bytes\n")
            os.chmod(schema_output, 0o600)
            expected_prior = (b"prior schema bytes\n", 0o600)

            original_replace = renderer.os.replace

            def fail_validator_replace(source, destination):
                if Path(destination) == validator_output:
                    raise PermissionError("forced validator replace failure")
                return original_replace(source, destination)

            with mock.patch.object(
                renderer.os, "replace", side_effect=fail_validator_replace
            ), mock.patch.object(
                sys, "argv", [
                    str(UPGRADE_RENDERER_PATH),
                    "--schema", str(UPGRADE_SCHEMA_PATH),
                    "--validator", str(UPGRADE_VALIDATOR_PATH),
                    "--schema-output", str(schema_output),
                    "--validator-output", str(validator_output),
                ]
            ):
                with self.assertRaises(renderer.UpgradePackageError) as raised:
                    renderer.main()

            self.assertEqual(raised.exception.reason, "OUTPUT_WRITE_FAILED")
            self.assertEqual(
                (schema_output.read_bytes(), stat.S_IMODE(schema_output.stat().st_mode)),
                expected_prior,
            )
            self.assertFalse(validator_output.exists())
            self.assertEqual(list(temporary_directory.glob(".*")), [])


if __name__ == "__main__":
    unittest.main()
