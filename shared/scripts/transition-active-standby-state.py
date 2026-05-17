#!/usr/bin/env python3
"""Pure local, fail-closed Active-Standby transition authority."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MACHINE = ROOT / "shared/config/active-standby-state-machine.json"
FROZEN_MACHINE_SHA256 = "40712de668557d25c013c1cfd360afeae20f5951e7dff8c97471c06ffb2aaf96"
STATE_KEYS = {
    "schema_version", "from_state", "to_state", "transition", "mode",
    "incident_id", "pubkey", "lease_owner", "lease_purpose", "issued_at",
    "lease_expires_at", "retry_deadline", "allowed_mutations",
    "last_completed_gate", "evidence_hashes", "state_version", "no_go_reason",
    "consumed_emergency_idempotency_keys",
}
REQUEST_KEYS = {
    "event", "expected_state_version", "incident_id", "pubkey", "lease_owner",
    "lease_purpose", "issued_at", "lease_expires_at", "retry_deadline",
    "idempotency_key", "loss_reason", "requested_mutations", "evidence_hashes",
    "soak_samples", "approvers",
}
SAMPLE_KEYS = {
    "observed_at", "aws_fence_fresh", "conflict_indicators", "slashing_indicators",
}
STATES = ["S0", "S1", "S2", "S3", "S4"]
MODES = [
    "NORMAL", "F8_RETRY_ONLY", "F8_CONTAINMENT_ONLY",
    "F8_PRIME_RETRY_ONLY", "F8_PRIME_CONTAINMENT_ONLY",
]
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
PUBKEY_RE = re.compile(r"0x[0-9a-f]{96}\Z")
INCIDENT_RE = re.compile(r"INC-[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
BASE_GATES = ["F0_APPROVED", *[f"F{i}_PASS" for i in range(1, 8)]]
PRIME_GATES = [
    "F3_PRIME_PASS", "F1_PRIME_PASS", "F2_PRIME_PASS",
    "F5_PRIME_PASS", "F6_PRIME_PASS", "F7_PRIME_PASS",
]


class DuplicateKey(ValueError):
    pass


class TransitionError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def regular_file(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
        return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
    except OSError:
        return False


def read_json(path: Path) -> Any:
    if not regular_file(path):
        raise ValueError("input is not a regular file")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=unique_object)


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or TIME_RE.fullmatch(value) is None:
        raise ValueError("invalid timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def evidence_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_hash_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and bool(key) and isinstance(item, str)
        and HASH_RE.fullmatch(item) is not None
        for key, item in value.items()
    )


def unique_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(value) == len(set(value))
    )


def valid_machine(path: Path, machine: Any) -> bool:
    try:
        return (
            path.resolve(strict=True) == CANONICAL_MACHINE.resolve(strict=True)
            and hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_MACHINE_SHA256
            and isinstance(machine, dict)
            and machine["operational_states"] == STATES
            and machine["modes"] == MODES
            and machine["retry_max_seconds"] == 3600
            and machine["prime_gate_order"] == PRIME_GATES
            and machine["retry_allowed_mutations"] == ["FINAL_EVIDENCE_WRITE"]
            and machine["containment_allowed_mutations"] == [
                "STOP", "MASK", "FRESH_DESCENDANT_SP_EXPORT",
                "FRESH_DESCENDANT_SP_PRESERVE", "RESEAL", "HARD_FENCE",
                "EVIDENCE_WRITE",
            ]
            and machine["failback"] == {
                "evidence": "STANDBY_SIGNING_SOAK_60M",
                "duration_seconds": 3600,
                "sample_count": 13,
                "distinct_approver_count": 2,
            }
        )
    except (OSError, KeyError, TypeError):
        return False


def syntactically_valid_state(state: Any, machine: dict[str, Any]) -> bool:
    if not isinstance(state, dict) or set(state) != STATE_KEYS:
        return False
    if (
        state["schema_version"] != "active-standby-state-v1"
        or state["from_state"] not in STATES or state["to_state"] not in STATES
        or not isinstance(state["transition"], str) or not state["transition"]
        or state["mode"] not in MODES
        or not isinstance(state["incident_id"], str)
        or INCIDENT_RE.fullmatch(state["incident_id"]) is None
        or not isinstance(state["pubkey"], str) or PUBKEY_RE.fullmatch(state["pubkey"]) is None
        or not isinstance(state["lease_owner"], str)
        or PRINCIPAL_RE.fullmatch(state["lease_owner"]) is None
        or state["lease_purpose"] not in {"OPERATIONAL_TRANSITION", "EMERGENCY_CONTAINMENT"}
        or not unique_strings(state["allowed_mutations"])
        or not valid_hash_map(state["evidence_hashes"])
        or not isinstance(state["state_version"], int)
        or isinstance(state["state_version"], bool) or state["state_version"] < 0
        or state["no_go_reason"] not in {None, "F8_RETRY_EXPIRED", "F8_RETRY_FRESHNESS_LOST"}
        or not unique_strings(state["consumed_emergency_idempotency_keys"])
        or any(IDEMPOTENCY_RE.fullmatch(item) is None for item in state["consumed_emergency_idempotency_keys"])
        or state["last_completed_gate"] is not None
        and not isinstance(state["last_completed_gate"], str)
    ):
        return False
    try:
        parse_time(state["issued_at"])
        parse_time(state["lease_expires_at"])
        if state["retry_deadline"] is not None:
            parse_time(state["retry_deadline"])
    except ValueError:
        return False
    expected_mutations = {
        "NORMAL": [],
        "F8_RETRY_ONLY": machine["retry_allowed_mutations"],
        "F8_PRIME_RETRY_ONLY": machine["retry_allowed_mutations"],
        "F8_CONTAINMENT_ONLY": machine["containment_allowed_mutations"],
        "F8_PRIME_CONTAINMENT_ONLY": machine["containment_allowed_mutations"],
    }
    return state["allowed_mutations"] == expected_mutations[state["mode"]]


def history_candidates(state: dict[str, Any], machine: dict[str, Any]) -> list[dict[str, Any]]:
    bindings = {"INCIDENT_BINDING", "PUBKEY_BINDING"}
    incident = {"INCIDENT_SIGNAL", *bindings}
    candidates: list[dict[str, Any]] = []

    def add(
        transition: str, from_state: str, to_state: str, mode: str,
        last: str | None, version: int, keys: set[str],
        *, no_go: str | None = None, retry: bool = False,
        emergency: bool = False,
    ) -> None:
        candidates.append({
            "transition": transition, "from_state": from_state, "to_state": to_state,
            "mode": mode, "last_completed_gate": last, "state_version": version,
            "keys": keys, "no_go_reason": no_go, "retry": retry,
            "lease_purpose": "EMERGENCY_CONTAINMENT" if emergency else "OPERATIONAL_TRANSITION",
        })

    add("INITIALIZED", "S0", "S0", "NORMAL", None, 0, set())
    add("INCIDENT_SIGNAL", "S0", "S1", "NORMAL", "INCIDENT_SIGNAL", 1, incident)
    add("SOURCE_RETAINED", "S1", "S0", "NORMAL", "SOURCE_RETAINED", 2, incident | {"SOURCE_RETAINED"})
    base_keys = set(incident)
    for index, gate in enumerate(BASE_GATES, start=2):
        base_keys = base_keys | {gate}
        add(gate, "S1" if gate == "F0_APPROVED" else "S2", "S2", "NORMAL", gate, index, set(base_keys))

    retry_keys = base_keys | {"AWS_FENCE_FRESH", "STANDBY_SOLE_SIGNER"}
    add("F8_RETRY_ENTER", "S2", "S2", "F8_RETRY_ONLY", "F7_PASS", 10, retry_keys, retry=True)
    for had_retry, complete_version, complete_keys in (
        (False, 10, base_keys | {"F8_COMPLETE"}),
        (True, 11, retry_keys | {"F8_COMPLETE"}),
    ):
        add("F8_COMPLETE", "S2", "S3", "NORMAL", "F8_COMPLETE", complete_version, complete_keys)
        failback_keys = complete_keys | {"STANDBY_SIGNING_SOAK_60M"}
        failback_version = complete_version + 1
        add("FAILBACK_APPROVED", "S3", "S4", "NORMAL", "FAILBACK_APPROVED", failback_version, failback_keys)
        prime_keys = set(failback_keys)
        prime_version = failback_version
        for gate in PRIME_GATES:
            prime_version += 1
            prime_keys.add(gate)
            add(gate, "S4", "S4", "NORMAL", gate, prime_version, set(prime_keys))
        prime_retry_keys = prime_keys | {"STANDBY_HARD_FENCE_FRESH", "AWS_SOLE_SIGNER"}
        add(
            "F8_PRIME_RETRY_ENTER", "S4", "S4", "F8_PRIME_RETRY_ONLY",
            "F7_PRIME_PASS", prime_version + 1, prime_retry_keys, retry=True,
        )
        add(
            "F8_PRIME_COMPLETE", "S4", "S0", "NORMAL", "F8_PRIME_COMPLETE",
            prime_version + 1, prime_keys | {"F8_PRIME_COMPLETE"},
        )
        add(
            "F8_PRIME_COMPLETE", "S4", "S0", "NORMAL", "F8_PRIME_COMPLETE",
            prime_version + 2, prime_retry_keys | {"F8_PRIME_COMPLETE"},
        )
        for loss_key, no_go in (
            ("RETRY_DEADLINE_EXPIRED", "F8_RETRY_EXPIRED"),
            ("RETRY_FRESHNESS_LOST", "F8_RETRY_FRESHNESS_LOST"),
        ):
            contained = prime_retry_keys | {loss_key}
            add(
                "F8_PRIME_RETRY_CONTAIN_ENTER", "S4", "S4",
                "F8_PRIME_CONTAINMENT_ONLY", "F7_PRIME_PASS", prime_version + 2,
                contained, no_go=no_go, retry=True, emergency=True,
            )
            add(
                "F8_PRIME_RETRY_ABORT_CONTAINED", "S4", "S4", "NORMAL",
                "F7_PRIME_PASS", prime_version + 3,
                contained | set(machine["containment_allowed_mutations"]),
                no_go=no_go, retry=True,
            )

    for loss_key, no_go in (
        ("RETRY_DEADLINE_EXPIRED", "F8_RETRY_EXPIRED"),
        ("RETRY_FRESHNESS_LOST", "F8_RETRY_FRESHNESS_LOST"),
    ):
        contained = retry_keys | {loss_key}
        add(
            "F8_RETRY_CONTAIN_ENTER", "S2", "S2", "F8_CONTAINMENT_ONLY",
            "F7_PASS", 11, contained, no_go=no_go, retry=True, emergency=True,
        )
        add(
            "F8_RETRY_ABORT_CONTAINED", "S2", "S2", "NORMAL", "F7_PASS", 12,
            contained | set(machine["containment_allowed_mutations"]),
            no_go=no_go, retry=True,
        )
    return candidates


def valid_state(state: Any, machine: dict[str, Any]) -> bool:
    if not syntactically_valid_state(state, machine):
        return False
    assert isinstance(state, dict)
    candidates = history_candidates(state, machine)
    for candidate in candidates:
        if any(state[key] != candidate[key] for key in (
            "transition", "from_state", "to_state", "mode", "last_completed_gate",
            "state_version", "no_go_reason", "lease_purpose",
        )):
            continue
        if set(state["evidence_hashes"]) != candidate["keys"]:
            continue
        retry_present = state["retry_deadline"] is not None
        if retry_present != candidate["retry"]:
            continue
        if candidate["keys"]:
            if state["evidence_hashes"].get("INCIDENT_BINDING") != evidence_digest(state["incident_id"]):
                continue
            if state["evidence_hashes"].get("PUBKEY_BINDING") != evidence_digest(state["pubkey"]):
                continue
        return True
    return False


def valid_request(request: Any) -> bool:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        return False
    if (
        not isinstance(request["event"], str) or not request["event"]
        or not isinstance(request["expected_state_version"], int)
        or isinstance(request["expected_state_version"], bool)
        or request["expected_state_version"] < 0
        or not isinstance(request["incident_id"], str)
        or INCIDENT_RE.fullmatch(request["incident_id"]) is None
        or not isinstance(request["pubkey"], str) or PUBKEY_RE.fullmatch(request["pubkey"]) is None
        or not isinstance(request["lease_owner"], str)
        or PRINCIPAL_RE.fullmatch(request["lease_owner"]) is None
        or request["lease_purpose"] not in {"OPERATIONAL_TRANSITION", "EMERGENCY_CONTAINMENT"}
        or not unique_strings(request["requested_mutations"])
        or not valid_hash_map(request["evidence_hashes"])
        or not isinstance(request["approvers"], list)
        or any(not isinstance(item, str) or PRINCIPAL_RE.fullmatch(item) is None for item in request["approvers"])
    ):
        return False
    for nullable in ("idempotency_key", "loss_reason"):
        if request[nullable] is not None and not isinstance(request[nullable], str):
            return False
    try:
        parse_time(request["issued_at"])
        parse_time(request["lease_expires_at"])
        if request["retry_deadline"] is not None:
            parse_time(request["retry_deadline"])
    except ValueError:
        return False
    if not isinstance(request["soak_samples"], list):
        return False
    for sample in request["soak_samples"]:
        if not isinstance(sample, dict) or set(sample) != SAMPLE_KEYS:
            return False
        if (
            not isinstance(sample["aws_fence_fresh"], bool)
            or not isinstance(sample["conflict_indicators"], int)
            or isinstance(sample["conflict_indicators"], bool)
            or sample["conflict_indicators"] < 0
            or not isinstance(sample["slashing_indicators"], int)
            or isinstance(sample["slashing_indicators"], bool)
            or sample["slashing_indicators"] < 0
        ):
            return False
        try:
            parse_time(sample["observed_at"])
        except ValueError:
            return False
    return True


def find_transition(machine: dict[str, Any], event: str) -> dict[str, str] | None:
    return next((row for row in machine["transitions"] if row["event"] == event), None)


def expected_evidence(machine: dict[str, Any], request: dict[str, Any]) -> set[str]:
    event = request["event"]
    if event in machine["retry_entry_evidence"]:
        return set(machine["retry_entry_evidence"][event])
    if event in {"F8_RETRY_CONTAIN_ENTER", "F8_PRIME_RETRY_CONTAIN_ENTER"}:
        return {request["loss_reason"]} if request["loss_reason"] in {
            "RETRY_DEADLINE_EXPIRED", "RETRY_FRESHNESS_LOST",
        } else set()
    if event in {"F8_RETRY_ABORT_CONTAINED", "F8_PRIME_RETRY_ABORT_CONTAINED"}:
        return set(machine["containment_allowed_mutations"])
    if event == "FAILBACK_APPROVED":
        return {"STANDBY_SIGNING_SOAK_60M"}
    return {event}


def validate_evidence(current: dict[str, Any], request: dict[str, Any], machine: dict[str, Any]) -> None:
    for key, value in request["evidence_hashes"].items():
        if key in current["evidence_hashes"] and current["evidence_hashes"][key] != value:
            raise TransitionError("evidence_conflict")
    required = expected_evidence(machine, request)
    if not required or not required.issubset(request["evidence_hashes"]):
        raise TransitionError("evidence_missing")
    if any(key not in required and key not in current["evidence_hashes"] for key in request["evidence_hashes"]):
        raise TransitionError("evidence_missing")


def validate_soak(request: dict[str, Any]) -> None:
    samples = request["soak_samples"]
    if len(samples) != 13 or len(request["approvers"]) != 2 or len(set(request["approvers"])) != 2:
        raise TransitionError("evidence_missing")
    times = [parse_time(sample["observed_at"]) for sample in samples]
    if any((right - left).total_seconds() != 300 for left, right in zip(times, times[1:])):
        raise TransitionError("evidence_missing")
    if (times[-1] - times[0]).total_seconds() != 3600:
        raise TransitionError("evidence_missing")
    if any(
        not sample["aws_fence_fresh"] or sample["conflict_indicators"] != 0
        or sample["slashing_indicators"] != 0 for sample in samples
    ):
        raise TransitionError("evidence_missing")


def enforce_gate_order(current: dict[str, Any], event: str, machine: dict[str, Any]) -> None:
    previous = current["last_completed_gate"]
    if event == "F0_APPROVED" and previous != "INCIDENT_SIGNAL":
        raise TransitionError("gate_out_of_order")
    if re.fullmatch(r"F[1-7]_PASS", event):
        number = int(event[1])
        expected = "F0_APPROVED" if number == 1 else f"F{number - 1}_PASS"
        if previous != expected:
            raise TransitionError("gate_out_of_order")
    if event in PRIME_GATES:
        index = PRIME_GATES.index(event)
        expected = "FAILBACK_APPROVED" if index == 0 else PRIME_GATES[index - 1]
        if previous != expected:
            raise TransitionError("gate_out_of_order")
    if event in {"F8_RETRY_ENTER", "F8_COMPLETE"} and previous != "F7_PASS":
        raise TransitionError("gate_out_of_order")
    if event in {"F8_PRIME_RETRY_ENTER", "F8_PRIME_COMPLETE"} and previous != "F7_PRIME_PASS":
        raise TransitionError("gate_out_of_order")
    if event == "FAILBACK_APPROVED" and previous != "F8_COMPLETE":
        raise TransitionError("gate_out_of_order")


def transition(machine: dict[str, Any], current: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    event = request["event"]
    if request["expected_state_version"] != current["state_version"]:
        raise TransitionError("expected_version_mismatch")
    if request["incident_id"] != current["incident_id"] or request["pubkey"] != current["pubkey"]:
        raise TransitionError("wrong_edge")
    if current["no_go_reason"] is not None and current["mode"] not in {
        "F8_CONTAINMENT_ONLY", "F8_PRIME_CONTAINMENT_ONLY",
    }:
        raise TransitionError("wrong_edge")
    row = find_transition(machine, event)
    if row is None or row["from"] != current["to_state"]:
        raise TransitionError("wrong_edge")
    if current["mode"] not in row["required_mode"].split("|"):
        if event in {"F8_RETRY_ABORT_CONTAINED", "F8_PRIME_RETRY_ABORT_CONTAINED"}:
            raise TransitionError("containment_mutation_forbidden")
        raise TransitionError("wrong_edge")

    now = utc_now()
    containment_enter = event in {"F8_RETRY_CONTAIN_ENTER", "F8_PRIME_RETRY_CONTAIN_ENTER"}
    if not containment_enter and parse_time(current["lease_expires_at"]) <= now:
        raise TransitionError("lease_expired")
    if parse_time(request["lease_expires_at"]) <= now:
        raise TransitionError("lease_expired")
    if containment_enter:
        if request["lease_purpose"] != "EMERGENCY_CONTAINMENT" or request["requested_mutations"]:
            raise TransitionError("containment_mutation_forbidden")
    elif request["lease_purpose"] != "OPERATIONAL_TRANSITION":
        raise TransitionError("wrong_edge")

    retry_enter = event in {"F8_RETRY_ENTER", "F8_PRIME_RETRY_ENTER"}
    retry_complete = event in {"F8_COMPLETE", "F8_PRIME_COMPLETE"}
    abort = event in {"F8_RETRY_ABORT_CONTAINED", "F8_PRIME_RETRY_ABORT_CONTAINED"}
    if retry_enter and request["requested_mutations"]:
        raise TransitionError("retry_mutation_forbidden")
    if retry_complete:
        if current["mode"] in {"F8_RETRY_ONLY", "F8_PRIME_RETRY_ONLY"}:
            if request["requested_mutations"] != machine["retry_allowed_mutations"]:
                raise TransitionError("retry_mutation_forbidden")
            if current["retry_deadline"] is None or parse_time(current["retry_deadline"]) <= now:
                raise TransitionError("wrong_edge")
        elif request["requested_mutations"]:
            raise TransitionError("retry_mutation_forbidden")
    elif abort:
        if any(item not in machine["containment_allowed_mutations"] for item in request["requested_mutations"]):
            raise TransitionError("containment_mutation_forbidden")
        if set(request["requested_mutations"]) != set(machine["containment_allowed_mutations"]):
            raise TransitionError("evidence_missing")
    elif request["requested_mutations"]:
        raise TransitionError("wrong_edge")

    enforce_gate_order(current, event, machine)
    validate_evidence(current, request, machine)
    mode = current["mode"]
    retry_deadline = current["retry_deadline"]
    allowed_mutations = list(current["allowed_mutations"])
    no_go_reason = current["no_go_reason"]
    consumed = list(current["consumed_emergency_idempotency_keys"])
    last_completed_gate = event

    if event == "INCIDENT_SIGNAL":
        request["evidence_hashes"]["INCIDENT_BINDING"] = evidence_digest(current["incident_id"])
        request["evidence_hashes"]["PUBKEY_BINDING"] = evidence_digest(current["pubkey"])
    if retry_enter:
        if request["retry_deadline"] is None:
            raise TransitionError("wrong_edge")
        deadline = parse_time(request["retry_deadline"])
        seconds = (deadline - now).total_seconds()
        if seconds <= 0 or seconds > 3600:
            raise TransitionError("wrong_edge")
        retry_deadline = request["retry_deadline"]
        mode = "F8_PRIME_RETRY_ONLY" if "PRIME" in event else "F8_RETRY_ONLY"
        allowed_mutations = list(machine["retry_allowed_mutations"])
        last_completed_gate = current["last_completed_gate"]
    elif retry_complete:
        mode, retry_deadline, allowed_mutations = "NORMAL", None, []
    elif containment_enter:
        key = request["idempotency_key"]
        if not isinstance(key, str) or IDEMPOTENCY_RE.fullmatch(key) is None:
            raise TransitionError("wrong_edge")
        if key in consumed:
            raise TransitionError("emergency_lease_reused")
        if retry_deadline is None:
            raise TransitionError("wrong_edge")
        if request["loss_reason"] == "RETRY_DEADLINE_EXPIRED":
            if now < parse_time(retry_deadline):
                raise TransitionError("wrong_edge")
            no_go_reason = "F8_RETRY_EXPIRED"
        elif request["loss_reason"] == "RETRY_FRESHNESS_LOST":
            no_go_reason = "F8_RETRY_FRESHNESS_LOST"
        else:
            raise TransitionError("wrong_edge")
        consumed.append(key)
        mode = "F8_PRIME_CONTAINMENT_ONLY" if "PRIME" in event else "F8_CONTAINMENT_ONLY"
        allowed_mutations = list(machine["containment_allowed_mutations"])
        last_completed_gate = current["last_completed_gate"]
    elif abort:
        mode, allowed_mutations = "NORMAL", []
        last_completed_gate = current["last_completed_gate"]
    elif event == "FAILBACK_APPROVED":
        validate_soak(request)

    evidence_hashes = dict(current["evidence_hashes"])
    evidence_hashes.update(request["evidence_hashes"])
    return {
        "schema_version": "active-standby-state-v1",
        "from_state": current["to_state"], "to_state": row["to"],
        "transition": event, "mode": mode, "incident_id": current["incident_id"],
        "pubkey": current["pubkey"], "lease_owner": request["lease_owner"],
        "lease_purpose": request["lease_purpose"], "issued_at": request["issued_at"],
        "lease_expires_at": request["lease_expires_at"], "retry_deadline": retry_deadline,
        "allowed_mutations": allowed_mutations, "last_completed_gate": last_completed_gate,
        "evidence_hashes": evidence_hashes, "state_version": current["state_version"] + 1,
        "no_go_reason": no_go_reason,
        "consumed_emergency_idempotency_keys": consumed,
    }


def output_is_safe(path: Path) -> bool:
    try:
        return regular_file(path) and stat.S_ISDIR(os.lstat(path.parent).st_mode) and not path.parent.is_symlink()
    except OSError:
        return False


def lock_file(path: Path) -> int:
    lock_path = Path(f"{path}.lock")
    if os.path.lexists(lock_path) and not regular_file(lock_path):
        raise TransitionError("output_invalid")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise TransitionError("output_invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except OSError as exc:
        raise TransitionError("output_invalid") from exc


def write_bytes_atomic(path: Path, payload: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    existed = path.exists()
    prior_bytes = path.read_bytes() if existed else b""
    prior_mode = stat.S_IMODE(path.stat().st_mode) if existed else 0o600
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    replaced = False
    try:
        write_bytes_atomic(path, payload, 0o600)
        replaced = True
        fsync_parent(path)
    except OSError as exc:
        if replaced:
            try:
                if existed:
                    write_bytes_atomic(path, prior_bytes, prior_mode)
                else:
                    os.unlink(path)
                fsync_parent(path)
            except OSError:
                pass
        raise TransitionError("output_invalid") from exc


def parse_paths(argv: list[str]) -> dict[str, Path] | None:
    allowed = {"--machine", "--current", "--request", "--output"}
    if len(argv) != 8:
        return None
    values: dict[str, Path] = {}
    for index in range(0, len(argv), 2):
        if argv[index] not in allowed or argv[index] in values:
            return None
        values[argv[index]] = Path(argv[index + 1])
    return values if set(values) == allowed else None


def fail(reason: str) -> int:
    print(f"ACTIVE_STANDBY_TRANSITION=FAIL reason={reason}", file=sys.stderr)
    return 2


def same_canonical_path(current: Path, output: Path) -> bool:
    try:
        return current.resolve(strict=True) == output.resolve(strict=True)
    except OSError:
        return False


def main(argv: list[str]) -> int:
    paths = parse_paths(argv)
    if paths is None:
        return fail("invalid_request")
    output = paths["--output"]
    if not same_canonical_path(paths["--current"], output) or not output_is_safe(output):
        return fail("output_invalid")
    try:
        machine = read_json(paths["--machine"])
    except (OSError, UnicodeError, ValueError, TypeError):
        return fail("invalid_machine")
    if not valid_machine(paths["--machine"], machine):
        return fail("invalid_machine")
    try:
        request = read_json(paths["--request"])
    except (OSError, UnicodeError, ValueError, TypeError):
        return fail("invalid_request")
    if not valid_request(request):
        return fail("invalid_request")

    descriptor = -1
    try:
        descriptor = lock_file(output)
        if not output_is_safe(output):
            raise TransitionError("output_invalid")
        try:
            current = read_json(output)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise TransitionError("invalid_current_state") from exc
        if not valid_state(current, machine):
            raise TransitionError("invalid_current_state")
        new_state = transition(machine, current, request)
        if not valid_state(new_state, machine):
            raise TransitionError("invalid_current_state")
        atomic_write(output, new_state)
    except TransitionError as exc:
        return fail(exc.reason)
    finally:
        if descriptor >= 0:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    print(
        "ACTIVE_STANDBY_TRANSITION=PASS "
        f"from={new_state['from_state']} to={new_state['to_state']} transition={new_state['transition']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
