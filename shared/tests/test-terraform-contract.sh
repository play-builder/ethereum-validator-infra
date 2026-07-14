#!/usr/bin/env bash
# test-terraform-contract.sh — 감사 P1-01/P1-02 회귀 방지 (네트워크·과금 없음)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TF="$HERE/../../primary-aws/terraform"
P=0; F=0
ok(){ echo "TEST PASS: $1"; P=$((P+1)); }
bad(){ echo "TEST FAIL: $1"; F=$((F+1)); }

# P1-01: 버전 계약이 S3 native lock과 일치해야 한다 (1.10+)
if grep -qE 'required_version *= *">= *1\.1[0-9]' "$TF/versions.tf"; then
  ok "required_version >= 1.10 (use_lockfile 전제와 일치)"
else bad "required_version이 1.10 미만 — use_lockfile과 모순(P1-01)"; fi

# P1-01: 1.9 분기(DynamoDB 대안)가 문서에 남아 있으면 안 된다
if grep -ni "dynamodb" "$HERE/../../primary-aws/terraform/README.md" "$HERE/../../primary-aws/bootstrap/cicd/README.md" >/dev/null 2>&1; then
  bad "문서에 DynamoDB 잠금 분기 잔존 — 단일 잠금 계약 위반(P1-01)"
else ok "잠금 방식 단일화(문서에 DynamoDB 분기 없음)"; fi

# P1-02: provider lock file 존재 (없으면 실행일마다 provider가 부유)
if [ -f "$TF/.terraform.lock.hcl" ]; then
  if grep -q 'provider "registry.terraform.io/hashicorp/aws"' "$TF/.terraform.lock.hcl" \
    && grep -q 'version *= *"6\.' "$TF/.terraform.lock.hcl" \
    && grep -q 'zh:' "$TF/.terraform.lock.hcl"; then
    ok "AWS provider lock file 존재 + registry selection/checksum 포함"
  else bad "lock file에 AWS provider selection 또는 zh checksum이 없음(P1-02)"; fi
else
  MSG=".terraform.lock.hcl 없음 — 신뢰 환경에서 생성해야 한다:
      terraform -chdir=primary-aws/terraform providers lock -platform=linux_amd64 -platform=darwin_arm64
      생성·리뷰·커밋 후 학습자는 terraform init -lockfile=readonly 로만 초기화한다."
  bad "$MSG"
fi
echo "----------------------------------------"
echo "terraform-contract tests: PASS=$P FAIL=$F"
[ "$F" -eq 0 ]
