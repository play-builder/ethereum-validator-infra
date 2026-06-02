#!/usr/bin/env python3
"""Validate exact Hoodi receipt, validator identity, and liveness JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys


TX = re.compile(r"^0x[0-9A-Fa-f]{64}$")
ADDRESS = re.compile(r"^0x[0-9A-Fa-f]{40}$")
PUBKEY = re.compile(r"^0x[0-9A-Fa-f]{96}$")
INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
BLOCK = re.compile(r"^0x(?:0|[1-9A-Fa-f][0-9A-Fa-f]*)$")


def die(reason: str) -> "NoReturn":
    raise SystemExit(f"HOODI_EVIDENCE=FAIL reason={reason}")


def payload() -> dict:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        die("invalid_json")
    if not isinstance(value, dict):
        die("json_not_object")
    return value


def receipt(args: argparse.Namespace, value: dict) -> None:
    if not TX.fullmatch(args.tx):
        die("deposit_tx_malformed")
    if not ADDRESS.fullmatch(args.deposit_contract):
        die("deposit_contract_malformed")
    result = value.get("result")
    if not isinstance(result, dict):
        die("receipt_missing")
    transaction_hash = result.get("transactionHash")
    if not isinstance(transaction_hash, str) or not TX.fullmatch(transaction_hash):
        die("receipt_transaction_hash_missing_or_malformed")
    if transaction_hash.lower() != args.tx.lower():
        die("receipt_transaction_hash_mismatch")
    if result.get("status") != "0x1":
        die("receipt_status_not_success")
    destination = result.get("to")
    if not isinstance(destination, str) or destination.lower() != args.deposit_contract.lower():
        die("receipt_contract_mismatch")
    block = result.get("blockNumber")
    if not isinstance(block, str) or not BLOCK.fullmatch(block):
        die("receipt_block_missing_or_malformed")
    print(
        json.dumps(
            {
                "transactionHash": transaction_hash,
                "status": "0x1",
                "to": destination,
                "blockNumber": block,
            },
            separators=(",", ":"),
        )
    )


def validator(args: argparse.Namespace, value: dict) -> None:
    if not PUBKEY.fullmatch(args.pubkey):
        die("pubkey_malformed")
    data = value.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        die("validator_cardinality_not_one")
    record = data[0]
    validator_record = record.get("validator")
    observed_pubkey = validator_record.get("pubkey") if isinstance(validator_record, dict) else None
    if not isinstance(observed_pubkey, str) or observed_pubkey.lower() != args.pubkey.lower():
        die("validator_pubkey_mismatch")
    index = record.get("index")
    if not isinstance(index, str) or not INDEX.fullmatch(index):
        die("validator_index_noncanonical")
    print(index)


def liveness(args: argparse.Namespace, value: dict) -> None:
    if not INDEX.fullmatch(args.index):
        die("expected_index_noncanonical")
    data = value.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        die("liveness_cardinality_not_one")
    if data[0].get("index") != args.index:
        die("liveness_index_mismatch")
    if data[0].get("is_live") is not True:
        die("liveness_not_true")
    print(f"LIVENESS=PASS index={args.index}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    receipt_parser = subparsers.add_parser("receipt")
    receipt_parser.add_argument("--tx", required=True)
    receipt_parser.add_argument("--deposit-contract", required=True)
    validator_parser = subparsers.add_parser("validator")
    validator_parser.add_argument("--pubkey", required=True)
    liveness_parser = subparsers.add_parser("liveness")
    liveness_parser.add_argument("--index", required=True)
    args = parser.parse_args()
    value = payload()
    {"receipt": receipt, "validator": validator, "liveness": liveness}[args.command](args, value)


if __name__ == "__main__":
    main()
