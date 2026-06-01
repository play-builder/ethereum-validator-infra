#!/usr/bin/env bash
# sso-seal-session.sh — 세리머니 seal: SSO 세션 주체를 확인한 뒤 seal-keystores.sh seal을 비루트로 실행한다.
# 세션 획득은 aws sso login이 담당한다. 전체 cache 정리는 role 전환 수단이 아니며 이 스크립트는 자격을 만들지 않는다.
set -euo pipefail
KEYSTORE_DIR=""; OUT_DIR=""; WORK_DIR=""; KMS_ARN=""; EXPECT_SET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --keystore-dir) KEYSTORE_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --work-dir) WORK_DIR="$2"; shift 2 ;;
    --kms-key-arn) KMS_ARN="$2"; shift 2 ;;
    --expect-permission-set) EXPECT_SET="$2"; shift 2 ;;
    *) echo "SEAL_SESSION=FAIL reason=bad_arg arg=$1" >&2; exit 2 ;;
  esac
done
[ -n "${KEYSTORE_DIR}" ] && [ -n "${OUT_DIR}" ] && [ -n "${WORK_DIR}" ] && [ -n "${KMS_ARN}" ] && [ -n "${EXPECT_SET}" ] \
  || { echo "SEAL_SESSION=FAIL reason=missing_arg" >&2; exit 2; }
HELPER_DIR="$(cd "$(dirname "$0")" && pwd -P)"
CALLER="$(aws sts get-caller-identity --query Arn --output text)"
case "${CALLER}" in
  arn:aws:sts::*:assumed-role/AWSReservedSSO_${EXPECT_SET}_*) ;;
  *) echo "SSO_CALLER=FAIL expected=AWSReservedSSO_${EXPECT_SET} actual=${CALLER}" >&2; exit 1 ;;
esac
EXPIRES="$(aws sts get-caller-identity >/dev/null 2>&1 && echo valid || echo expired)"
[ "${EXPIRES}" = "valid" ] || { echo "SSO_SESSION=EXPIRED hint=aws sso login --profile ${LAB_AWS_PROFILE:-hoodi-testnet-dev-builder}" >&2; exit 1; }
printf 'SSO_SEAL_SESSION_CALLER=%s\n' "${CALLER}"
( cd "${OUT_DIR}" && bash "${HELPER_DIR}/seal-keystores.sh" seal \
    --keystore-dir "${KEYSTORE_DIR}" --out-dir . \
    --work-dir "${WORK_DIR}" --kms-key-arn "${KMS_ARN}" )
echo "SEAL_SSO_SCOPE=SESSION_BOUND session=sso-login-managed"
