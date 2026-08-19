#!/usr/bin/env python3
"""Behavior tests for the durable, dirfd-relative absence evidence store."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import pwd
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
HELPER = HERE.parent / "scripts" / "vc-evidence-store.py"
PAYLOAD = b'{"schema":"absence-evidence/v1","result":"ABSENCE_OBSERVED"}\n'


def load_helper():
    if not HELPER.is_file():
        raise AssertionError(f"missing evidence-store helper: {HELPER}")
    spec = importlib.util.spec_from_file_location("vc_evidence_store", HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load evidence-store helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceStoreDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name)) / "evidence"
        self.root.mkdir(mode=0o700)
        self.out = self.root / "current-absence.json"
        self.owner = pwd.getpwuid(os.geteuid()).pw_name
        self.module = load_helper()
        self.real_fsync = os.fsync
        self.events: list[tuple[str, bool, bool, bool, int]] = []
        self.fsync_labels: list[str] = []
        self.owner_present_at_sync: list[bool] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def traced_fsync(self, fd: int) -> None:
        descriptor = os.fstat(fd)
        root_stat = self.root.stat()
        descriptor_identity = (descriptor.st_dev, descriptor.st_ino)
        owner_path = Path(str(self.out) + ".observe.lock") / "owner"
        if stat.S_ISDIR(descriptor.st_mode):
            if descriptor_identity == (root_stat.st_dev, root_stat.st_ino):
                kind = "root"
                label = "root"
            else:
                kind = "dir"
                label = "lock-dir"
        else:
            kind = "file"
            label = "unknown-file"
            candidates = [owner_path, *self.root.glob(".*.tmp")]
            for candidate in candidates:
                try:
                    item = candidate.lstat()
                except FileNotFoundError:
                    continue
                if descriptor_identity != (item.st_dev, item.st_ino):
                    continue
                if candidate == owner_path:
                    label = "lock-owner"
                elif candidate.name.endswith(".json.tmp"):
                    label = "json-temp"
                elif candidate.name.endswith(".sha256.tmp"):
                    label = "checksum-temp"
                break
        self.fsync_labels.append(label)
        self.owner_present_at_sync.append(owner_path.exists())
        self.events.append(
            (
                kind,
                self.out.exists(),
                Path(str(self.out) + ".sha256").exists(),
                Path(str(self.out) + ".observe.lock").exists(),
                len(tuple(self.root.glob(".*.tmp"))),
            )
        )
        self.real_fsync(fd)

    def reset_trace(self) -> None:
        self.events.clear()
        self.fsync_labels.clear()
        self.owner_present_at_sync.clear()

    def store(self):
        return self.module.EvidenceStore(
            str(self.root), self.owner, str(self.out)
        )

    def test_every_crash_transition_is_durable_and_fail_closed(self) -> None:
        self.out.write_bytes(b"old evidence\n")
        self.out.with_name(self.out.name + ".sha256").write_text(
            hashlib.sha256(self.out.read_bytes()).hexdigest() + "\n",
            encoding="ascii",
        )
        with mock.patch.object(self.module.os, "fsync", self.traced_fsync):
            with self.store() as store:
                self.reset_trace()
                store.begin(invalidate=True)
                # lock creation is durable while the old pair still exists;
                # invalidation has its own later root fsync with lock retained.
                self.assertIn(("root", True, True, True, 0), self.events)
                self.assertIn("file", [event[0] for event in self.events])
                self.assertEqual(self.events[-1], ("root", False, False, True, 0))
                self.assertEqual(
                    self.fsync_labels,
                    ["root", "lock-owner", "lock-dir", "root", "root"],
                )
                self.assertFalse(self.out.exists())
                self.assertFalse(Path(str(self.out) + ".sha256").exists())
                self.assertTrue(Path(str(self.out) + ".observe.lock").is_dir())

                self.reset_trace()
                transaction = store.prepare(PAYLOAD)
                self.assertEqual([event[0] for event in self.events], ["file", "file", "root"])
                self.assertEqual(self.events[-1], ("root", False, False, True, 2))
                self.assertEqual(
                    self.fsync_labels, ["json-temp", "checksum-temp", "root"]
                )

                self.reset_trace()
                store.commit_checksum(transaction)
                self.assertEqual(self.events, [("root", False, True, True, 1)])
                self.assertFalse(self.out.exists())
                self.assertTrue(Path(str(self.out) + ".sha256").is_file())

                self.reset_trace()
                store.commit_json(transaction)
                self.assertEqual(self.events, [("root", True, True, True, 0)])
                self.assertEqual(self.out.read_bytes(), PAYLOAD)
                expected = hashlib.sha256(PAYLOAD).hexdigest() + "\n"
                self.assertEqual(
                    Path(str(self.out) + ".sha256").read_text(encoding="ascii"),
                    expected,
                )
                self.assertTrue(Path(str(self.out) + ".observe.lock").is_dir())

                self.reset_trace()
                store.release()
                self.assertEqual([event[0] for event in self.events], ["dir", "root"])
                self.assertEqual(self.events[-1], ("root", True, True, False, 0))
                self.assertEqual(self.fsync_labels, ["lock-dir", "root"])
                self.assertFalse(self.owner_present_at_sync[0])
                self.assertFalse(Path(str(self.out) + ".observe.lock").exists())

    def test_abort_durably_invalidates_pair_before_removing_lock(self) -> None:
        with mock.patch.object(self.module.os, "fsync", self.traced_fsync):
            with self.store() as store:
                store.begin(invalidate=True)
                transaction = store.prepare(PAYLOAD)
                store.commit_checksum(transaction)
                store.commit_json(transaction)
                self.reset_trace()
                store.abort(transaction)
                self.assertIn(("root", False, False, True, 0), self.events)
                self.assertEqual(self.events[-1], ("root", False, False, False, 0))
                self.assertEqual(
                    self.fsync_labels, ["root", "root", "lock-dir", "root"]
                )
                self.assertFalse(self.owner_present_at_sync[-2])
        self.assertFalse(self.out.exists())
        self.assertFalse(Path(str(self.out) + ".sha256").exists())
        self.assertFalse(Path(str(self.out) + ".observe.lock").exists())

    def test_both_roles_deploy_and_configure_the_store_helper(self) -> None:
        repo = HERE.parent.parent
        primary_tasks = repo / "primary-aws/ansible/roles/lighthouse_vc_gated/tasks/main.yml"
        primary_env = repo / "primary-aws/ansible/roles/lighthouse_vc_gated/templates/gate.env.j2"
        standby_tasks = repo / "standby-aws/ansible/roles/vc_sealed/tasks/main.yml"
        standby_config = repo / "standby-aws/ansible/ansible.cfg"

        body = primary_tasks.read_text(encoding="utf-8")
        self.assertIn("vc-evidence-store.py", body, primary_tasks)
        self.assertIn("/var/lib/ethereum-maintenance/evidence", body, primary_tasks)
        self.assertIn('mode: "0700"', body, primary_tasks)

        body = primary_env.read_text(encoding="utf-8")
        self.assertIn("EVIDENCE_ROOT=/var/lib/ethereum-maintenance/evidence", body)
        self.assertIn("EVIDENCE_STORE_HELPER=/usr/local/sbin/vc-evidence-store", body)
        self.assertIn("EVIDENCE_OWNER_EXPECTED=root", body)

        body = standby_tasks.read_text(encoding="utf-8")
        self.assertIn("ansible.builtin.include_role", body, standby_tasks)
        self.assertIn("name: lighthouse_vc_gated", body, standby_tasks)
        self.assertIn("roles_path = roles:../../primary-aws/ansible/roles", standby_config.read_text(encoding="utf-8"))

    def test_primary_base_role_preserves_private_evidence_root_mode(self) -> None:
        repo = HERE.parent.parent
        base_tasks = repo / "primary-aws/ansible/roles/base_os/tasks/main.yml"
        body = base_tasks.read_text(encoding="utf-8")
        private_task = body.split(
            "- name: Gate evidence root — dirfd store 전용 private boundary\n", 1
        )[1].split("\n- name:", 1)[0]
        self.assertIn("path: /var/lib/ethereum-maintenance/evidence", private_task)
        self.assertIn("owner: root", private_task)
        self.assertIn("group: root", private_task)
        self.assertIn('mode: "0700"', private_task)
        broad_0750_entry = (
            '- { path: "/var/lib/ethereum-maintenance/evidence", '
            "         owner: root }"
        )
        self.assertNotIn(broad_0750_entry, body)

    def test_o_nofollow_is_mandatory(self) -> None:
        with mock.patch.dict(
            os.environ, {"VC_EVIDENCE_STORE_TEST_NO_NOFOLLOW": "1"}
        ):
            with self.assertRaises(self.module.EvidenceStoreError) as caught:
                self.store()
        self.assertEqual(caught.exception.reason, "o_nofollow_unavailable")

    def test_checksum_suffix_cannot_alias_another_evidence_pair(self) -> None:
        self.out.write_bytes(PAYLOAD)
        checksum = self.out.with_name(self.out.name + ".sha256")
        checksum.write_text(
            hashlib.sha256(PAYLOAD).hexdigest() + "\n", encoding="ascii"
        )
        before_json = self.out.read_bytes()
        before_checksum = checksum.read_bytes()

        with self.assertRaises(self.module.EvidenceStoreError):
            with self.module.EvidenceStore(
                str(self.root), self.owner, str(checksum)
            ) as store:
                store.begin(invalidate=True)

        self.assertEqual(self.out.read_bytes(), before_json)
        self.assertEqual(checksum.read_bytes(), before_checksum)
        self.assertFalse(Path(str(checksum) + ".observe.lock").exists())

    def test_lock_suffix_is_reserved_from_evidence_basenames(self) -> None:
        colliding_out = Path(str(self.out) + ".observe.lock")
        with self.assertRaises(self.module.EvidenceStoreError):
            self.module.EvidenceStore(
                str(self.root), self.owner, str(colliding_out)
            )

        self.assertFalse(colliding_out.exists())
        self.assertEqual(tuple(self.root.iterdir()), ())

    def test_audit_log_namespace_cannot_be_invalidated_as_observer_out(self) -> None:
        audit_log = self.root / "vc-gate.log"
        audit_log.write_bytes(b'{"verdict":"GATE=PASS"}\n')
        before = audit_log.read_bytes()

        with self.assertRaises(self.module.EvidenceStoreError):
            with self.module.EvidenceStore(
                str(self.root), self.owner, str(audit_log)
            ) as store:
                store.begin(invalidate=True)

        self.assertEqual(audit_log.read_bytes(), before)
        self.assertFalse(Path(str(audit_log) + ".observe.lock").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
