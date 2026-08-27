#!/usr/bin/env python3
"""Render and verify the Standby Ansible inventory and group variables."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path

INVENTORY_PATH = Path("standby-aws/ansible/inventory/hosts.yml")
GROUP_VARS_PATH = Path("standby-aws/ansible/group_vars/all.yml")
FUTURE_PLACEHOLDER = "REPLACE_WITH_PRIMARY_WG_PUBLIC_KEY"
LABEL = "STANDBY_ANSIBLE_INPUTS"


def fail(reason: str, **extra: object) -> None:
    parts = [f"{LABEL}=FAIL", f"reason={reason}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    print(" ".join(parts), file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--node-ip", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--chain-device", required=True)
    p.add_argument("--validator-device", required=True)
    p.add_argument("--chain-volume-id", required=True)
    p.add_argument("--validator-volume-id", required=True)
    p.add_argument("--admin-cidr", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--fee-recipient", required=True)
    p.add_argument("--primary-ip", required=True)
    p.add_argument("--sns-topic-arn", required=True)
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--host-alias", default="aws-standby-01")
    return p.parse_args()


def group_var_values(a: argparse.Namespace) -> dict[str, str]:
    return {
        "chain_device": json.dumps(a.chain_device),
        "validator_device": json.dumps(a.validator_device),
        "chain_volume_id": json.dumps(a.chain_volume_id),
        "validator_volume_id": json.dumps(a.validator_volume_id),
        "fee_recipient": json.dumps(a.fee_recipient),
        "wg_peer_public_ip": json.dumps(a.primary_ip),
        "admin_ipv4_cidrs": json.dumps([a.admin_cidr], separators=(",", ":")),
        "aws_region": json.dumps(a.region),
        "sns_topic_arn": json.dumps(a.sns_topic_arn),
    }


def render(a: argparse.Namespace) -> None:
    try:
        ipaddress.ip_address(a.node_ip)
    except ValueError:
        fail("node_ip_not_an_ip_address", value=a.node_ip)

    try:
        admin = ipaddress.ip_network(a.admin_cidr, strict=True)
    except ValueError:
        fail("admin_cidr_not_a_network", value=a.admin_cidr)

    if admin.version != 4 or admin.prefixlen != 32:
        fail("admin_cidr_must_be_ipv4_32", value=a.admin_cidr)

    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(
        "---\n"
        "all:\n"
        "  children:\n"
        "    aws_standby:\n"
        "      hosts:\n"
        f"        {a.host_alias}:\n"
        f"          ansible_host: {a.node_ip}\n"
        "          ansible_user: ubuntu\n"
        f"          ansible_ssh_private_key_file: {json.dumps(a.key_file)}\n",
        encoding="utf-8",
    )

    if not GROUP_VARS_PATH.is_file():
        fail("group_vars_missing", path=str(GROUP_VARS_PATH))

    text = GROUP_VARS_PATH.read_text(encoding="utf-8")
    for key, value in group_var_values(a).items():
        text, count = re.subn(
            rf"^{re.escape(key)}:.*$", f"{key}: {value}", text, count=1, flags=re.MULTILINE
        )
        if count != 1:
            fail("key_count", key=key, count=count)
    GROUP_VARS_PATH.write_text(text, encoding="utf-8")


def verify(a: argparse.Namespace) -> None:
    if not INVENTORY_PATH.is_file():
        fail("inventory_missing", path=str(INVENTORY_PATH))
    if not GROUP_VARS_PATH.is_file():
        fail("group_vars_missing", path=str(GROUP_VARS_PATH))

    inventory = INVENTORY_PATH.read_text(encoding="utf-8")
    group_vars = GROUP_VARS_PATH.read_text(encoding="utf-8")

    required = {
        f"          ansible_host: {a.node_ip}",
        f"          ansible_ssh_private_key_file: {json.dumps(a.key_file)}",
    }
    required |= {f"{k}: {v}" for k, v in group_var_values(a).items()}

    present = set((inventory + group_vars).splitlines())
    missing = sorted(line for line in required if line not in present)
    if missing:
        fail("value_readback", missing=json.dumps(missing))

    if "REPLACE_" in inventory:
        fail("inventory_placeholder")

    remaining = re.findall(r"REPLACE_[A-Z0-9_]+", group_vars)
    if remaining != [FUTURE_PLACEHOLDER]:
        fail("unexpected_placeholders", values=json.dumps(remaining))

    print(f"{LABEL}=PASS future_placeholder={FUTURE_PLACEHOLDER}")


def main() -> None:
    a = parse_args()
    if not a.verify_only:
        render(a)
    verify(a)


if __name__ == "__main__":
    main()
