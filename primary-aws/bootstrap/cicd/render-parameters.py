#!/usr/bin/env python3
"""Render or verify the exact CloudFormation bootstrap parameter evidence.

This helper is deliberately offline.  ``render`` binds the canonical template to
explicit, trusted incident/change context before the stack exists.  ``check``
later binds the unchanged parameter file to the runtime manifest populated from
the stack outputs.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_PARAMETER_KEYS = (
    "GitHubRepository",
    "GitHubRepositoryOwnerId",
    "GitHubRepositoryId",
    "GitHubEnvironment",
    "GitHubTeardownEnvironment",
    "ExistingGithubOidcProviderArn",
    "TerraformPlanRoleName",
    "TerraformApplyRoleName",
    "NodeRoleName",
    "NodeInstanceProfileName",
    "NodeSshKeyPairName",
    "NodeSshPublicKey",
    "NodePermissionsBoundaryName",
    "StateBucketMode",
    "StateBucketName",
    "ExistingStateBucketName",
    "ExistingStateKmsKeyArn",
    "StateKey",
    "PlanArtifactBucketName",
    "CloudTrailTrailArn",
)
ENVIRONMENT = "hoodi-testnet-dev"
TEARDOWN_ENVIRONMENT = "hoodi-testnet-dev-teardown"
STATE_KEY = "hoodi-testnet-dev/terraform.tfstate"
PLACEHOLDER_PATTERN = re.compile(
    r"(?:REPLACE(?:_WITH)?|YOUR_(?:GITHUB|AWS)|CHANGEME|EXAMPLE)", re.IGNORECASE
)
ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NUMERIC_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class ParameterEvidenceError(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def fail(reason: str, detail: str) -> None:
    raise ParameterEvidenceError(reason, detail)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render/check exact bootstrap CloudFormation parameters offline."
    )
    parser.add_argument("--mode", required=True, choices=("render", "check"))
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-owner-id", required=True)
    parser.add_argument("--expected-repository-id", required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--expected-region", required=True)
    parser.add_argument("--state-bucket-mode", choices=("CREATE", "EXISTING"))
    parser.add_argument("--state-bucket-name")
    parser.add_argument("--plan-artifact-bucket-name")
    parser.add_argument("--node-ssh-public-key-file", type=Path)
    parser.add_argument("--existing-state-kms-key-arn", default="")
    parser.add_argument("--existing-github-oidc-provider-arn", default="")
    parser.add_argument("--cloudtrail-trail-arn", default="")
    parser.add_argument("--runtime-manifest", type=Path)
    return parser.parse_args()


def duplicate_object_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate_json_key", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def read_regular_bytes(path: Path, reason: str, label: str) -> bytes:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        fail(reason, f"{label} does not exist: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        fail(reason, f"{label} must be a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        fail(reason, f"cannot read {label}: {error}")


def read_regular_json(path: Path, reason: str, label: str) -> Any:
    raw = read_regular_bytes(path, reason, label)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_object_guard)
    except ParameterEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(reason, f"{label} must be UTF-8 JSON: {error}")


def validate_context(args: argparse.Namespace) -> None:
    if not REPOSITORY_PATTERN.fullmatch(args.expected_repository):
        fail("repository_format", "expected repository must be owner/name")
    if not NUMERIC_ID_PATTERN.fullmatch(args.expected_owner_id):
        fail("owner_id_format", "expected owner ID must be a positive decimal ID")
    if not NUMERIC_ID_PATTERN.fullmatch(args.expected_repository_id):
        fail("repository_id_format", "expected repository ID must be a positive decimal ID")
    if not ACCOUNT_PATTERN.fullmatch(args.expected_account_id):
        fail("account_id_format", "expected account ID must be exactly 12 digits")
    if not REGION_PATTERN.fullmatch(args.expected_region):
        fail("region_format", "expected region is not canonical")


def validate_template(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("Parameters"), dict):
        fail("template_parameters", "template must contain a Parameters object")
    keys = tuple(document["Parameters"])
    if set(keys) != set(EXPECTED_PARAMETER_KEYS) or len(keys) != len(EXPECTED_PARAMETER_KEYS):
        fail("template_parameter_key_set", "canonical template parameter set changed")
    return document


def template_defaults(template: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, schema in template["Parameters"].items():
        if not isinstance(schema, dict):
            fail("template_parameters", f"parameter schema is not an object: {key}")
        if "Default" in schema:
            default = schema["Default"]
            if not isinstance(default, str):
                fail("template_parameters", f"parameter default is not a string: {key}")
            values[key] = default
    return values


def validate_bucket(value: str, reason: str, account_id: str) -> str:
    if (
        not BUCKET_PATTERN.fullmatch(value)
        or ".." in value
        or ".-" in value
        or "-." in value
        or not value.endswith("-" + account_id)
    ):
        fail(reason, f"bucket must be canonical and end in -{account_id}: {value!r}")
    return value


def validate_optional_oidc_provider(value: str, account_id: str) -> None:
    expected = (
        f"arn:aws:iam::{account_id}:oidc-provider/"
        "token.actions.githubusercontent.com"
    )
    if value not in {"", expected}:
        fail("oidc_provider_arn", "OIDC provider ARN must be empty or the same-account GitHub provider")


def validate_existing_kms_arn(value: str, account_id: str, region: str) -> None:
    prefix = f"arn:aws:kms:{region}:{account_id}:key/"
    key = value.removeprefix(prefix)
    if not value.startswith(prefix) or not re.fullmatch(
        r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|mrk-[0-9a-f]{32})",
        key,
    ):
        fail("existing_state_kms_key_arn", "existing state KMS key must be a same-account, same-region key ARN")


def validate_public_key_text(text: str) -> str:
    if "PRIVATE KEY" in text:
        fail("private_key_forbidden", "private-key material is forbidden")
    lines = text.splitlines()
    if len(lines) != 1:
        fail("public_key_single_line", "public key must contain exactly one line")
    line = lines[0]
    if PLACEHOLDER_PATTERN.search(line):
        fail("public_key_format", "public key contains a placeholder")
    parts = line.split()
    if len(parts) not in {2, 3} or parts[0] != "ssh-ed25519":
        fail("public_key_format", "public key must be one ssh-ed25519 OpenSSH line")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        if len(blob) < 8:
            raise ValueError
        algorithm_length = struct.unpack(">I", blob[:4])[0]
        offset = 4 + algorithm_length
        algorithm = blob[4:offset]
        key_length = struct.unpack(">I", blob[offset : offset + 4])[0]
        key = blob[offset + 4 :]
        if algorithm != b"ssh-ed25519" or key_length != 32 or len(key) != 32:
            raise ValueError
    except (binascii.Error, ValueError, struct.error):
        fail("public_key_blob", "public key has an invalid Ed25519 OpenSSH blob")
    return line


def parse_public_key(path: Path) -> str:
    raw = read_regular_bytes(path, "public_key_not_regular", "public key")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        fail("public_key_format", "public key must be UTF-8")
    return validate_public_key_text(text)


def validate_no_sensitive_or_placeholder(values: dict[str, str]) -> None:
    for key, value in values.items():
        if "PRIVATE KEY" in value:
            fail("private_key_forbidden", f"private-key material in {key}")
        if PLACEHOLDER_PATTERN.search(value):
            fail("placeholder_value", f"placeholder value in {key}")


def render_values(args: argparse.Namespace, template: dict[str, Any]) -> dict[str, str]:
    required = {
        "state_bucket_mode": args.state_bucket_mode,
        "state_bucket_name": args.state_bucket_name,
        "plan_artifact_bucket_name": args.plan_artifact_bucket_name,
        "node_ssh_public_key_file": args.node_ssh_public_key_file,
    }
    for name, value in required.items():
        if value is None:
            fail("render_argument", f"--{name.replace('_', '-')} is required in render mode")
    assert args.state_bucket_mode is not None
    assert args.state_bucket_name is not None
    assert args.plan_artifact_bucket_name is not None
    assert args.node_ssh_public_key_file is not None
    values = template_defaults(template)
    values.update(
        {
            "GitHubRepository": args.expected_repository,
            "GitHubRepositoryOwnerId": args.expected_owner_id,
            "GitHubRepositoryId": args.expected_repository_id,
            "GitHubEnvironment": ENVIRONMENT,
            "GitHubTeardownEnvironment": TEARDOWN_ENVIRONMENT,
            "ExistingGithubOidcProviderArn": args.existing_github_oidc_provider_arn,
            "NodeSshPublicKey": parse_public_key(args.node_ssh_public_key_file),
            "StateBucketMode": args.state_bucket_mode,
            "PlanArtifactBucketName": validate_bucket(
                args.plan_artifact_bucket_name, "plan_bucket_format", args.expected_account_id
            ),
            "CloudTrailTrailArn": args.cloudtrail_trail_arn,
        }
    )
    validate_optional_oidc_provider(
        values["ExistingGithubOidcProviderArn"], args.expected_account_id
    )
    if args.state_bucket_mode == "CREATE":
        if args.existing_state_kms_key_arn:
            fail("create_existing_state", "CREATE mode forbids an existing state KMS ARN")
        values.update(
            {
                "StateBucketName": validate_bucket(
                    args.state_bucket_name, "state_bucket_format", args.expected_account_id
                ),
                "ExistingStateBucketName": "",
                "ExistingStateKmsKeyArn": "",
            }
        )
    else:
        if not args.existing_state_kms_key_arn:
            fail("existing_state_kms_key_arn", "EXISTING mode requires --existing-state-kms-key-arn")
        validate_existing_kms_arn(
            args.existing_state_kms_key_arn,
            args.expected_account_id,
            args.expected_region,
        )
        values.update(
            {
                "StateBucketName": "",
                "ExistingStateBucketName": validate_bucket(
                    args.state_bucket_name, "state_bucket_format", args.expected_account_id
                ),
                "ExistingStateKmsKeyArn": args.existing_state_kms_key_arn,
            }
        )
    if set(values) != set(EXPECTED_PARAMETER_KEYS):
        fail("parameter_key_set", "rendered values do not match the full parameter set")
    validate_no_sensitive_or_placeholder(values)
    return values


def parse_parameter_file(document: Any) -> dict[str, str]:
    if not isinstance(document, list):
        fail("parameter_key_set", "parameters must be a JSON array")
    values: dict[str, str] = {}
    for index, item in enumerate(document):
        if not isinstance(item, dict) or set(item) != {"ParameterKey", "ParameterValue"}:
            fail("parameter_shape", f"invalid parameter entry at index {index}")
        key, value = item["ParameterKey"], item["ParameterValue"]
        if not isinstance(key, str) or not isinstance(value, str):
            fail("parameter_shape", f"parameter key/value must be strings at index {index}")
        if key in values:
            fail("duplicate_parameter", f"duplicate ParameterKey: {key}")
        values[key] = value
    if set(values) != set(EXPECTED_PARAMETER_KEYS) or len(values) != len(EXPECTED_PARAMETER_KEYS):
        fail("parameter_key_set", "parameters must contain the exact full parameter set")
    validate_no_sensitive_or_placeholder(values)
    return values


def nested(document: Any, path: tuple[str, ...], reason: str) -> str:
    current = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            fail(reason, f"runtime manifest is missing {'.'.join(path)}")
        current = current[key]
    if not isinstance(current, str):
        fail(reason, f"runtime manifest {'.'.join(path)} must be a string")
    return current


def compare(label: str, actual: str, expected: str, reason: str) -> None:
    if actual != expected:
        fail(reason, f"{label} mismatch")


def check_cross_binding(
    values: dict[str, str], manifest: Any, args: argparse.Namespace
) -> None:
    if not isinstance(manifest, dict):
        fail("runtime_manifest", "runtime manifest must be an object")
    checks = (
        ("repository", values["GitHubRepository"], nested(manifest, ("repository",), "repository_mismatch"), args.expected_repository, "repository_mismatch"),
        ("owner ID", values["GitHubRepositoryOwnerId"], nested(manifest, ("repository_owner_id",), "owner_id_mismatch"), args.expected_owner_id, "owner_id_mismatch"),
        ("repository ID", values["GitHubRepositoryId"], nested(manifest, ("repository_id",), "repository_id_mismatch"), args.expected_repository_id, "repository_id_mismatch"),
        ("environment", values["GitHubEnvironment"], nested(manifest, ("environment",), "environment_mismatch"), ENVIRONMENT, "environment_mismatch"),
        ("account ID", args.expected_account_id, nested(manifest, ("aws", "account_id"), "account_id_mismatch"), args.expected_account_id, "account_id_mismatch"),
        ("region", args.expected_region, nested(manifest, ("aws", "region"), "region_mismatch"), args.expected_region, "region_mismatch"),
        ("state key", values["StateKey"], nested(manifest, ("aws", "state_key"), "state_key_mismatch"), STATE_KEY, "state_key_mismatch"),
        ("plan bucket", values["PlanArtifactBucketName"], nested(manifest, ("aws", "plan_artifact_bucket"), "plan_bucket_mismatch"), values["PlanArtifactBucketName"], "plan_bucket_mismatch"),
        ("key pair", values["NodeSshKeyPairName"], nested(manifest, ("terraform", "key_pair_name"), "key_pair_mismatch"), values["NodeSshKeyPairName"], "key_pair_mismatch"),
    )
    for label, parameter_value, manifest_value, explicit_value, reason in checks:
        if parameter_value != manifest_value or parameter_value != explicit_value:
            fail(reason, f"{label} does not bind parameters, manifest, and trusted context")
    if values["GitHubTeardownEnvironment"] != TEARDOWN_ENVIRONMENT:
        fail("teardown_environment_mismatch", "teardown environment changed")
    state_bucket = (
        values["StateBucketName"]
        if values["StateBucketMode"] == "CREATE"
        else values["ExistingStateBucketName"]
    )
    compare("state bucket", state_bucket, nested(manifest, ("aws", "state_bucket"), "state_bucket_mismatch"), "state_bucket_mismatch")
    validate_bucket(state_bucket, "state_bucket_mismatch", args.expected_account_id)
    validate_bucket(
        values["PlanArtifactBucketName"],
        "plan_bucket_mismatch",
        args.expected_account_id,
    )
    role_checks = (
        ("TerraformPlanRoleName", "plan_role_arn", "role", "plan_role_mismatch"),
        ("TerraformApplyRoleName", "apply_role_arn", "role", "apply_role_mismatch"),
        ("NodePermissionsBoundaryName", "node_permissions_boundary_arn", "policy", "node_boundary_mismatch"),
    )
    for parameter_key, manifest_key, resource_type, reason in role_checks:
        arn = nested(manifest, ("aws", manifest_key), reason)
        expected_arn = (
            f"arn:aws:iam::{args.expected_account_id}:{resource_type}/"
            f"{values[parameter_key]}"
        )
        if arn != expected_arn:
            fail(reason, f"{manifest_key} basename does not match {parameter_key}")
    validate_public_key_text(values["NodeSshPublicKey"])
    validate_optional_oidc_provider(values["ExistingGithubOidcProviderArn"], args.expected_account_id)
    if values["StateBucketMode"] == "CREATE":
        if values["ExistingStateBucketName"] or values["ExistingStateKmsKeyArn"]:
            fail("create_existing_state", "CREATE mode existing-state fields must be empty")
    elif values["StateBucketMode"] == "EXISTING":
        if values["StateBucketName"]:
            fail("existing_state_bucket", "EXISTING mode StateBucketName must be empty")
        validate_existing_kms_arn(values["ExistingStateKmsKeyArn"], args.expected_account_id, args.expected_region)
        compare(
            "existing state KMS key ARN",
            values["ExistingStateKmsKeyArn"],
            nested(manifest, ("aws", "state_kms_key_arn"), "existing_state_kms_key_mismatch"),
            "existing_state_kms_key_mismatch",
        )
    else:
        fail("state_bucket_mode", "StateBucketMode must be CREATE or EXISTING")


def validate_output(path: Path, input_paths: set[Path]) -> Path:
    absolute = path.absolute()
    if absolute in input_paths:
        fail("output_overwrites_input", "parameters output must not overwrite an input")
    try:
        parent_metadata = os.lstat(absolute.parent)
    except FileNotFoundError:
        fail("output_parent", f"output parent does not exist: {absolute.parent}")
    if not stat.S_ISDIR(parent_metadata.st_mode):
        fail("output_parent", "output parent must be a real directory")
    try:
        metadata = os.lstat(absolute)
    except FileNotFoundError:
        return absolute
    if not stat.S_ISREG(metadata.st_mode):
        fail("output_not_regular", "parameters output must be a regular file")
    return absolute


def atomic_write(path: Path, raw: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    try:
        args = parse_args()
        validate_context(args)
        template_path = args.template.absolute()
        template = validate_template(
            read_regular_json(template_path, "template_not_regular", "canonical template")
        )
        parameters_path = args.parameters.absolute()
        if args.mode == "render":
            if args.runtime_manifest is not None:
                fail("render_argument", "render mode must not depend on a runtime manifest")
            values = render_values(args, template)
            inputs = {template_path, args.node_ssh_public_key_file.absolute()}
            output = validate_output(parameters_path, inputs)
            raw = (
                json.dumps(
                    [
                        {"ParameterKey": key, "ParameterValue": values[key]}
                        for key in EXPECTED_PARAMETER_KEYS
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            atomic_write(output, raw)
        else:
            if args.runtime_manifest is None:
                fail("runtime_manifest", "--runtime-manifest is required in check mode")
            raw = read_regular_bytes(parameters_path, "parameters_not_regular", "parameters")
            try:
                document = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_object_guard)
            except ParameterEvidenceError:
                raise
            except (UnicodeError, json.JSONDecodeError) as error:
                fail("parameters_json", f"parameters must be UTF-8 JSON: {error}")
            values = parse_parameter_file(document)
            manifest = read_regular_json(
                args.runtime_manifest.absolute(), "runtime_manifest_not_regular", "runtime manifest"
            )
            check_cross_binding(values, manifest, args)
        digest = hashlib.sha256(raw).hexdigest()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "mode": args.mode,
                    "parameter_count": len(EXPECTED_PARAMETER_KEYS),
                    "sha256": digest,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except ParameterEvidenceError as error:
        print(
            f"BOOTSTRAP_PARAMETERS=FAIL reason={error.reason} detail={error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
