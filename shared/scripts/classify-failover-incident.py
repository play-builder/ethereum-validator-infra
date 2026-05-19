#!/usr/bin/env python3
"""Fail-closed, local-only incident classification authority."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_POLICY = ROOT / "shared/config/failover-incident-policy.json"
FROZEN_POLICY_SHA256 = "eb0f9aa7b205ae2939bf1608861a9ad5cd18bff6e5a17ee03185577b4a98f04d"
INPUT_KEYS = {
    "schema_version",
    "incident_id",
    "incident_type",
    "primary_execution_client",
    "standby_execution_client",
    "affected_sites",
    "hard_fence_evidence_hash",
    "change_id",
    "approval_evidence_hash",
    "approvers",
}
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
INCIDENT_RE = re.compile(r"INC-[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class DuplicateKey(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=unique_object)


def parse_paths(argv: list[str]) -> tuple[Path, Path] | None:
    if len(argv) != 4:
        return None
    values: dict[str, Path] = {}
    for index in range(0, len(argv), 2):
        option = argv[index]
        if option not in {"--policy", "--input"} or option in values:
            return None
        values[option] = Path(argv[index + 1])
    if set(values) != {"--policy", "--input"}:
        return None
    return values["--policy"], values["--input"]


def valid_hash(value: Any) -> bool:
    return value is None or (isinstance(value, str) and HASH_RE.fullmatch(value) is not None)


def valid_policy(path: Path, policy: Any) -> bool:
    if not isinstance(policy, dict):
        return False
    try:
        frozen = (
            path.resolve(strict=True) == CANONICAL_POLICY.resolve(strict=True)
            and hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_POLICY_SHA256
        )
    except OSError:
        return False
    return frozen and (
        set(policy) == {
            "schema_version", "outputs", "incident_types", "common_client",
            "planned_drill", "site_failure",
        }
        and policy.get("schema_version") == "failover-incident-policy-v1"
        and policy.get("outputs")
        == [
            "ALLOW_PLANNED_DRILL",
            "ALLOW_SITE_FAILURE_REVIEW",
            "NO_GO_COMMON_CLIENT",
            "NO_GO_UNCLASSIFIED",
        ]
        and policy.get("incident_types")
        == ["PLANNED_DRILL", "SITE_FAILURE", "COMMON_CLIENT_FAILURE", "UNKNOWN"]
        and policy.get("common_client") == "nethermind"
        and policy.get("planned_drill") == {
            "change_id_pattern": "^CHG-[0-9]{4}-[0-9]{4,}$",
            "distinct_approver_count": 2,
            "approval_hash_algorithm": "sha256",
        }
        and policy.get("site_failure") == {
            "hard_fence_required": True,
            "result_is_review_only": True,
        }
    )


def valid_input(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != INPUT_KEYS:
        return False
    if value["schema_version"] != "incident-classification-v1":
        return False
    if not isinstance(value["incident_id"], str) or INCIDENT_RE.fullmatch(value["incident_id"]) is None:
        return False
    if value["incident_type"] not in {
        "PLANNED_DRILL",
        "SITE_FAILURE",
        "COMMON_CLIENT_FAILURE",
        "UNKNOWN",
    }:
        return False
    for key in ("primary_execution_client", "standby_execution_client"):
        if not isinstance(value[key], str) or not value[key]:
            return False
    sites = value["affected_sites"]
    if (
        not isinstance(sites, list)
        or any(site not in {"primary-aws", "standby-standby"} for site in sites)
        or len(sites) != len(set(sites))
    ):
        return False
    if not valid_hash(value["hard_fence_evidence_hash"]) or not valid_hash(value["approval_evidence_hash"]):
        return False
    if value["change_id"] is not None and not isinstance(value["change_id"], str):
        return False
    approvers = value["approvers"]
    return (
        isinstance(approvers, list)
        and all(isinstance(item, str) and PRINCIPAL_RE.fullmatch(item) for item in approvers)
        and len(approvers) == len(set(approvers))
    )


def classify(policy: dict[str, Any], incident: dict[str, Any]) -> str:
    incident_type = incident["incident_type"]
    common_client = policy["common_client"]
    if (
        incident["primary_execution_client"] == common_client
        and incident["standby_execution_client"] == common_client
    ):
        return "NO_GO_COMMON_CLIENT"
    if incident_type == "PLANNED_DRILL":
        drill = policy["planned_drill"]
        change_id = incident["change_id"]
        approvers = incident["approvers"]
        if (
            isinstance(change_id, str)
            and re.fullmatch(drill["change_id_pattern"], change_id)
            and isinstance(incident["approval_evidence_hash"], str)
            and HASH_RE.fullmatch(incident["approval_evidence_hash"])
            and len(approvers) == drill["distinct_approver_count"]
            and len(set(approvers)) == drill["distinct_approver_count"]
            and incident["affected_sites"] == []
            and incident["hard_fence_evidence_hash"] is None
        ):
            return "ALLOW_PLANNED_DRILL"
        return "NO_GO_UNCLASSIFIED"
    if incident_type == "SITE_FAILURE":
        if (
            isinstance(incident["hard_fence_evidence_hash"], str)
            and HASH_RE.fullmatch(incident["hard_fence_evidence_hash"])
            and len(incident["affected_sites"]) == 1
        ):
            return "ALLOW_SITE_FAILURE_REVIEW"
        return "NO_GO_UNCLASSIFIED"
    return "NO_GO_UNCLASSIFIED"


def main(argv: list[str]) -> int:
    paths = parse_paths(argv)
    result = "NO_GO_UNCLASSIFIED"
    if paths is not None:
        try:
            policy = read_json(paths[0])
            incident = read_json(paths[1])
            if valid_policy(paths[0], policy) and valid_input(incident):
                result = classify(policy, incident)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, re.error):
            pass
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
