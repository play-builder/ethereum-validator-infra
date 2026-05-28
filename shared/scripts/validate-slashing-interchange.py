#!/usr/bin/env python3
"""Read-only validation of one Hoodi EIP-3076 slashing interchange file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


HEX_32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
PUBKEY = re.compile(r"^0x[0-9a-fA-F]{96}$")
UINT = re.compile(r"^(0|[1-9][0-9]*)$")


def normalized_hex(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    return value.lower()


def unsigned_integer(value: object) -> int | None:
    if not isinstance(value, str) or not UINT.fullmatch(value):
        return None
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate public EIP-3076 slashing history without changing it."
    )
    parser.add_argument("--interchange", required=True, type=Path)
    parser.add_argument("--expected-genesis-validators-root", required=True)
    parser.add_argument("--expected-pubkey", required=True)
    arguments = parser.parse_args()

    expected_root = normalized_hex(arguments.expected_genesis_validators_root, HEX_32)
    expected_pubkey = normalized_hex(arguments.expected_pubkey, PUBKEY)
    if expected_root is None or expected_pubkey is None:
        print("CHECK=invalid_expected_identity STATUS=FAIL")
        print("SLASHING_INTERCHANGE=FAIL")
        return 64
    if not arguments.interchange.is_file() or arguments.interchange.is_symlink():
        print("CHECK=interchange_must_be_regular_file STATUS=FAIL")
        print("SLASHING_INTERCHANGE=FAIL")
        return 1

    try:
        document = json.loads(arguments.interchange.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("CHECK=interchange_json_unreadable STATUS=FAIL")
        print("SLASHING_INTERCHANGE=FAIL")
        return 1

    failures: list[str] = []
    if not isinstance(document, dict):
        failures.append("interchange_not_object")
        document = {}
    metadata = document.get("metadata")
    records = document.get("data")
    if not isinstance(metadata, dict):
        failures.append("metadata_invalid")
        metadata = {}
    if metadata.get("interchange_format_version") != "5":
        failures.append("interchange_format_version_not_5")
    observed_root = normalized_hex(metadata.get("genesis_validators_root"), HEX_32)
    if observed_root is None:
        failures.append("genesis_validators_root_invalid")
    elif observed_root != expected_root:
        failures.append("genesis_validators_root_mismatch")
    if not isinstance(records, list) or not records:
        failures.append("validator_data_missing")
        records = []

    parsed_records: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    seen_pubkeys: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            failures.append("validator_record_invalid")
            continue
        pubkey = normalized_hex(record.get("pubkey"), PUBKEY)
        if pubkey is None or pubkey in seen_pubkeys or pubkey != expected_pubkey:
            failures.append("duplicate_or_unexpected_pubkey")
            continue
        seen_pubkeys.add(pubkey)
        blocks = record.get("signed_blocks")
        attestations = record.get("signed_attestations")
        if not isinstance(blocks, list):
            failures.append("signed_blocks_invalid")
            blocks = []
        if not isinstance(attestations, list):
            failures.append("signed_attestations_invalid")
            attestations = []
        parsed_records.append((pubkey, blocks, attestations))

    if seen_pubkeys != {expected_pubkey} or len(records) != 1:
        if "duplicate_or_unexpected_pubkey" not in failures:
            failures.append("duplicate_or_unexpected_pubkey")

    block_slots: list[int] = []
    attestation_targets: list[int] = []
    total_blocks = total_attestations = 0
    for _, blocks, attestations in parsed_records:
        seen_slots: set[int] = set()
        for block in blocks:
            total_blocks += 1
            if not isinstance(block, dict):
                failures.append("signed_blocks_entry_invalid")
                continue
            slot = unsigned_integer(block.get("slot"))
            root = block.get("signing_root")
            if slot is None:
                failures.append("signed_blocks_slot_invalid")
                continue
            if slot in seen_slots:
                failures.append("signed_blocks_duplicate_slot")
                continue
            if root is not None and normalized_hex(root, HEX_32) is None:
                failures.append("signed_blocks_root_invalid")
                continue
            seen_slots.add(slot)
            block_slots.append(slot)

        seen_targets: set[int] = set()
        accepted_attestations: list[tuple[int, int]] = []
        for attestation in attestations:
            total_attestations += 1
            if not isinstance(attestation, dict):
                failures.append("signed_attestations_entry_invalid")
                continue
            source = unsigned_integer(attestation.get("source_epoch"))
            target = unsigned_integer(attestation.get("target_epoch"))
            root = attestation.get("signing_root")
            if source is None or target is None:
                failures.append("signed_attestations_epoch_invalid")
                continue
            if source > target:
                failures.append("signed_attestations_source_after_target")
                continue
            if target in seen_targets:
                failures.append("signed_attestations_duplicate_target")
                continue
            if root is not None and normalized_hex(root, HEX_32) is None:
                failures.append("signed_attestations_root_invalid")
                continue
            seen_targets.add(target)
            accepted_attestations.append((source, target))
            attestation_targets.append(target)
        for source_a, target_a in accepted_attestations:
            for source_b, target_b in accepted_attestations:
                if source_a < source_b < target_b < target_a:
                    failures.append("signed_attestations_surround_vote")
                    break

    if total_blocks == 0 and total_attestations == 0:
        failures.append("signing_history_empty")

    for failure in sorted(set(failures)):
        print(f"CHECK={failure} STATUS=FAIL")
    if failures:
        print("SLASHING_INTERCHANGE=FAIL")
        return 1

    print("INTERCHANGE_FORMAT_VERSION=5")
    print(f"GENESIS_VALIDATORS_ROOT={expected_root}")
    print("VALIDATOR_COUNT=1")
    print(f"EXPECTED_PUBKEY={expected_pubkey}")
    print(f"SIGNED_BLOCK_COUNT={total_blocks}")
    print(f"MAX_SIGNED_BLOCK_SLOT={max(block_slots, default=0)}")
    print(f"SIGNED_ATTESTATION_COUNT={total_attestations}")
    print(f"MAX_SIGNED_ATTESTATION_TARGET_EPOCH={max(attestation_targets, default=0)}")
    print("WARNING=valid_interchange_does_not_prove_freshness_or_global_signer_fence")
    print("SLASHING_INTERCHANGE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
