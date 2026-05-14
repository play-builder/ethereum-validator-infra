#!/usr/bin/env python3
"""Render byte-identical package mirrors of the upgrade schema and validator.

The root files under shared/schemas and shared/scripts are the only edit
authority. This renderer copies their exact bytes, atomically and
symlink-safe, to the two package paths; if the second replace fails the
first output is rolled back so the package never holds a half-updated pair.
"""

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile


sys.dont_write_bytecode = True

SCHEMA_MODE = 0o644
VALIDATOR_MODE = 0o755


class UpgradePackageError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_line(pairs):
    body = ",".join(f'"{key}":"{value}"' for key, value in sorted(pairs.items()))
    return "{" + body + "}"


def read_source(path):
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise UpgradePackageError("SOURCE_READ_FAILED") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise UpgradePackageError("SOURCE_NOT_REGULAR")
    try:
        return path.read_bytes()
    except OSError as error:
        raise UpgradePackageError("SOURCE_READ_FAILED") from error


def require_regular_output(path):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as error:
        raise UpgradePackageError("OUTPUT_IO_FAILED") from error
    if metadata is not None and (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)):
        raise UpgradePackageError("OUTPUT_NOT_REGULAR")
    try:
        invalid_parent = not path.parent.is_dir() or path.parent.is_symlink()
    except OSError as error:
        raise UpgradePackageError("OUTPUT_IO_FAILED") from error
    if invalid_parent:
        raise UpgradePackageError("OUTPUT_PARENT_INVALID")
    return path


def read_prior_output(path):
    try:
        return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UpgradePackageError("OUTPUT_READ_FAILED") from error


def prepare_atomic_temp(path, content, mode):
    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as output:
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
        raise UpgradePackageError("OUTPUT_WRITE_FAILED") from error
    return Path(temporary_path)


def remove_if_present(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise UpgradePackageError("OUTPUT_ROLLBACK_FAILED") from error


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
        raise UpgradePackageError("OUTPUT_ROLLBACK_FAILED") from error


def commit_pair(schema_output, validator_output, schema_temp, validator_temp, schema_prior):
    try:
        os.replace(schema_temp, schema_output)
        schema_temp = None
        os.replace(validator_temp, validator_output)
        validator_temp = None
    except OSError as error:
        if schema_temp is None:
            try:
                restore_prior_output(schema_output, schema_prior)
            except UpgradePackageError as rollback_error:
                raise rollback_error from error
        raise UpgradePackageError("OUTPUT_WRITE_FAILED") from error
    finally:
        if schema_temp is not None:
            remove_if_present(schema_temp)
        if validator_temp is not None:
            remove_if_present(validator_temp)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    parser.add_argument("--validator", required=True)
    parser.add_argument("--schema-output", required=True)
    parser.add_argument("--validator-output", required=True)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    schema_bytes = read_source(arguments.schema)
    validator_bytes = read_source(arguments.validator)
    schema_output = require_regular_output(arguments.schema_output)
    validator_output = require_regular_output(arguments.validator_output)
    schema_prior = read_prior_output(schema_output)
    schema_temp = prepare_atomic_temp(schema_output, schema_bytes, SCHEMA_MODE)
    try:
        validator_temp = prepare_atomic_temp(validator_output, validator_bytes, VALIDATOR_MODE)
    except UpgradePackageError:
        remove_if_present(schema_temp)
        raise
    commit_pair(schema_output, validator_output, schema_temp, validator_temp, schema_prior)
    print(
        canonical_line(
            {
                "schema_sha256": hashlib.sha256(schema_output.read_bytes()).hexdigest(),
                "validator_sha256": hashlib.sha256(validator_output.read_bytes()).hexdigest(),
            }
        ),
        end="",
    )


if __name__ == "__main__":
    try:
        main()
    except UpgradePackageError as error:
        print(f"CLIENT_UPGRADE_RENDER=FAIL reason={error.reason}", file=sys.stderr)
        raise SystemExit(1)
    except OSError:
        print("CLIENT_UPGRADE_RENDER=FAIL reason=OUTPUT_IO_FAILED", file=sys.stderr)
        raise SystemExit(1)
