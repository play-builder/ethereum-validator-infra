#!/usr/bin/env bash
# sso-session-probe.sh — IAM Identity Center 세션으로 recovery KMS 키를 실제로 쓸 수 있는지 검증한다.
# 장기 자격도, 6자리 코드 입력도 없다. MFA는 aws sso login 단계에서 이미 강제됐다.
set -euo pipefail
KMS_ARN=""; REGION=""; LABEL="RECOVERY_KMS_SSO"; EXPECT_SET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --kms-key-arn) KMS_ARN="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --expect-permission-set) EXPECT_SET="$2"; shift 2 ;;
    *) echo "SSO_PROBE=FAIL reason=bad_arg arg=$1" >&2; exit 2 ;;
  esac
done
[ -n "${KMS_ARN}" ] && [ -n "${REGION}" ] && [ -n "${EXPECT_SET}" ] \
  || { echo "SSO_PROBE=FAIL reason=missing_arg" >&2; exit 2; }
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
case "${CALLER}" in
  arn:aws:sts::*:assumed-role/AWSReservedSSO_${EXPECT_SET}_*) ;;
  *) echo "SSO_CALLER=FAIL expected=AWSReservedSSO_${EXPECT_SET} actual=${CALLER}" >&2; exit 1 ;;
esac
aws kms encrypt --key-id "${KMS_ARN}" --region "${REGION}" \
  --plaintext cGluZw== --query CiphertextBlob --output text >/dev/null
printf 'SSO_CALLER=%s\n%s=PASS region=%s\n' "${CALLER}" "${LABEL}" "${REGION}"
