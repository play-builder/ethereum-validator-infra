#!/usr/bin/env python3
"""Validate one tracked CI manifest and atomically render Terraform runtime files."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn


TOP_LEVEL_KEYS = {
    "schema_version",
    "repository",
    "repository_owner_id",
    "repository_id",
    "environment",
    "aws",
    "terraform",
}
AWS_KEYS = {
    "account_id",
    "region",
    "state_key",
    "plan_role_arn",
    "apply_role_arn",
    "kms_break_glass_role_arn",
    "state_bucket",
    "state_kms_key_arn",
    "plan_artifact_bucket",
    "plan_artifact_kms_key_arn",
    "node_permissions_boundary_arn",
}
TERRAFORM_KEYS = {
    "network",
    "region",
    "kms_recovery_region",
    "key_pair_name",
    "admin_cidrs",
    "backup_peer_public_ip",
    "sso_operator_permission_sets",
    "operator_alert_emails",
    "enable_deadman_alarm",
    "enable_staging_bucket",
}
STANDBY_TERRAFORM_KEYS = TERRAFORM_KEYS | {"node_ssh_public_key"}
MODES = {
    "deploy": False,
    "prepare-teardown": True,
    "teardown": True,
}
EXPECTED_ENVIRONMENT = "hoodi-testnet-dev"
EXPECTED_STATE_KEYS = {
    "hoodi-testnet-dev/terraform.tfstate",
    "hoodi-testnet-dev/standby-terraform.tfstate",
}
EXPECTED_PERMISSION_SETS = [
    "testnet_operator_01_builder",
    "testnet_operator_02_approver",
]
GMAIL_ROLE_ALIAS_PATTERN = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9.]*[A-Za-z0-9])?)\+(testnet_op[12])@gmail\.com$"
)
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,38}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}$"
)
NUMERIC_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]$")
ROLE_PATH_PATTERN = r"[A-Za-z0-9+=,.@_/-]{1,512}"
POLICY_PATH_PATTERN = r"[A-Za-z0-9+=,.@_/-]{1,512}"
KMS_KEY_ID_PATTERN = r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|mrk-[0-9a-f]{32})"
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
OUTPUT_FILES = {
    "canonical_manifest": "runtime-inputs.canonical.json",
    "terraform_tfvars": "ci.auto.tfvars.json",
    "backend_hcl": "backend.hcl",
    "control_json": "control.json",
}


class ContractError(RuntimeError):
    """Fail-closed input or publication contract violation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DuplicateKeyError(ValueError):
    """JSON object contains a repeated member name."""


