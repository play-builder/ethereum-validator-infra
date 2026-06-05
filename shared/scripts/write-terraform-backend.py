#!/usr/bin/env python3
"""Write the lab's Terraform S3 backend configuration."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not BUCKET_PATTERN.fullmatch(args.bucket):
        raise SystemExit("invalid S3 bucket name")
    if not REGION_PATTERN.fullmatch(args.region):
        raise SystemExit("invalid AWS Region")

    content = f'''terraform {{
  backend "s3" {{
    bucket       = "{args.bucket}"
    key          = "primary-aws/hoodi/terraform.tfstate"
    region       = "{args.region}"
    use_lockfile = true
  }}
}}
'''
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"BACKEND_CONFIG=WRITTEN path={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
