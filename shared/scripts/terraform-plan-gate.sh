#!/usr/bin/env bash
# terraform-plan-gate.sh — plan을 저장하고 종료 코드를 규약대로 판정한다.
# 강의 화면에서 if/case를 걷어내기 위한 헬퍼. plan 로그는 stdout으로 그대로 흘린다.
#   --plan-out      : terraform 이 저장할 plan 파일 (관측 증거 경로와 무관)
#   --accept 0      : 변경 없음만 허용
#   --accept 0,2    : -detailed-exitcode 의 "변경 있음(2)"도 허용
set -euo pipefail
CHDIR=""; OUT=""; ACCEPT="0"; LABEL="TF_PLAN"; VARS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --chdir) CHDIR="$2"; shift 2 ;;
    --plan-out) OUT="$2"; shift 2 ;;
    --accept) ACCEPT="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --) shift; VARS=("$@"); break ;;
    *) echo "${LABEL}=FAIL reason=bad_arg arg=$1" >&2; exit 2 ;;
  esac
done
[ -n "${CHDIR}" ] && [ -n "${OUT}" ] || { echo "${LABEL}=FAIL reason=missing_arg" >&2; exit 2; }
PLAN_LOG="$(mktemp)"
trap 'rm -f "${PLAN_LOG}"' EXIT HUP INT TERM
rm -f "${OUT}"
PLAN_RC=0
terraform -chdir="${CHDIR}" plan -out="${OUT}" "${VARS[@]}" >"${PLAN_LOG}" || PLAN_RC=$?
printf '%s' ",${ACCEPT}," | grep -q ",${PLAN_RC}," || {
  cat "${PLAN_LOG}" >&2
  echo "${LABEL}=FAIL exit=${PLAN_RC} accepted=${ACCEPT}" >&2
  exit "${PLAN_RC}"
}
cat "${PLAN_LOG}"
echo "${LABEL}=PLANNED exit=${PLAN_RC} out=${OUT}"
