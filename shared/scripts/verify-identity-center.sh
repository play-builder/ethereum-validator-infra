#!/bin/bash
# IAM Identity Center 구성 결과를 read-only API로 일괄 검증한다.
#
#   --account-id 는 워크로드 계정 hoodi-testnet-dev 의 12자리 Account ID다.
#   playbuilder-management(관리 계정)나 playbuilder-identity(사람 관리 계정)의 ID가 아니다.
#
#   bash shared/scripts/verify-identity-center.sh \
#     --profile playbuilder-identity-admin --account-id <hoodi-testnet-dev Account ID>
#   bash shared/scripts/verify-identity-center.sh \
#     --profile playbuilder-identity-admin --account-id <hoodi-testnet-dev Account ID> --check recall
#
# 기본 모드: 사용자 2명과 email · Permission Set 2개 · 계정 할당 등 16가지를 조회한다.
# recall 모드: 임시 부트스트랩 Permission Set의 할당이 0건으로 회수됐는지 확인한다.
# 두 모드 모두 조회 API만 사용하며 아무것도 만들거나 바꾸지 않는다.

PROFILE=""
ACCOUNT_ID=""
MODE="setup"
OP1_USER="${OP1_SSO_USER:-testnet_operator_01}"
OP2_USER="${OP2_SSO_USER:-testnet_operator_02}"
OP1_PS="${OP1_PERMISSION_SET:-testnet_operator_01_builder}"
OP2_PS="${OP2_PERMISSION_SET:-testnet_operator_02_approver}"
EXPECTED_OP1_EMAIL="${OP1_EMAIL:-}"
EXPECTED_OP2_EMAIL="${OP2_EMAIL:-}"
WORKLOAD_NAME="${WORKLOAD_ACCOUNT_NAME:-hoodi-testnet-dev}"
while [ $# -gt 0 ]; do
  case "$1" in
    --profile)    PROFILE="$2"; shift 2 ;;
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --check)      MODE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "${ACCOUNT_ID}" in
  ""|*[!0-9]*) ;;
  *) if [ "${#ACCOUNT_ID}" -ne 12 ]; then
       echo "account-id must be the 12-digit hoodi-testnet-dev Account ID (got ${#ACCOUNT_ID} digits)" >&2
       exit 2
     fi ;;
esac
if [ -z "${PROFILE}" ] || [ -z "${ACCOUNT_ID}" ]; then
  echo "usage: bash shared/scripts/verify-identity-center.sh \\" >&2
  echo "         --profile <SSO profile> --account-id <hoodi-testnet-dev Account ID, 12-digit> [--check recall]" >&2
  exit 2
fi
if [ -z "${EXPECTED_OP1_EMAIL}" ] || [ -z "${EXPECTED_OP2_EMAIL}" ]; then
  echo "OP1_EMAIL / OP2_EMAIL is empty — source lab.env 먼저 실행" >&2
  exit 2
fi

