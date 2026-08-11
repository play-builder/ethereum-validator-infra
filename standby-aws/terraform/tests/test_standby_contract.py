#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
text = "\n".join(path.read_text() for path in root.glob("*.tf"))
required = (
    'http_tokens   = "required"',
    "disable_api_termination",
    'resource "aws_ebs_volume" "chain"',
    'resource "aws_ebs_volume" "validator"',
    "prevent_destroy = true",
    'data "aws_iam_instance_profile" "node"',
)
for token in required:
    assert token in text, token
assert text.count("prevent_destroy = true") >= 2
assert 'resource "aws_iam_role" "node"' not in text
assert 'for_each          = toset(var.admin_cidrs)' in text
assert 'description       = "SSH from approved operator /32"' in text
print("STANDBY_TERRAFORM_CONTRACT=PASS")
