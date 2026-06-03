#!/usr/bin/env python3
"""Discover the deployed AWS runtime identity and atomically update lab.env."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


REQUIRED_TAGS = {
    "Project": "eth-failover",
    "Network": "hoodi",
    "Role": "primary",
    "Managed": "terraform",
}
INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{17}$")
ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
NETWORK_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
EXPORT_PATTERN = re.compile(
    r"^export (NODE_IP|INSTANCE_ID|TOPIC_ARN|PARAM_PREFIX)=.*$"
)


class ContractError(RuntimeError):
    """A fail-closed runtime discovery contract violation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fail(reason: str) -> NoReturn:
    raise ContractError(reason)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read exact tagged AWS runtime resources and update lab.env."
    )
    parser.add_argument("--aws-bin", default="aws")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--lab-env", type=Path, required=True)
    return parser.parse_args()


def validate_arguments(args: argparse.Namespace) -> None:
    if not ACCOUNT_ID_PATTERN.fullmatch(args.expected_account_id):
        fail("expected_account_id_invalid")
    if not REGION_PATTERN.fullmatch(args.region):
        fail("region_invalid")
    if not NETWORK_PATTERN.fullmatch(args.network):
        fail("network_invalid")
    if args.network != REQUIRED_TAGS["Network"]:
        fail("network_mismatch")
    if not args.profile or any(char.isspace() for char in args.profile):
        fail("profile_invalid")


def validate_destination(path: Path) -> None:
    if path.is_symlink():
        fail("lab_env_symlink")
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        fail("lab_env_not_regular")
    if not path.parent.exists() or not path.parent.is_dir():
        fail("lab_env_parent_missing")