PASS_COUNT=0
FAIL_COUNT=0
ok()   { printf 'CHECK %-24s OK    %s\n' "$1" "$2"; PASS_COUNT=$((PASS_COUNT+1)); }
bad()  { printf 'CHECK %-24s FAIL  %s\n' "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); }
done_summary() {
  local label="$1" result=PASS
  [ "${FAIL_COUNT}" -eq 0 ] || result=FAIL
  printf '%s=%s checks=%d fail=%d\n' "${label}" "${result}" $((PASS_COUNT+FAIL_COUNT)) "${FAIL_COUNT}"
  [ "${result}" = "PASS" ]
}

A() { aws "$@" --profile "${PROFILE}"; }

## ── Identity Center instance와 identity store ─────────────────────────────
read -r INSTANCE_ARN STORE_ID < <(A sso-admin list-instances \
  --query 'Instances[0].[InstanceArn,IdentityStoreId]' --output text 2>/dev/null)
if [ -n "${INSTANCE_ARN}" ] && [ "${INSTANCE_ARN}" != "None" ]; then
  ok identity_instance "${STORE_ID}"
else
  bad identity_instance "instance not found (profile 또는 Region 확인)"
  done_summary IDENTITY_READBACK
  exit 1
fi

## 이름으로 Permission Set ARN을 찾는다
find_ps_arn() {
  local wanted="$1" arn name
  for arn in $(A sso-admin list-permission-sets --instance-arn "${INSTANCE_ARN}" \
      --query 'PermissionSets[]' --output text); do
    name="$(A sso-admin describe-permission-set --instance-arn "${INSTANCE_ARN}" \
      --permission-set-arn "${arn}" --query 'PermissionSet.Name' --output text)"
    if [ "${name}" = "${wanted}" ]; then
      printf '%s\n' "${arn}"
      return 0
    fi
  done
  return 1
}

## ── recall 모드: 임시 권한 회수 확인 ─────────────────────────────────────────
if [ "${MODE}" = "recall" ]; then
  BOOT_ARN="$(find_ps_arn terraform_cicd_bootstrap_admin)"
  if [ -z "${BOOT_ARN}" ]; then
    bad bootstrap_permission_set "terraform_cicd_bootstrap_admin not found"
  else
    ok bootstrap_permission_set "terraform_cicd_bootstrap_admin"
    ROWS="$(A sso-admin list-account-assignments --instance-arn "${INSTANCE_ARN}" \
      --account-id "${ACCOUNT_ID}" --permission-set-arn "${BOOT_ARN}" \
      --query 'length(AccountAssignments)' --output text)"
    if [ "${ROWS}" = "0" ]; then
      ok bootstrap_assignment "0 rows (recalled)"
    else
      bad bootstrap_assignment "${ROWS} rows still assigned"
    fi
  fi
  done_summary IDENTITY_READBACK
  exit $?
fi

## ── 기본 모드 1) 운영자 사용자 2명 ──────────────────────────────────────────
lookup_user() {  # $1=username → USER_ID·USER_EMAIL 전역에 저장
  USER_ID="$(A identitystore list-users --identity-store-id "${STORE_ID}" \
    --query "Users[?UserName=='$1'].UserId" --output text)"
  USER_EMAIL="$(A identitystore list-users --identity-store-id "${STORE_ID}" \
    --query "Users[?UserName=='$1'].Emails[?Primary].Value | [0] | [0]" --output text)"
}
lookup_user "${OP1_USER}"
OP1_UID="${USER_ID}"
if [ -n "${OP1_UID}" ] && [ "${OP1_UID}" != "None" ]; then
  ok op1_user_record "${OP1_USER}"
else
  bad op1_user_record "${OP1_USER} not found"
fi
if [ "${USER_EMAIL}" = "${EXPECTED_OP1_EMAIL}" ]; then
  ok op1_email "${USER_EMAIL}"
else
  bad op1_email "expected=${EXPECTED_OP1_EMAIL} actual=${USER_EMAIL:-<empty>}"
fi
lookup_user "${OP2_USER}"
OP2_UID="${USER_ID}"
if [ -n "${OP2_UID}" ] && [ "${OP2_UID}" != "None" ]; then
  ok op2_user_record "${OP2_USER}"
else
  bad op2_user_record "${OP2_USER} not found"
fi
if [ "${USER_EMAIL}" = "${EXPECTED_OP2_EMAIL}" ]; then
  ok op2_email "${USER_EMAIL}"
else
  bad op2_email "expected=${EXPECTED_OP2_EMAIL} actual=${USER_EMAIL:-<empty>}"
fi
if [ -n "${OP1_UID}" ] && [ -n "${OP2_UID}" ] && [ "${OP1_UID}" != "${OP2_UID}" ]; then
  ok users_distinct "2 distinct UserIds"
else
  bad users_distinct "UserId empty or identical"
fi

## ── 기본 모드 2) Permission Set 2개 — 이름·세션·inline policy·managed 0개 ────
check_permission_set() {  # $1=이름 $2=태그 $3=inline policy 원본 경로 → PS_ARN 전역
  local name="$1" tag="$2" src="$3" arn duration remote local_json managed
  arn="$(find_ps_arn "${name}")"
  if [ -z "${arn}" ]; then
    bad "${tag}_permission_set" "${name} not found"
    PS_ARN=""
    return
  fi
  duration="$(A sso-admin describe-permission-set --instance-arn "${INSTANCE_ARN}" \
    --permission-set-arn "${arn}" --query 'PermissionSet.SessionDuration' --output text)"
  if [ "${duration}" = "PT4H" ]; then
    ok "${tag}_permission_set" "${name} PT4H"
  else
    bad "${tag}_permission_set" "${name} SessionDuration=${duration} (expected PT4H)"
  fi
  remote="$(A sso-admin get-inline-policy-for-permission-set --instance-arn "${INSTANCE_ARN}" \
    --permission-set-arn "${arn}" --query InlinePolicy --output text | jq -cS .)"
  local_json="$(jq -cS . "${src}")"
  if [ -n "${remote}" ] && [ "${remote}" = "${local_json}" ]; then
    ok "${tag}_inline_policy" "matches ${src}"
  else
    bad "${tag}_inline_policy" "differs from ${src}"
  fi
  managed="$(A sso-admin list-managed-policies-in-permission-set --instance-arn "${INSTANCE_ARN}" \
    --permission-set-arn "${arn}" --query 'length(AttachedManagedPolicies)' --output text)"
  if [ "${managed}" = "0" ]; then
    ok "${tag}_managed_policies" "0 attached"
  else
    bad "${tag}_managed_policies" "${managed} attached (expected 0)"
  fi
  PS_ARN="${arn}"
}
check_permission_set "${OP1_PS}" op1 primary-aws/bootstrap/operator-1-builder.json
OP1_PS_ARN="${PS_ARN}"
check_permission_set "${OP2_PS}" op2 primary-aws/bootstrap/operator-2-approver.json
OP2_PS_ARN="${PS_ARN}"
if [ -n "${OP1_PS_ARN}" ] && [ -n "${OP2_PS_ARN}" ] && [ "${OP1_PS_ARN}" != "${OP2_PS_ARN}" ]; then
  ok permission_set_arns "2 distinct ARNs"
else
  bad permission_set_arns "ARN empty or identical"
fi

## ── 기본 모드 3) 워크로드 계정 할당과 provisioning ───────────────────────────
check_assignment() {  # $1=태그 $2=PS ARN $3=기대 UserId
  local tag="$1" arn="$2" uid="$3" row count principal ptype account
  if [ -z "${arn}" ]; then
    bad "${tag}_assignment" "permission set missing"
    return
  fi
  row="$(A sso-admin list-account-assignments --instance-arn "${INSTANCE_ARN}" \
    --account-id "${ACCOUNT_ID}" --permission-set-arn "${arn}" \
    --query 'AccountAssignments[].[PrincipalId,PrincipalType,AccountId]' --output text)"
  count="$(printf '%s\n' "${row}" | grep -c .)"
  principal="$(printf '%s\n' "${row}" | awk 'NR==1{print $1}')"
  ptype="$(printf '%s\n' "${row}" | awk 'NR==1{print $2}')"
  account="$(printf '%s\n' "${row}" | awk 'NR==1{print $3}')"
  if [ "${count}" = "1" ] && [ "${ptype}" = "USER" ] && [ "${account}" = "${ACCOUNT_ID}" ] \
     && [ -n "${uid}" ] && [ "${principal}" = "${uid}" ]; then
    ok "${tag}_assignment" "1 row USER on ${WORKLOAD_NAME} ${ACCOUNT_ID}"
  else
    bad "${tag}_assignment" "rows=${count} type=${ptype} account=${account}"
  fi
}
check_assignment op1 "${OP1_PS_ARN}" "${OP1_UID}"
check_assignment op2 "${OP2_PS_ARN}" "${OP2_UID}"

PROVISIONED="$(A sso-admin list-permission-sets-provisioned-to-account \
  --instance-arn "${INSTANCE_ARN}" --account-id "${ACCOUNT_ID}" \
  --query 'PermissionSets' --output text | tr '\t\n' '  ')"
PROVISIONED=" ${PROVISIONED} "
HIT=0
case "${PROVISIONED}" in *" ${OP1_PS_ARN} "*) HIT=$((HIT+1)) ;; esac
case "${PROVISIONED}" in *" ${OP2_PS_ARN} "*) HIT=$((HIT+1)) ;; esac
if [ "${HIT}" = "2" ] && [ -n "${OP1_PS_ARN}" ] && [ -n "${OP2_PS_ARN}" ]; then
  ok provisioned_sets "both provisioned to ${WORKLOAD_NAME} ${ACCOUNT_ID}"
else
  bad provisioned_sets "provisioned ${HIT}/2"
fi

done_summary IDENTITY_READBACK
exit $?
