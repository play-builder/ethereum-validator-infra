#!/usr/bin/env python3
"""Compute failover and failback recovery time from two CH18 timeline files."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "validator-roundtrip-rto/v1"
MEASUREMENT = "source_vc_stop_to_target_first_successful_attestation"
LABEL = "ROUNDTRIP_RTO"

FAILOVER_START = "FAILOVER_SOURCE_VC_STOP_UTC"
FAILOVER_END = "FAILOVER_TARGET_FIRST_ATTESTATION_UTC"
FAILBACK_START = "FAILBACK_SOURCE_VC_STOP_UTC"
FAILBACK_END = "FAILBACK_TARGET_FIRST_ATTESTATION_UTC"


def fail(reason: str, **extra: object) -> None:
    parts = [f"{LABEL}=FAIL", f"reason={reason}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    print(" ".join(parts), file=sys.stderr)
    raise SystemExit(1)


def read_unique(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size == 0:
        fail("timeline_missing_or_empty", path=str(path))
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key:
            fail("timeline_line_not_key_value", path=str(path), line=line)
        if key in values:
            fail("timeline_duplicate_key", path=str(path), key=key)
        values[key] = value
    return values


def utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        fail("timestamp_not_canonical_utc", field=field, value=value)
    return parsed.replace(tzinfo=timezone.utc)


def pick(values: dict[str, str], key: str, path: Path) -> str:
    if key not in values:
        fail("timeline_field_missing", path=str(path), field=key)
    return values[key]


def duration(start: str, end: str, prefix: str) -> int:
    seconds = int((utc(end, f"{prefix}_end") - utc(start, f"{prefix}_start")).total_seconds())
    if seconds < 0:
        fail("target_attestation_precedes_source_stop", phase=prefix)
    return seconds


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--failover-timeline", required=True)
    p.add_argument("--failback-timeline", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()

    fo_path = Path(a.failover_timeline)
    fb_path = Path(a.failback_timeline)
    if fo_path.resolve() == fb_path.resolve():
        fail("same_timeline_file_for_both_phases")

    fo = read_unique(fo_path)
    fb = read_unique(fb_path)

    fo_start = pick(fo, FAILOVER_START, fo_path)
    fo_end = pick(fo, FAILOVER_END, fo_path)
    fb_start = pick(fb, FAILBACK_START, fb_path)
    fb_end = pick(fb, FAILBACK_END, fb_path)

    fo_seconds = duration(fo_start, fo_end, "failover")
    fb_seconds = duration(fb_start, fb_end, "failback")

    report = {
        "schema": SCHEMA,
        "measurement": MEASUREMENT,
        "failover": {"start_utc": fo_start, "end_utc": fo_end, "rto_seconds": fo_seconds},
        "failback": {"start_utc": fb_start, "end_utc": fb_end, "rto_seconds": fb_seconds},
    }
    Path(a.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"FAILOVER_RTO={fo_end} - {fo_start} = {fo_seconds}s")
    print(f"FAILBACK_RTO={fb_end} - {fb_start} = {fb_seconds}s")
    print(f"{LABEL}=PASS out={a.out}")


if __name__ == "__main__":
    main()
