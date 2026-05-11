#!/usr/bin/env python3
"""Render deterministic, inert client-release snapshots for both sites."""

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile


sys.dont_write_bytecode = True

VALIDATOR_PATH = Path(__file__).with_name("validate-client-release-lock.py")
_specification = importlib.util.spec_from_file_location("client_release_lock_validator", VALIDATOR_PATH)
_validator = importlib.util.module_from_spec(_specification)
_specification.loader.exec_module(_validator)
ClientReleaseError = _validator.ClientReleaseError
canonical_json = _validator.canonical_json
load_and_validate_lock = _validator.load_and_validate_lock


def require_regular_output(path):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as error:
        raise ClientReleaseError("OUTPUT_IO_FAILED") from error
    if metadata is not None and (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)):
        raise ClientReleaseError("OUTPUT_NOT_REGULAR")
    try:
        invalid_parent = not path.parent.is_dir() or path.parent.is_symlink()
    except OSError as error:
        raise ClientReleaseError("OUTPUT_IO_FAILED") from error
    if invalid_parent:
        raise ClientReleaseError("OUTPUT_PARENT_INVALID")
    return path


def read_prior_output(path):
    try:
        return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ClientReleaseError("OUTPUT_READ_FAILED") from error


def prepare_atomic_temp(path, content, mode=0o644):
    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        output_mode = "wb" if isinstance(content, bytes) else "w"
        output_options = {} if output_mode == "wb" else {"encoding": "utf-8", "newline": "\n"}
        with os.fdopen(descriptor, output_mode, **output_options) as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, mode)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary_path is not None:
                os.unlink(temporary_path)
        except OSError:
            pass
        raise ClientReleaseError("OUTPUT_WRITE_FAILED") from error
    return Path(temporary_path)


def remove_if_present(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ClientReleaseError("OUTPUT_ROLLBACK_FAILED") from error


def restore_prior_output(path, prior_output):
    if prior_output is None:
        remove_if_present(path)
        return
    content, mode = prior_output
    temporary_path = prepare_atomic_temp(path, content, mode)
    try:
        os.replace(temporary_path, path)
    except OSError as error:
        remove_if_present(temporary_path)
        raise ClientReleaseError("OUTPUT_ROLLBACK_FAILED") from error


def commit_pair(primary_output, standby_output, primary_temp, standby_temp, primary_prior):
    try:
        os.replace(primary_temp, primary_output)
        primary_temp = None
        os.replace(standby_temp, standby_output)
        standby_temp = None
    except OSError as error:
        if primary_temp is None:
            try:
                restore_prior_output(primary_output, primary_prior)
            except ClientReleaseError as rollback_error:
                raise rollback_error from error
        raise ClientReleaseError("OUTPUT_WRITE_FAILED") from error
    finally:
        if primary_temp is not None:
            remove_if_present(primary_temp)
        if standby_temp is not None:
            remove_if_present(standby_temp)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--primary-output", required=True)
    parser.add_argument("--standby-output", required=True)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    lock, raw_lock = load_and_validate_lock(arguments.lock)
    lock_sha256 = hashlib.sha256(raw_lock).hexdigest()
    snapshot = canonical_json({"source_lock_sha256": lock_sha256, "client_release_lock": lock})
    primary_output = require_regular_output(arguments.primary_output)
    standby_output = require_regular_output(arguments.standby_output)
    primary_prior = read_prior_output(primary_output)
    primary_temp = prepare_atomic_temp(primary_output, snapshot)
    try:
        standby_temp = prepare_atomic_temp(standby_output, snapshot)
    except ClientReleaseError:
        remove_if_present(primary_temp)
        raise
    commit_pair(primary_output, standby_output, primary_temp, standby_temp, primary_prior)
    print(
        canonical_json(
            {
                "lock_sha256": lock_sha256,
                "primary_output_sha256": hashlib.sha256(primary_output.read_bytes()).hexdigest(),
                "standby_output_sha256": hashlib.sha256(standby_output.read_bytes()).hexdigest(),
            }
        ),
        end="",
    )


if __name__ == "__main__":
    try:
        main()
    except ClientReleaseError as error:
        print(f"CLIENT_RELEASE_RENDER=FAIL reason={error.reason}", file=sys.stderr)
        raise SystemExit(1)
    except OSError:
        print("CLIENT_RELEASE_RENDER=FAIL reason=OUTPUT_IO_FAILED", file=sys.stderr)
        raise SystemExit(1)