def aws_json(args: argparse.Namespace, service: str, operation: str, *extra: str) -> dict[str, Any]:
    command = [
        args.aws_bin,
        service,
        operation,
        *extra,
        "--profile",
        args.profile,
        "--region",
        args.region,
        "--output",
        "json",
        "--no-cli-pager",
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        fail(f"aws_cli_{service}_{operation}_exec")
    if result.returncode != 0:
        fail(f"aws_cli_{service}_{operation}_rc_{result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        fail(f"aws_cli_{service}_{operation}_json")
    if not isinstance(payload, dict):
        fail(f"aws_cli_{service}_{operation}_shape")
    return payload


def tags_to_dict(tags: Any, reason: str) -> dict[str, str]:
    if not isinstance(tags, list):
        fail(reason)
    result: dict[str, str] = {}
    for item in tags:
        if not isinstance(item, dict):
            fail(reason)
        key = item.get("Key")
        value = item.get("Value")
        if not isinstance(key, str) or not isinstance(value, str) or key in result:
            fail(reason)
        result[key] = value
    return result


def require_tags(tags: Any, reason: str) -> None:
    actual = tags_to_dict(tags, reason)
    if any(actual.get(key) != value for key, value in REQUIRED_TAGS.items()):
        fail(reason)


def tag_filters() -> tuple[str, ...]:
    return tuple(
        f"Name=tag:{key},Values={value}" for key, value in REQUIRED_TAGS.items()
    )


def discover(args: argparse.Namespace) -> dict[str, str]:
    identity = aws_json(args, "sts", "get-caller-identity")
    if identity.get("Account") != args.expected_account_id:
        fail("account_mismatch")
    caller_arn = identity.get("Arn")
    assumed_role = re.compile(
        rf"^arn:(?:aws|aws-us-gov|aws-cn):sts::{re.escape(args.expected_account_id)}:assumed-role/[^/]+/[^/]+$"
    )
    if not isinstance(caller_arn, str) or not assumed_role.fullmatch(caller_arn):
        fail("caller_identity_not_assumed_role")

    instances_payload = aws_json(
        args,
        "ec2",
        "describe-instances",
        "--filters",
        *tag_filters(),
    )
    reservations = instances_payload.get("Reservations")
    if not isinstance(reservations, list):
        fail("instance_response_shape")
    instances: list[dict[str, Any]] = []
    for reservation in reservations:
        if not isinstance(reservation, dict) or not isinstance(
            reservation.get("Instances"), list
        ):
            fail("instance_response_shape")
        instances.extend(reservation["Instances"])
    instances = [
        instance
        for instance in instances
        if isinstance(instance, dict)
        and instance.get("State", {}).get("Name") != "terminated"
    ]
    if len(instances) != 1:
        fail("instance_cardinality")
    instance = instances[0]
    instance_id = instance.get("InstanceId")
    if not isinstance(instance_id, str) or not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        fail("instance_id_invalid")
    state_name = instance.get("State", {}).get("Name")
    if state_name not in {"pending", "running", "stopping", "stopped"}:
        fail("instance_state_invalid")
    availability_zone = instance.get("Placement", {}).get("AvailabilityZone")
    if not isinstance(availability_zone, str) or not availability_zone.startswith(
        args.region
    ):
        fail("instance_region_mismatch")
    require_tags(instance.get("Tags"), "instance_tags_mismatch")

    addresses_payload = aws_json(
        args,
        "ec2",
        "describe-addresses",
        "--filters",
        *tag_filters(),
    )
    addresses = addresses_payload.get("Addresses")
    if not isinstance(addresses, list) or len(addresses) != 1:
        fail("address_cardinality")
    address = addresses[0]
    if not isinstance(address, dict):
        fail("address_response_shape")
    if address.get("Domain") != "vpc" or address.get("InstanceId") != instance_id:
        fail("address_instance_mismatch")
    require_tags(address.get("Tags"), "address_tags_mismatch")
    node_ip = address.get("PublicIp")
    try:
        parsed_ip = ipaddress.ip_address(node_ip)
    except (ValueError, TypeError):
        fail("node_ip_not_global_ipv4")
    if parsed_ip.version != 4 or not parsed_ip.is_global:
        fail("node_ip_not_global_ipv4")

    expected_topic_arn = (
        f"arn:aws:sns:{args.region}:{args.expected_account_id}:"
        f"eth-failover-{args.network}-alerts"
    )
    topics_payload = aws_json(args, "sns", "list-topics")
    topics = topics_payload.get("Topics")
    if not isinstance(topics, list):
        fail("sns_topic_response_shape")
    matching_topics = [
        topic
        for topic in topics
        if isinstance(topic, dict) and topic.get("TopicArn") == expected_topic_arn
    ]
    if len(matching_topics) != 1:
        fail("sns_topic_cardinality")
    topic_arn = matching_topics[0]["TopicArn"]
    topic_tags = aws_json(
        args,
        "sns",
        "list-tags-for-resource",
        "--resource-arn",
        topic_arn,
    )
    require_tags(topic_tags.get("Tags"), "sns_topic_tags_mismatch")

    parameter_prefix = f"/eth-staking/{args.network}"
    expected_parameter_names = {
        f"{parameter_prefix}/checkpoint_sync_url",
        f"{parameter_prefix}/fee_recipient",
        f"{parameter_prefix}/graffiti",
    }
    parameter_filters = tuple(
        f"Key=tag:{key},Option=Equals,Values={value}"
        for key, value in REQUIRED_TAGS.items()
    )
    parameters_payload = aws_json(
        args,
        "ssm",
        "describe-parameters",
        "--parameter-filters",
        *parameter_filters,
    )
    parameters = parameters_payload.get("Parameters")
    if not isinstance(parameters, list):
        fail("ssm_parameter_response_shape")
    actual_parameter_names = {
        parameter.get("Name")
        for parameter in parameters
        if isinstance(parameter, dict) and isinstance(parameter.get("Name"), str)
    }
    if len(parameters) != 3 or actual_parameter_names != expected_parameter_names:
        fail("ssm_parameter_set_mismatch")
    parameter_arn_prefix = (
        f"arn:aws:ssm:{args.region}:{args.expected_account_id}:parameter"
    )
    for parameter in parameters:
        name = parameter["Name"]
        if parameter.get("ARN") != f"{parameter_arn_prefix}{name}":
            fail("ssm_parameter_identity_mismatch")

    return {
        "NODE_IP": str(parsed_ip),
        "INSTANCE_ID": instance_id,
        "TOPIC_ARN": topic_arn,
        "PARAM_PREFIX": parameter_prefix,
    }


def render_updated_env(original: str, values: dict[str, str]) -> str:
    output: list[str] = []
    written: set[str] = set()
    for line in original.splitlines():
        match = EXPORT_PATTERN.fullmatch(line)
        if match:
            name = match.group(1)
            if name not in written:
                output.append(f"export {name}={shlex.quote(values[name])}")
                written.add(name)
            continue
        output.append(line)
    for name, value in values.items():
        if name not in written:
            output.append(f"export {name}={shlex.quote(value)}")
    return "\n".join(output) + "\n"


def atomic_replace(path: Path, content: str) -> None:
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, existing_mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    try:
        args = parse_args()
        validate_arguments(args)
        validate_destination(args.lab_env)
        values = discover(args)
        original = (
            args.lab_env.read_text(encoding="utf-8") if args.lab_env.exists() else ""
        )
        atomic_replace(args.lab_env, render_updated_env(original, values))
    except (ContractError, UnicodeDecodeError) as error:
        reason = error.reason if isinstance(error, ContractError) else "lab_env_utf8"
        print(f"AWS_RUNTIME_SYNC=FAIL reason={reason}", file=sys.stderr)
        return 1
    for name in values:
        print(f"LAB_ENV_UPSERT=OK name={name}")
    print(f"AWS_RUNTIME_SYNC=PASS count={len(values)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
