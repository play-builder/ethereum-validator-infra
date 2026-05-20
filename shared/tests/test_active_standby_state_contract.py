from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "shared/config/active-standby-state-machine.json"
SCHEMA = ROOT / "shared/schemas/active-standby-state-v1.json"
HELPER = ROOT / "shared/scripts/transition-active-standby-state.py"
PUBKEY = "0x" + "11" * 48
CONTAINMENT_ALLOWED = [
    "STOP",
    "MASK",
    "FRESH_DESCENDANT_SP_EXPORT",
    "FRESH_DESCENDANT_SP_PRESERVE",
    "RESEAL",
    "HARD_FENCE",
    "EVIDENCE_WRITE",
]


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


class ActiveStandbyStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in (MACHINE, SCHEMA, HELPER):
            self.assertTrue(path.is_file(), f"required Task 2 path missing: {path}")
        self.machine = json.loads(MACHINE.read_text(encoding="utf-8"))
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory(prefix="active-standby-state-")
        self.work = Path(self.temp.name)
        self.run_index = 0
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.last_before: bytes | None = None
        self.last_mode: int | None = None
        self.last_canonical: Path | None = None

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ts(self, seconds: int) -> str:
        return (self.now + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def state(self, *, operational_state: str = "S0", version: int = 0) -> dict:
        return {
            "schema_version": "active-standby-state-v1",
            "from_state": operational_state,
            "to_state": operational_state,
            "transition": "INITIALIZED",
            "mode": "NORMAL",
            "incident_id": "INC-2026-0001",
            "pubkey": PUBKEY,
            "lease_owner": "operator-a",
            "lease_purpose": "OPERATIONAL_TRANSITION",
            "issued_at": self.ts(-10),
            "lease_expires_at": self.ts(1800),
            "retry_deadline": None,
            "allowed_mutations": [],
            "last_completed_gate": None,
            "evidence_hashes": {},
            "state_version": version,
            "no_go_reason": None,
            "consumed_emergency_idempotency_keys": [],
        }

    def request(self, event: str, version: int, **overrides: object) -> dict:
        value = {
            "event": event,
            "expected_state_version": version,
            "incident_id": "INC-2026-0001",
            "pubkey": PUBKEY,
            "lease_owner": "operator-a",
            "lease_purpose": "OPERATIONAL_TRANSITION",
            "issued_at": self.ts(-10),
            "lease_expires_at": self.ts(1800),
            "retry_deadline": None,
            "idempotency_key": None,
            "loss_reason": None,
            "requested_mutations": [],
            "evidence_hashes": {event: digest(event)},
            "soak_samples": [],
            "approvers": [],
        }
        value.update(overrides)
        return value

    def run_engine(
        self,
        current: dict | str,
        request: dict | str,
        *,
        output: Path | None = None,
        machine: Path = MACHINE,
        injected_directory_fsync_failure: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        self.run_index += 1
        output_path = output or self.work / f"canonical-state-{self.run_index}.json"
        current_path = output_path
        unsafe_output = output_path.is_symlink() or (output_path.exists() and not output_path.is_file())
        if unsafe_output:
            current_path = self.work / f"unsafe-current-{self.run_index}.json"
        current_path.write_text(
            current if isinstance(current, str) else json.dumps(current), encoding="utf-8"
        )
        request_path = self.work / f"request-{self.run_index}.json"
        request_path.write_text(
            request if isinstance(request, str) else json.dumps(request), encoding="utf-8"
        )
        self.last_canonical = current_path if current_path == output_path else None
        self.last_before = current_path.read_bytes() if self.last_canonical else None
        self.last_mode = stat.S_IMODE(current_path.stat().st_mode) if self.last_canonical else None
        argv = [
            "--machine",
            str(machine),
            "--current",
            str(current_path),
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ]
        command = ["python3", str(HELPER), *argv]
        if injected_directory_fsync_failure:
            wrapper = self.work / f"fsync-wrapper-{self.run_index}.py"
            wrapper.write_text(
                textwrap.dedent(
                    f"""
                    import importlib.util
                    import os
                    import sys

                    spec = importlib.util.spec_from_file_location("state_engine", {str(HELPER)!r})
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    real_fsync = module.os.fsync
                    calls = 0
                    def fail_second_fsync(fd):
                        global calls
                        calls += 1
                        if calls == 2:
                            raise OSError("injected directory fsync failure")
                        return real_fsync(fd)
                    module.os.fsync = fail_second_fsync
                    raise SystemExit(module.main(sys.argv[1:]))
                    """
                ),
                encoding="utf-8",
            )
            command = ["python3", str(wrapper), *argv]
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return result, output_path

    def assert_pass(self, result: subprocess.CompletedProcess[str], line: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, line + "\n")
        self.assertEqual(result.stderr, "")

    def assert_fail(
        self,
        result: subprocess.CompletedProcess[str],
        reason: str,
    ) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr, f"ACTIVE_STANDBY_TRANSITION=FAIL reason={reason}\n"
        )
        self.assertNotIn("Traceback", result.stderr)
        if self.last_canonical is not None and self.last_before is not None:
            self.assertEqual(self.last_canonical.read_bytes(), self.last_before)
            self.assertEqual(stat.S_IMODE(self.last_canonical.stat().st_mode), self.last_mode)

    def advance(self, current: dict, event: str, **overrides: object) -> dict:
        request = self.request(event, current["state_version"], **overrides)
        result, output = self.run_engine(current, request)
        self.assert_pass(
            result,
            "ACTIVE_STANDBY_TRANSITION=PASS "
            f"from={current['to_state']} to={json.loads(output.read_text())['to_state']} "
            f"transition={event}",
        )
        return json.loads(output.read_text(encoding="utf-8"))

    def reach_f7(self, *, prime: bool = False) -> dict:
        current = self.state()
        current = self.advance(current, "INCIDENT_SIGNAL")
        current = self.advance(current, "F0_APPROVED")
        for number in range(1, 8):
            current = self.advance(current, f"F{number}_PASS")
        if not prime:
            return current
        current = self.advance(current, "F8_COMPLETE")
        current = self.advance(
            current,
            "FAILBACK_APPROVED",
            evidence_hashes={"STANDBY_SIGNING_SOAK_60M": digest("soak")},
            soak_samples=self.soak_samples(),
            approvers=["operator-a", "operator-b"],
        )
        for event in (
            "F3_PRIME_PASS",
            "F1_PRIME_PASS",
            "F2_PRIME_PASS",
            "F5_PRIME_PASS",
            "F6_PRIME_PASS",
            "F7_PRIME_PASS",
        ):
            current = self.advance(current, event)
        return current

    def enter_retry(self, *, prime: bool = False) -> dict:
        current = self.reach_f7(prime=prime)
        event = "F8_PRIME_RETRY_ENTER" if prime else "F8_RETRY_ENTER"
        fence = "STANDBY_HARD_FENCE_FRESH" if prime else "AWS_FENCE_FRESH"
        signer = "AWS_SOLE_SIGNER" if prime else "STANDBY_SOLE_SIGNER"
        return self.advance(
            current,
            event,
            lease_expires_at=self.ts(300),
            retry_deadline=self.ts(600),
            evidence_hashes={fence: digest(fence), signer: digest(signer)},
        )

    def enter_containment(self, *, prime: bool = False) -> dict:
        current = self.enter_retry(prime=prime)
        current["retry_deadline"] = self.ts(-60)
        event = "F8_PRIME_RETRY_CONTAIN_ENTER" if prime else "F8_RETRY_CONTAIN_ENTER"
        return self.advance(
            current,
            event,
            issued_at=self.ts(-10),
            lease_expires_at=self.ts(600),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-0001",
            loss_reason="RETRY_DEADLINE_EXPIRED",
            evidence_hashes={"RETRY_DEADLINE_EXPIRED": digest("expired")},
        )

    def soak_samples(self) -> list[dict]:
        start = self.now - timedelta(hours=1)
        timestamps = [
            (start + timedelta(minutes=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for offset in range(0, 61, 5)
        ]
        return [
            {
                "observed_at": observed_at,
                "aws_fence_fresh": True,
                "conflict_indicators": 0,
                "slashing_indicators": 0,
            }
            for observed_at in timestamps
        ]

    def test_only_s0_through_s4_are_operational_states(self):
        self.assertEqual(self.machine["operational_states"], ["S0", "S1", "S2", "S3", "S4"])
        self.assertEqual(
            self.schema["properties"]["to_state"]["enum"],
            ["S0", "S1", "S2", "S3", "S4"],
        )
        invalid = self.state(operational_state="S5")
        result, output = self.run_engine(invalid, self.request("INCIDENT_SIGNAL", 0))
        self.assert_fail(result, "invalid_current_state")

    def test_only_canonical_f_and_f_prime_transitions_exist(self):
        expected = [
            ("INCIDENT_SIGNAL", "S0", "S1", "NORMAL"),
            ("SOURCE_RETAINED", "S1", "S0", "NORMAL"),
            ("F0_APPROVED", "S1", "S2", "NORMAL"),
            *[(f"F{i}_PASS", "S2", "S2", "NORMAL") for i in range(1, 8)],
            ("F8_RETRY_ENTER", "S2", "S2", "NORMAL"),
            ("F8_COMPLETE", "S2", "S3", "NORMAL|F8_RETRY_ONLY"),
            ("F8_RETRY_CONTAIN_ENTER", "S2", "S2", "F8_RETRY_ONLY"),
            ("F8_RETRY_ABORT_CONTAINED", "S2", "S2", "F8_CONTAINMENT_ONLY"),
            ("FAILBACK_APPROVED", "S3", "S4", "NORMAL"),
            *[
                (event, "S4", "S4", "NORMAL")
                for event in (
                    "F3_PRIME_PASS",
                    "F1_PRIME_PASS",
                    "F2_PRIME_PASS",
                    "F5_PRIME_PASS",
                    "F6_PRIME_PASS",
                    "F7_PRIME_PASS",
                )
            ],
            ("F8_PRIME_RETRY_ENTER", "S4", "S4", "NORMAL"),
            ("F8_PRIME_COMPLETE", "S4", "S0", "NORMAL|F8_PRIME_RETRY_ONLY"),
            ("F8_PRIME_RETRY_CONTAIN_ENTER", "S4", "S4", "F8_PRIME_RETRY_ONLY"),
            ("F8_PRIME_RETRY_ABORT_CONTAINED", "S4", "S4", "F8_PRIME_CONTAINMENT_ONLY"),
        ]
        actual = [
            (row["event"], row["from"], row["to"], row["required_mode"])
            for row in self.machine["transitions"]
        ]
        self.assertEqual(actual, expected)
        result, output = self.run_engine(self.state(), self.request("F0_APPROVED", 0))
        self.assert_fail(result, "wrong_edge")
        signaled = self.advance(self.state(), "INCIDENT_SIGNAL")
        retained = self.advance(signaled, "SOURCE_RETAINED")
        self.assertEqual(retained["to_state"], "S0")

    def test_auto_failover_is_absent(self):
        result, output = self.run_engine(self.state(), self.request("AUTO_FAILOVER", 0))
        self.assert_fail(result, "wrong_edge")
        self.assertEqual(self.machine["authority"], "HUMAN_APPROVED_LOCAL_ONLY")
        for mutation in ("START", "UNMASK", "KEY_IMPORT", "SLASHING_PROTECTION_IMPORT"):
            with self.subTest(normal_gate_mutation=mutation):
                result, _ = self.run_engine(
                    self.state(),
                    self.request("INCIDENT_SIGNAL", 0, requested_mutations=[mutation]),
                )
                self.assert_fail(result, "wrong_edge")
        variants = []
        extra_edge = json.loads(json.dumps(self.machine))
        extra_edge["transitions"].append(
            {"event": "AUTO_FAILOVER", "from": "S0", "to": "S3", "required_mode": "NORMAL"}
        )
        variants.append(extra_edge)
        relaxed_mutations = json.loads(json.dumps(self.machine))
        relaxed_mutations["retry_allowed_mutations"].extend(["START", "KEY_IMPORT"])
        variants.append(relaxed_mutations)
        relaxed_soak = json.loads(json.dumps(self.machine))
        relaxed_soak["failback"]["duration_seconds"] = 0
        relaxed_soak["failback"]["sample_count"] = 1
        variants.append(relaxed_soak)
        relaxed_modes = json.loads(json.dumps(self.machine))
        relaxed_modes["modes"].append("AUTO_START")
        variants.append(relaxed_modes)
        for index, variant in enumerate(variants):
            with self.subTest(tampered_machine=index):
                path = self.work / f"tampered-machine-{index}.json"
                path.write_text(json.dumps(variant), encoding="utf-8")
                result, _ = self.run_engine(
                    self.state(), self.request("INCIDENT_SIGNAL", 0), machine=path
                )
                self.assert_fail(result, "invalid_machine")

    def test_f8_retry_only_forbids_key_sp_and_signer_mutation(self):
        current = self.enter_retry()
        for mutation in ("KEY_MUTATION", "SLASHING_PROTECTION_MUTATION", "SIGNER_MUTATION"):
            with self.subTest(mutation=mutation):
                request = self.request(
                    "F8_COMPLETE",
                    current["state_version"],
                    requested_mutations=[mutation],
                )
                result, output = self.run_engine(current, request)
                self.assert_fail(result, "retry_mutation_forbidden")
        completed = self.advance(
            current, "F8_COMPLETE", requested_mutations=["FINAL_EVIDENCE_WRITE"]
        )
        self.assertEqual(completed["to_state"], "S3")

    def test_f8_prime_retry_only_forbids_key_sp_and_signer_mutation(self):
        current = self.enter_retry(prime=True)
        for mutation in ("KEY_MUTATION", "SLASHING_PROTECTION_MUTATION", "SIGNER_MUTATION"):
            with self.subTest(mutation=mutation):
                request = self.request(
                    "F8_PRIME_COMPLETE",
                    current["state_version"],
                    requested_mutations=[mutation],
                )
                result, output = self.run_engine(current, request)
                self.assert_fail(result, "retry_mutation_forbidden")
        completed = self.advance(
            current, "F8_PRIME_COMPLETE", requested_mutations=["FINAL_EVIDENCE_WRITE"]
        )
        self.assertEqual(completed["to_state"], "S0")

    def test_retry_entry_requires_prior_gate_fresh_fence_and_bounded_expiry(self):
        for prime in (False, True):
            current = self.reach_f7(prime=prime)
            event = "F8_PRIME_RETRY_ENTER" if prime else "F8_RETRY_ENTER"
            fence = "STANDBY_HARD_FENCE_FRESH" if prime else "AWS_FENCE_FRESH"
            signer = "AWS_SOLE_SIGNER" if prime else "STANDBY_SOLE_SIGNER"
            valid = self.request(
                event,
                current["state_version"],
                lease_expires_at=self.ts(300),
                retry_deadline=self.ts(600),
                evidence_hashes={fence: digest(fence), signer: digest(signer)},
            )
            cases = {
                "missing_fence": {**valid, "evidence_hashes": {signer: digest(signer)}},
                "missing_signer": {**valid, "evidence_hashes": {fence: digest(fence)}},
                "unbounded": {**valid, "retry_deadline": self.ts(3700)},
                "deadline_not_future": {**valid, "retry_deadline": self.ts(-1)},
            }
            for label, request in cases.items():
                with self.subTest(prime=prime, case=label):
                    result, output = self.run_engine(current, request)
                    self.assert_fail(result, "evidence_missing" if "missing" in label else "wrong_edge")
            prior = dict(current)
            prior["last_completed_gate"] = "F6_PRIME_PASS" if prime else "F6_PASS"
            result, output = self.run_engine(prior, valid)
            self.assert_fail(result, "invalid_current_state")
            for mutation in ("START", "UNMASK", "KEY_IMPORT", "SLASHING_PROTECTION_IMPORT"):
                with self.subTest(prime=prime, retry_entry_mutation=mutation):
                    mutated = {**valid, "requested_mutations": [mutation]}
                    result, _ = self.run_engine(current, mutated)
                    self.assert_fail(result, "retry_mutation_forbidden")
        forged = self.state(operational_state="S2", version=9)
        forged.update(
            transition="F7_PASS",
            last_completed_gate="F7_PASS",
        )
        request = self.request(
            "F8_RETRY_ENTER",
            9,
            lease_expires_at=self.ts(300),
            retry_deadline=self.ts(600),
            evidence_hashes={
                "AWS_FENCE_FRESH": digest("AWS_FENCE_FRESH"),
                "STANDBY_SOLE_SIGNER": digest("STANDBY_SOLE_SIGNER"),
            },
        )
        result, _ = self.run_engine(forged, request)
        self.assert_fail(result, "invalid_current_state")
        early = self.advance(self.state(), "INCIDENT_SIGNAL")
        early = self.advance(early, "F0_APPROVED")
        result, _ = self.run_engine(
            early,
            self.request("F2_PASS", early["state_version"]),
        )
        self.assert_fail(result, "gate_out_of_order")

    def test_retry_completion_or_expiry_containment_is_the_only_exit(self):
        for prime in (False, True):
            current = self.enter_retry(prime=prime)
            result, output = self.run_engine(
                current, self.request("SOURCE_RETAINED", current["state_version"])
            )
            self.assert_fail(result, "wrong_edge")
            complete = "F8_PRIME_COMPLETE" if prime else "F8_COMPLETE"
            expected_state = "S0" if prime else "S3"
            self.assertEqual(
                self.advance(
                    current,
                    complete,
                    requested_mutations=["FINAL_EVIDENCE_WRITE"],
                )["to_state"],
                expected_state,
            )
            expired = dict(current)
            expired["retry_deadline"] = self.ts(-60)
            result, _ = self.run_engine(
                expired,
                self.request(
                    complete,
                    expired["state_version"],
                    requested_mutations=["FINAL_EVIDENCE_WRITE"],
                ),
            )
            self.assert_fail(result, "wrong_edge")
            expected_mode = "F8_PRIME_CONTAINMENT_ONLY" if prime else "F8_CONTAINMENT_ONLY"
            self.assertEqual(self.enter_containment(prime=prime)["mode"], expected_mode)

    def test_containment_mode_is_forbidden_before_expiry_or_freshness_loss(self):
        current = self.enter_retry()
        request = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            current["state_version"],
            issued_at=self.ts(-300),
            lease_expires_at=self.ts(1200),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-early",
            loss_reason="RETRY_DEADLINE_EXPIRED",
            evidence_hashes={"RETRY_DEADLINE_EXPIRED": digest("early")},
        )
        result, output = self.run_engine(current, request)
        self.assert_fail(result, "wrong_edge")
        mutation_on_enter = {
            **request,
            "issued_at": self.ts(-10),
            "idempotency_key": "emergency-lease-mutation",
            "requested_mutations": ["STOP"],
        }
        result, output = self.run_engine(current, mutation_on_enter)
        self.assert_fail(result, "containment_mutation_forbidden")
        abort = self.request(
            "F8_RETRY_ABORT_CONTAINED",
            current["state_version"],
            requested_mutations=CONTAINMENT_ALLOWED,
            evidence_hashes={name: digest(name) for name in CONTAINMENT_ALLOWED},
        )
        result, output = self.run_engine(current, abort)
        self.assert_fail(result, "containment_mutation_forbidden")

    def test_containment_mode_allows_only_exact_emergency_mutations(self):
        for prime in (False, True):
            current = self.enter_containment(prime=prime)
            abort = "F8_PRIME_RETRY_ABORT_CONTAINED" if prime else "F8_RETRY_ABORT_CONTAINED"
            result_state = self.advance(
                current,
                abort,
                requested_mutations=CONTAINMENT_ALLOWED,
                evidence_hashes={name: digest(name) for name in CONTAINMENT_ALLOWED},
            )
            self.assertEqual(result_state["mode"], "NORMAL")
            self.assertEqual(result_state["to_state"], "S4" if prime else "S2")
            self.assertEqual(result_state["no_go_reason"], "F8_RETRY_EXPIRED")
            blocked_event = "F8_PRIME_COMPLETE" if prime else "F8_COMPLETE"
            blocked, output = self.run_engine(
                result_state,
                self.request(blocked_event, result_state["state_version"]),
            )
            self.assert_fail(blocked, "wrong_edge")

    def test_containment_mode_permanently_rejects_start_import_and_ancestor_restore(self):
        current = self.enter_containment()
        forbidden = [
            "START",
            "UNMASK",
            "KEY_IMPORT",
            "SLASHING_PROTECTION_IMPORT",
            "ANCESTOR_RESTORE",
            "EVIDENCE_DELETE",
        ]
        for mutation in forbidden:
            with self.subTest(mutation=mutation):
                request = self.request(
                    "F8_RETRY_ABORT_CONTAINED",
                    current["state_version"],
                    requested_mutations=[mutation],
                    evidence_hashes={name: digest(name) for name in CONTAINMENT_ALLOWED},
                )
                result, output = self.run_engine(current, request)
                self.assert_fail(result, "containment_mutation_forbidden")

    def test_abort_contained_requires_complete_containment_hashes(self):
        current = self.enter_containment()
        hashes = {name: digest(name) for name in CONTAINMENT_ALLOWED}
        hashes.pop("HARD_FENCE")
        request = self.request(
            "F8_RETRY_ABORT_CONTAINED",
            current["state_version"],
            requested_mutations=CONTAINMENT_ALLOWED,
            evidence_hashes=hashes,
        )
        result, output = self.run_engine(current, request)
        self.assert_fail(result, "evidence_missing")
        signaled = self.advance(self.state(), "INCIDENT_SIGNAL")
        conflict = self.request(
            "F0_APPROVED",
            signaled["state_version"],
            evidence_hashes={
                "F0_APPROVED": digest("F0_APPROVED"),
                "INCIDENT_SIGNAL": digest("conflicting-incident-signal"),
            },
        )
        result, _ = self.run_engine(signaled, conflict)
        self.assert_fail(result, "evidence_conflict")
        idempotent = {
            **conflict,
            "evidence_hashes": {
                "F0_APPROVED": digest("F0_APPROVED"),
                "INCIDENT_SIGNAL": signaled["evidence_hashes"]["INCIDENT_SIGNAL"],
            },
        }
        result, output = self.run_engine(signaled, idempotent)
        self.assert_pass(
            result,
            "ACTIVE_STANDBY_TRANSITION=PASS from=S1 to=S2 transition=F0_APPROVED",
        )

    def test_expired_retry_with_fresh_cas_emergency_lease_enters_containment(self):
        before = self.enter_retry()
        after = self.enter_containment()
        self.assertEqual(after["mode"], "F8_CONTAINMENT_ONLY")
        self.assertEqual(after["lease_purpose"], "EMERGENCY_CONTAINMENT")
        self.assertEqual(after["lease_expires_at"], self.ts(600))
        self.assertEqual(after["retry_deadline"], self.ts(-60))
        self.assertEqual(after["no_go_reason"], "F8_RETRY_EXPIRED")
        self.assertEqual(after["state_version"], before["state_version"] + 1)
        self.assertEqual(after["allowed_mutations"], CONTAINMENT_ALLOWED)
        self.assertIn("emergency-lease-0001", after["consumed_emergency_idempotency_keys"])
        freshness_current = self.enter_retry()
        freshness_request = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            freshness_current["state_version"],
            issued_at=self.ts(-300),
            lease_expires_at=self.ts(600),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-freshness",
            loss_reason="RETRY_FRESHNESS_LOST",
            evidence_hashes={"RETRY_FRESHNESS_LOST": digest("freshness-lost")},
        )
        result, output = self.run_engine(freshness_current, freshness_request)
        self.assert_pass(
            result,
            "ACTIVE_STANDBY_TRANSITION=PASS from=S2 to=S2 transition=F8_RETRY_CONTAIN_ENTER",
        )
        freshness_state = json.loads(output.read_text())
        self.assertEqual(freshness_state["no_go_reason"], "F8_RETRY_FRESHNESS_LOST")

    def test_expired_or_reused_emergency_lease_is_rejected(self):
        current = self.enter_retry()
        current["retry_deadline"] = self.ts(-60)
        common = dict(
            issued_at=self.ts(0),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-reused",
            loss_reason="RETRY_DEADLINE_EXPIRED",
            evidence_hashes={"RETRY_DEADLINE_EXPIRED": digest("expired")},
        )
        expired = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            current["state_version"],
            lease_expires_at=self.ts(-1),
            **common,
        )
        result, output = self.run_engine(current, expired)
        self.assert_fail(result, "lease_expired")
        current["consumed_emergency_idempotency_keys"] = ["emergency-lease-reused"]
        reused = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            current["state_version"],
            lease_expires_at=self.ts(600),
            **common,
        )
        result, output = self.run_engine(current, reused)
        self.assert_fail(result, "emergency_lease_reused")

    def test_emergency_containment_rejects_stale_expected_state_version(self):
        current = self.enter_retry()
        current["retry_deadline"] = self.ts(-60)
        request = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            current["state_version"] - 1,
            issued_at=self.ts(-300),
            lease_expires_at=self.ts(600),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-stale",
            loss_reason="RETRY_DEADLINE_EXPIRED",
            evidence_hashes={"RETRY_DEADLINE_EXPIRED": digest("expired")},
        )
        result, output = self.run_engine(current, request)
        self.assert_fail(result, "expected_version_mismatch")
        canonical = self.work / "concurrent-canonical.json"
        canonical.write_text(json.dumps(current), encoding="utf-8")
        concurrent_request = self.request(
            "F8_RETRY_CONTAIN_ENTER",
            current["state_version"],
            issued_at=self.ts(0),
            lease_expires_at=self.ts(600),
            lease_purpose="EMERGENCY_CONTAINMENT",
            idempotency_key="emergency-lease-concurrent",
            loss_reason="RETRY_DEADLINE_EXPIRED",
            evidence_hashes={"RETRY_DEADLINE_EXPIRED": digest("expired-concurrent")},
        )
        processes = []
        for index in range(2):
            request_path = self.work / f"concurrent-request-{index}.json"
            request_path.write_text(json.dumps(concurrent_request), encoding="utf-8")
            processes.append(
                subprocess.Popen(
                    [
                        "python3",
                        str(HELPER),
                        "--machine",
                        str(MACHINE),
                        "--current",
                        str(canonical),
                        "--request",
                        str(request_path),
                        "--output",
                        str(canonical),
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
            )
        outcomes = [(*process.communicate(timeout=10), process.returncode) for process in processes]
        self.assertEqual(sum(returncode == 0 for _, _, returncode in outcomes), 1, outcomes)
        self.assertEqual(sum("ACTIVE_STANDBY_TRANSITION=PASS" in stdout for stdout, _, _ in outcomes), 1)
        failures = [stderr for _, stderr, returncode in outcomes if returncode != 0]
        self.assertEqual(len(failures), 1)
        self.assertRegex(
            failures[0],
            r"^ACTIVE_STANDBY_TRANSITION=FAIL reason=(expected_version_mismatch|emergency_lease_reused)\n$",
        )
        self.assertNotIn("Traceback", "".join(stdout + stderr for stdout, stderr, _ in outcomes))
        self.assertEqual(json.loads(canonical.read_text())["state_version"], current["state_version"] + 1)

    def test_atomic_output_uses_invoking_euid_egid_without_chown(self):
        result, output = self.run_engine(self.state(), self.request("INCIDENT_SIGNAL", 0))
        self.assert_pass(
            result,
            "ACTIVE_STANDBY_TRANSITION=PASS from=S0 to=S1 transition=INCIDENT_SIGNAL",
        )
        metadata = output.stat()
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_uid, os.geteuid())
        self.assertEqual(metadata.st_gid, os.getegid())
        self.assertEqual(json.loads(output.read_text())["state_version"], 1)
        result, _ = self.run_engine(
            self.state(),
            self.request("INCIDENT_SIGNAL", 0),
            injected_directory_fsync_failure=True,
        )
        self.assert_fail(result, "output_invalid")

    def test_failback_requires_exact_sixty_minute_soak_and_continuous_fence(self):
        current = self.reach_f7()
        current = self.advance(current, "F8_COMPLETE")
        valid = self.request(
            "FAILBACK_APPROVED",
            current["state_version"],
            evidence_hashes={"STANDBY_SIGNING_SOAK_60M": digest("soak")},
            soak_samples=self.soak_samples(),
            approvers=["operator-a", "operator-b"],
        )
        result, output = self.run_engine(current, valid)
        self.assert_pass(
            result,
            "ACTIVE_STANDBY_TRANSITION=PASS from=S3 to=S4 transition=FAILBACK_APPROVED",
        )
        not_sixty = self.soak_samples()
        not_sixty[1]["observed_at"] = (self.now - timedelta(minutes=56)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        cases = {
            "twelve_samples": self.soak_samples()[:-1],
            "not_sixty_minutes": not_sixty,
            "stale_fence": [
                *self.soak_samples()[:6],
                {**self.soak_samples()[6], "aws_fence_fresh": False},
                *self.soak_samples()[7:],
            ],
            "conflict": [
                {**self.soak_samples()[0], "conflict_indicators": 1},
                *self.soak_samples()[1:],
            ],
            "slashing": [
                {**self.soak_samples()[0], "slashing_indicators": 1},
                *self.soak_samples()[1:],
            ],
        }
        for label, samples in cases.items():
            with self.subTest(case=label):
                request = {**valid, "soak_samples": samples}
                result, rejected_output = self.run_engine(current, request)
                self.assert_fail(result, "evidence_missing")
        duplicate = {**valid, "approvers": ["operator-a", "operator-a"]}
        result, output = self.run_engine(current, duplicate)
        self.assert_fail(result, "evidence_missing")
        forged_s3 = self.state(operational_state="S3", version=10)
        forged_s3.update(transition="F8_COMPLETE", last_completed_gate="F8_COMPLETE")
        result, _ = self.run_engine(forged_s3, {**valid, "expected_state_version": 10})
        self.assert_fail(result, "invalid_current_state")
        forged_s4 = self.state(operational_state="S4", version=11)
        forged_s4.update(transition="FAILBACK_APPROVED", last_completed_gate="FAILBACK_APPROVED")
        result, _ = self.run_engine(
            forged_s4,
            self.request("F3_PRIME_PASS", 11),
        )
        self.assert_fail(result, "invalid_current_state")

    def test_expired_or_stale_lease_is_rejected(self):
        current = self.advance(self.state(), "INCIDENT_SIGNAL")
        current["lease_expires_at"] = self.ts(-1)
        request = self.request(
            "F0_APPROVED",
            current["state_version"],
            issued_at=self.ts(-3600),
            lease_expires_at=self.ts(600),
        )
        result, output = self.run_engine(current, request)
        self.assert_fail(result, "lease_expired")
        current = self.advance(self.state(), "INCIDENT_SIGNAL")
        stale = self.request("F0_APPROVED", current["state_version"] - 1)
        result, output = self.run_engine(current, stale)
        self.assert_fail(result, "expected_version_mismatch")

    def test_invalid_transition_does_not_write_output(self):
        invalid_current = self.state()
        invalid_request = self.request("INCIDENT_SIGNAL", 0)
        cases = [
            (invalid_current, {**invalid_request, "unknown": True}, "invalid_request"),
            (invalid_current, {key: value for key, value in invalid_request.items() if key != "event"}, "invalid_request"),
            (invalid_current, {**invalid_request, "issued_at": "not-a-time"}, "invalid_request"),
            (invalid_current, {**invalid_request, "evidence_hashes": {"INCIDENT_SIGNAL": "bad"}}, "invalid_request"),
            ({**invalid_current, "unknown": True}, invalid_request, "invalid_current_state"),
            ({key: value for key, value in invalid_current.items() if key != "state_version"}, invalid_request, "invalid_current_state"),
            ({**invalid_current, "issued_at": "not-a-time"}, invalid_request, "invalid_current_state"),
            ({**invalid_current, "evidence_hashes": {"X": "bad"}}, invalid_request, "invalid_current_state"),
            ('{"schema_version":"active-standby-state-v1","schema_version":"duplicate"}', invalid_request, "invalid_current_state"),
            (invalid_current, '{"event":"INCIDENT_SIGNAL","event":"F0_APPROVED"}', "invalid_request"),
        ]
        for index, (current, request, reason) in enumerate(cases):
            with self.subTest(case=index):
                result, output = self.run_engine(current, request)
                self.assert_fail(result, reason)
        target = self.work / "real-output.json"
        target.write_text("preserve", encoding="utf-8")
        symlink = self.work / "output-link.json"
        symlink.symlink_to(target)
        result, _ = self.run_engine(self.state(), invalid_request, output=symlink)
        self.assert_fail(result, "output_invalid")
        self.assertEqual(target.read_text(), "preserve")
        directory = self.work / "output-directory"
        directory.mkdir()
        result, _ = self.run_engine(self.state(), invalid_request, output=directory)
        self.assert_fail(result, "output_invalid")
        current_path = self.work / "different-current.json"
        output_path = self.work / "different-output.json"
        request_path = self.work / "different-request.json"
        current_path.write_text(json.dumps(self.state()), encoding="utf-8")
        output_path.write_text("unchanged-output\n", encoding="utf-8")
        request_path.write_text(json.dumps(invalid_request), encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(HELPER),
                "--machine",
                str(MACHINE),
                "--current",
                str(current_path),
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assert_fail(result, "output_invalid")
        self.assertEqual(output_path.read_text(), "unchanged-output\n")
        lock_state = self.work / "lock-state.json"
        lock_target = self.work / "lock-target"
        lock_target.write_text("do-not-trust", encoding="utf-8")
        Path(f"{lock_state}.lock").symlink_to(lock_target)
        result, _ = self.run_engine(
            self.state(), invalid_request, output=lock_state
        )
        self.assert_fail(result, "output_invalid")
        self.assertEqual(lock_target.read_text(), "do-not-trust")


if __name__ == "__main__":
    unittest.main()
