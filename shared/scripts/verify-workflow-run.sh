#!/bin/bash
URL=""
WORKFLOW=""
SHA=""
REF="main"
EVENT="workflow_dispatch"
PRINT_ID=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url)       URL="$2"; shift 2 ;;
    --workflow)  WORKFLOW="$2"; shift 2 ;;
    --sha)       SHA="$2"; shift 2 ;;
    --ref)       REF="$2"; shift 2 ;;
    --event)     EVENT="$2"; shift 2 ;;
    --print-id)  PRINT_ID=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

OUT=/dev/stdout
[ "${PRINT_ID}" -eq 0 ] || OUT=/dev/stderr

PASS_COUNT=0
FAIL_COUNT=0
ok()  { printf 'CHECK %-20s OK    %s\n' "$1" "$2" >"${OUT}"; PASS_COUNT=$((PASS_COUNT+1)); }
bad() { printf 'CHECK %-20s FAIL  %s\n' "$1" "$2" >"${OUT}"; FAIL_COUNT=$((FAIL_COUNT+1)); }

[ -n "${GITHUB_REPOSITORY}" ] || { echo "GITHUB_REPOSITORY is empty — source lab.env 먼저 실행" >&2; exit 2; }
[ -n "${URL}" ]      || { echo "--url is required" >&2; exit 2; }
[ -n "${WORKFLOW}" ] || { echo "--workflow is required" >&2; exit 2; }
[ -n "${SHA}" ]      || { echo "--sha is required" >&2; exit 2; }

RUN_ID="${URL##*/}"
EXPECTED_URL="https://github.com/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}"

case "${RUN_ID}" in
  ''|*[!0-9]*) bad url-shape "run id is not numeric: ${RUN_ID}" ;;
  *)           ok  url-shape "run id ${RUN_ID}" ;;
esac
[ "${URL}" = "${EXPECTED_URL}" ] \
  && ok url-repository "${URL}" \
  || bad url-repository "expected ${EXPECTED_URL}"

if [ "${FAIL_COUNT}" -eq 0 ]; then
  RUN_JSON="$(gh run view "${RUN_ID}" --repo "${GITHUB_REPOSITORY}" \
    --json event,headBranch,headSha,workflowName,url 2>/dev/null)"
else
  RUN_JSON=""
fi
[ -n "${RUN_JSON}" ] \
  && ok run-lookup "gh run view ${RUN_ID}" \
  || bad run-lookup "run not found or gh not authenticated"

field() { printf '%s' "${RUN_JSON}" | jq -r "${1}" 2>/dev/null; }
compare() {
  local name="$1" got="$2" want="$3"
  [ "${got}" = "${want}" ] && ok "${name}" "${got}" || bad "${name}" "got ${got:-<empty>}, want ${want}"
}
if [ -n "${RUN_JSON}" ]; then
  compare event     "$(field .event)"        "${EVENT}"
  compare branch    "$(field .headBranch)"   "${REF}"
  compare commit    "$(field .headSha)"      "${SHA}"
  compare workflow  "$(field .workflowName)" "${WORKFLOW}"
  compare url       "$(field .url)"          "${URL}"
fi

RESULT=PASS
[ "${FAIL_COUNT}" -eq 0 ] || RESULT=FAIL
printf 'WORKFLOW_RUN=%s run_id=%s checks=%d fail=%d\n' \
  "${RESULT}" "${RUN_ID}" $((PASS_COUNT+FAIL_COUNT)) "${FAIL_COUNT}" >"${OUT}"
[ "${RESULT}" = "PASS" ] || exit 1
[ "${PRINT_ID}" -eq 0 ] || printf '%s\n' "${RUN_ID}"