def fail(reason: str) -> NoReturn:
    raise ContractError(reason)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render deterministic Terraform CI inputs from a tracked manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=tuple(MODES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-owner-id", required=True)
    parser.add_argument("--expected-repository-id", required=True)
    parser.add_argument("--expected-environment", required=True)
    return parser.parse_args()


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError
        result[key] = value
    return result


def read_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        fail("manifest_symlink")
    try:
        status = path.stat()
    except OSError:
        fail("manifest_unreadable")
    if not stat.S_ISREG(status.st_mode):
        fail("manifest_not_regular")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except DuplicateKeyError:
        fail("duplicate_json_key")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("manifest_json_invalid")
    if not isinstance(payload, dict):
        fail("schema_top_level")
    return payload


def require_exact_keys(value: Any, expected: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        fail(reason)
    return value


def require_numeric_id(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not NUMERIC_ID_PATTERN.fullmatch(value):
        fail(reason)
    return value


def validate_cli_context(args: argparse.Namespace) -> None:
    if not REPOSITORY_PATTERN.fullmatch(args.expected_repository):
        fail("expected_repository_invalid")
    require_numeric_id(args.expected_owner_id, "expected_owner_id_invalid")
    require_numeric_id(args.expected_repository_id, "expected_repository_id_invalid")
    if args.expected_environment != EXPECTED_ENVIRONMENT:
        fail("expected_environment_invalid")


def valid_bucket_name(value: Any) -> bool:
    if not isinstance(value, str) or not BUCKET_PATTERN.fullmatch(value):
        return False
    if ".." in value or ".-" in value or "-." in value:
        return False
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        pass
    if value.startswith(("xn--", "sthree-", "amzn-s3-demo-")):
        return False
    return not value.endswith(
        ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")
    )


def validate_role_arn(value: Any, account_id: str, reason: str) -> str:
    pattern = re.compile(
        rf"^arn:aws:iam::{re.escape(account_id)}:role/{ROLE_PATH_PATTERN}$"
    )
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail(reason)
    return value


def validate_boundary_arn(value: Any, account_id: str) -> str:
    expected = (
        f"arn:aws:iam::{account_id}:policy/"
        "hoodi-testnet-dev-node-permissions-boundary"
    )
    if value != expected:
        fail("node_permissions_boundary_arn_invalid")
    return value


def validate_kms_arn(value: Any, account_id: str, region: str, reason: str) -> str:
    pattern = re.compile(
        rf"^arn:aws:kms:{re.escape(region)}:{re.escape(account_id)}:key/{KMS_KEY_ID_PATTERN}$"
    )
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail(reason)
    return value


def validate_admin_cidrs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) for item in value
    ):
        fail("admin_cidrs_invalid")
    if len(set(value)) != len(value):
        fail("admin_cidrs_duplicate")
    for cidr in value:
        try:
            network = ipaddress.ip_network(cidr, strict=True)
        except ValueError:
            fail("admin_cidr_invalid")
        if (
            network.version != 4
            or network.prefixlen != 32
            or str(network) != cidr
        ):
            fail("admin_cidr_invalid")
        if not network.network_address.is_global:
            fail("admin_cidr_not_global")
    return value


def validate_backup_peer(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "/" in value:
        fail("backup_peer_public_ip_invalid")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        fail("backup_peer_public_ip_invalid")
    if address.version != 4 or str(address) != value or not address.is_global:
        fail("backup_peer_public_ip_invalid")
    return value


def validate_operator_alert_emails(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) != 2:
        fail("operator_alert_emails_invalid")
    matches = [
        GMAIL_ROLE_ALIAS_PATTERN.fullmatch(item) if isinstance(item, str) else None
        for item in value
    ]
    if (
        matches[0] is None
        or matches[1] is None
        or matches[0].group(1) != matches[1].group(1)
        or matches[0].group(2) != "testnet_op1"
        or matches[1].group(2) != "testnet_op2"
    ):
        fail("operator_alert_emails_invalid")
    return value


def validate_manifest(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    manifest = require_exact_keys(payload, TOP_LEVEL_KEYS, "schema_top_level")
    aws = require_exact_keys(manifest["aws"], AWS_KEYS, "schema_aws")
    expected_terraform_keys = (
        STANDBY_TERRAFORM_KEYS
        if aws.get("state_key") == "hoodi-testnet-dev/standby-terraform.tfstate"
        else TERRAFORM_KEYS
    )
    terraform = require_exact_keys(
        manifest["terraform"], expected_terraform_keys, "schema_terraform"
    )

    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        fail("schema_version_invalid")
    repository = manifest["repository"]
    if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
        fail("repository_invalid")
    if repository != args.expected_repository:
        fail("repository_mismatch")
    owner_id = require_numeric_id(
        manifest["repository_owner_id"], "repository_owner_id_invalid"
    )
    if owner_id != args.expected_owner_id:
        fail("repository_owner_id_mismatch")
    repository_id = require_numeric_id(
        manifest["repository_id"], "repository_id_invalid"
    )
    if repository_id != args.expected_repository_id:
        fail("repository_id_mismatch")
    if manifest["environment"] != EXPECTED_ENVIRONMENT:
        fail("environment_invalid")
    if manifest["environment"] != args.expected_environment:
        fail("environment_mismatch")

    account_id = aws["account_id"]
    if not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        fail("account_id_invalid")
    region = aws["region"]
    if not isinstance(region, str) or not REGION_PATTERN.fullmatch(region):
        fail("region_invalid")
    if aws["state_key"] not in EXPECTED_STATE_KEYS:
        fail("state_key_invalid")
    plan_role_arn = validate_role_arn(
        aws["plan_role_arn"], account_id, "plan_role_arn_invalid"
    )
    apply_role_arn = validate_role_arn(
        aws["apply_role_arn"], account_id, "apply_role_arn_invalid"
    )
    kms_break_glass_role_arn = validate_role_arn(
        aws["kms_break_glass_role_arn"],
        account_id,
        "kms_break_glass_role_arn_invalid",
    )
    expected_break_glass_role_arn = (
        f"arn:aws:iam::{account_id}:role/hoodi-testnet-dev-KmsBreakGlassRole"
    )
    if len({plan_role_arn, apply_role_arn, kms_break_glass_role_arn}) != 3:
        fail("iam_roles_not_distinct")
    if plan_role_arn != (
        f"arn:aws:iam::{account_id}:role/hoodi-testnet-dev-TerraformPlanRole"
    ):
        fail("plan_role_arn_invalid")
    if apply_role_arn != (
        f"arn:aws:iam::{account_id}:role/hoodi-testnet-dev-TerraformApplyRole"
    ):
        fail("apply_role_arn_invalid")
    if kms_break_glass_role_arn != expected_break_glass_role_arn:
        fail("kms_break_glass_role_arn_invalid")
    if not valid_bucket_name(aws["state_bucket"]):
        fail("state_bucket_invalid")
    if not valid_bucket_name(aws["plan_artifact_bucket"]):
        fail("plan_artifact_bucket_invalid")
    if aws["state_bucket"] == aws["plan_artifact_bucket"]:
        fail("buckets_not_distinct")
    validate_kms_arn(
        aws["state_kms_key_arn"], account_id, region, "state_kms_key_arn_invalid"
    )
    validate_kms_arn(
        aws["plan_artifact_kms_key_arn"],
        account_id,
        region,
        "plan_artifact_kms_key_arn_invalid",
    )
    validate_boundary_arn(aws["node_permissions_boundary_arn"], account_id)

    if terraform["network"] != "hoodi":
        fail("network_invalid")
    provider_region = terraform["region"]
    if not isinstance(provider_region, str) or not REGION_PATTERN.fullmatch(provider_region):
        fail("terraform_region_invalid")
    recovery_region = terraform["kms_recovery_region"]
    if (
        not isinstance(recovery_region, str)
        or not REGION_PATTERN.fullmatch(recovery_region)
        or recovery_region == provider_region
    ):
        fail("kms_recovery_region_invalid")
    if terraform["key_pair_name"] != "eth-failover-hoodi":
        fail("key_pair_name_invalid")
    if aws["state_key"] == "hoodi-testnet-dev/standby-terraform.tfstate":
        ssh_public_key = terraform["node_ssh_public_key"]
        if not isinstance(ssh_public_key, str) or not re.fullmatch(
            r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]{1,255})?", ssh_public_key
        ):
            fail("node_ssh_public_key_invalid")
    validate_admin_cidrs(terraform["admin_cidrs"])
    validate_backup_peer(terraform["backup_peer_public_ip"])
    if terraform["sso_operator_permission_sets"] != EXPECTED_PERMISSION_SETS:
        fail("sso_operator_permission_sets_invalid")
    validate_operator_alert_emails(terraform["operator_alert_emails"])
    for key in ("enable_deadman_alarm", "enable_staging_bucket"):
        if type(terraform[key]) is not bool:
            fail(f"{key}_invalid")
    return manifest


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def render_tfvars(manifest: dict[str, Any], mode: str) -> dict[str, Any]:
    aws = manifest["aws"]
    terraform = manifest["terraform"]
    rendered = {
        "network": terraform["network"],
        "node_permissions_boundary_arn": aws["node_permissions_boundary_arn"],
        "terraform_plan_role_arn": aws["plan_role_arn"],
        "terraform_apply_role_arn": aws["apply_role_arn"],
        "kms_break_glass_role_arn": aws["kms_break_glass_role_arn"],
        "allow_protected_destroy": MODES[mode],
        "region": terraform["region"],
        "kms_recovery_region": terraform["kms_recovery_region"],
        "key_pair_name": terraform["key_pair_name"],
        "admin_cidrs": terraform["admin_cidrs"],
        "backup_peer_public_ip": terraform["backup_peer_public_ip"],
        "sso_operator_permission_sets": terraform["sso_operator_permission_sets"],
        "operator_alert_emails": terraform["operator_alert_emails"],
        "enable_deadman_alarm": terraform["enable_deadman_alarm"],
        "enable_staging_bucket": terraform["enable_staging_bucket"],
    }
    if "node_ssh_public_key" in terraform:
        rendered["node_ssh_public_key"] = terraform["node_ssh_public_key"]
    return rendered


def render_backend(manifest: dict[str, Any]) -> bytes:
    aws = manifest["aws"]
    return (
        f'bucket       = "{aws["state_bucket"]}"\n'
        f'key          = "{aws["state_key"]}"\n'
        f'region       = "{aws["region"]}"\n'
        "encrypt      = true\n"
        f'kms_key_id   = "{aws["state_kms_key_arn"]}"\n'
        "use_lockfile = true\n"
    ).encode("utf-8")


def validate_publication_paths(output_dir: Path) -> dict[str, Path]:
    if output_dir.is_symlink():
        fail("output_dir_symlink")
    if output_dir.exists() and not output_dir.is_dir():
        fail("output_dir_not_directory")
    paths = {name: output_dir / filename for name, filename in OUTPUT_FILES.items()}
    for path in paths.values():
        if path.is_symlink():
            fail("output_symlink")
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            fail("output_not_regular")
    return paths


def atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def publish(manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    paths = validate_publication_paths(args.output_dir)
    canonical_manifest = canonical_json(manifest)
    manifest_sha = sha256_bytes(canonical_manifest)
    tfvars = canonical_json(render_tfvars(manifest, args.mode))
    backend = render_backend(manifest)
    canonical_path = paths["canonical_manifest"].resolve()
    aws = manifest["aws"]
    terraform = manifest["terraform"]
    control = canonical_json(
        {
            "schema_version": 1,
            "mode": args.mode,
            "repository": manifest["repository"],
            "repository_owner_id": manifest["repository_owner_id"],
            "repository_id": manifest["repository_id"],
            "environment": manifest["environment"],
            "aws_account_id": aws["account_id"],
            "aws_region": aws["region"],
            "terraform_region": terraform["region"],
            "plan_role_arn": aws["plan_role_arn"],
            "apply_role_arn": aws["apply_role_arn"],
            "kms_break_glass_role_arn": aws["kms_break_glass_role_arn"],
            "state_bucket": aws["state_bucket"],
            "state_key": aws["state_key"],
            "state_kms_key_arn": aws["state_kms_key_arn"],
            "plan_artifact_bucket": aws["plan_artifact_bucket"],
            "plan_artifact_kms_key_arn": aws["plan_artifact_kms_key_arn"],
            "node_permissions_boundary_arn": aws["node_permissions_boundary_arn"],
            "allow_protected_destroy": MODES[args.mode],
            "canonical_manifest_path": str(canonical_path),
            "runtime_manifest_sha256": manifest_sha,
        }
    )
    artifacts = {
        "canonical_manifest": canonical_manifest,
        "terraform_tfvars": tfvars,
        "backend_hcl": backend,
        "control_json": control,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, content in artifacts.items():
        atomic_write(paths[name], content)
    return {
        "status": "PASS",
        "mode": args.mode,
        "artifacts": {
            name: {
                "path": str(paths[name].resolve()),
                "sha256": sha256_bytes(content),
            }
            for name, content in artifacts.items()
        },
    }


def main() -> int:
    args = parse_args()
    try:
        validate_cli_context(args)
        manifest = validate_manifest(read_manifest(args.manifest), args)
        result = publish(manifest, args)
    except ContractError as error:
        print(f"CI_RUNTIME_RENDER=FAIL reason={error.reason}", file=sys.stderr)
        return 1
    except OSError:
        print("CI_RUNTIME_RENDER=FAIL reason=filesystem_error", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
