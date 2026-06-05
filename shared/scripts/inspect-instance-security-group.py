#!/usr/bin/env python3
"""Require one attached Security Group, then print all of its inbound rules."""

from __future__ import annotations

import argparse
import json
import re
import subprocess


INSTANCE_RE = re.compile(r"^i-[0-9a-f]{17}$")
SG_RE = re.compile(r"^sg-[0-9a-f]{17}$")


def fail(reason: str) -> "NoReturn":
    raise SystemExit(f"INSTANCE_SECURITY_GROUPS=FAIL reason={reason}")


def run_json(command: list[str], label: str) -> object:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        fail(f"{label}_rc_{result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"{label}_not_json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aws-bin", default="aws")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    if INSTANCE_RE.fullmatch(args.instance_id) is None:
        fail("invalid_instance_id")

    common = ["--profile", args.profile, "--region", args.region, "--output", "json"]
    instance = run_json(
        [args.aws_bin, "ec2", "describe-instances", "--instance-ids", args.instance_id, *common],
        "describe_instances",
    )
    try:
        groups = instance["Reservations"][0]["Instances"][0]["SecurityGroups"]
    except (KeyError, IndexError, TypeError):
        fail("missing_security_groups")
    if not isinstance(groups, list) or len(groups) != 1:
        count = len(groups) if isinstance(groups, list) else -1
        fail(f"expected_one count={count}")
    group_id = groups[0].get("GroupId") if isinstance(groups[0], dict) else None
    if not isinstance(group_id, str) or SG_RE.fullmatch(group_id) is None:
        fail("invalid_security_group_id")

    rules = run_json(
        [
            args.aws_bin,
            "ec2",
            "describe-security-group-rules",
            "--filters",
            f"Name=group-id,Values={group_id}",
            *common,
        ],
        "describe_security_group_rules",
    )
    try:
        inbound = [rule for rule in rules["SecurityGroupRules"] if rule.get("IsEgress") is False]
    except (KeyError, TypeError):
        fail("invalid_security_group_rules")
    inbound.sort(key=lambda rule: (int(rule.get("FromPort", -1)), str(rule.get("IpProtocol", ""))))
    print(f"INSTANCE_SECURITY_GROUPS=PASS count=1 id={group_id}")
    for rule in inbound:
        port = rule.get("FromPort", "all")
        protocol = rule.get("IpProtocol", "unknown")
        cidr = rule.get("CidrIpv4", rule.get("CidrIpv6", "none"))
        print(f"SG_RULE port={port} protocol={protocol} cidr={cidr}")


if __name__ == "__main__":
    main()
