#!/bin/bash
# GitHub 승인 통제선(팀 분리·Environment 보호)을 read-only API로 일괄 검증한다.
#
#   bash shared/scripts/verify-github-controls.sh --check teams
#   bash shared/scripts/verify-github-controls.sh --check ruleset
#   bash shared/scripts/verify-github-controls.sh --check environments
#   bash shared/scripts/verify-github-controls.sh --check handover
#
# lab.env의 GITHUB_REPOSITORY와 GITHUB_*_ACCOUNT 변수를 읽는다. 계정 이름은
# --operator2 / --security-reviewer 로 덮어쓸 수 있다. gh는 배포 담당자 계정으로
# 로그인돼 있어야 하며, 조회 API만 사용하고 아무것도 만들거나 바꾸지 않는다.

MODE=""
OPERATOR2="${GITHUB_OPERATOR2_ACCOUNT:-}"
SECURITY="${GITHUB_SECURITY_ACCOUNT:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --check)             MODE="$2"; shift 2 ;;
    --operator2)         OPERATOR2="$2"; shift 2 ;;
    --security-reviewer) SECURITY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "${GITHUB_REPOSITORY}" ]; then
  echo "GITHUB_REPOSITORY is empty — source lab.env 먼저 실행" >&2
  exit 2
fi
GITHUB_ORG="${GITHUB_REPOSITORY%%/*}"
API_VER="X-GitHub-Api-Version: 2026-03-10"

PASS_COUNT=0
FAIL_COUNT=0
ok()  { printf 'CHECK %-24s OK    %s\n' "$1" "$2"; PASS_COUNT=$((PASS_COUNT+1)); }
bad() { printf 'CHECK %-24s FAIL  %s\n' "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); }
done_summary() {
  local result=PASS
  [ "${FAIL_COUNT}" -eq 0 ] || result=FAIL
  printf 'GITHUB_CONTROLS=%s checks=%d fail=%d\n' "${result}" $((PASS_COUNT+FAIL_COUNT)) "${FAIL_COUNT}"
  [ "${result}" = "PASS" ]
}

## ── teams 모드: 승인 팀 분리 ────────────────────────────────────────────────
run_teams() {
  if [ -z "${OPERATOR2}" ] || [ -z "${SECURITY}" ]; then
    echo "GITHUB_OPERATOR2_ACCOUNT / GITHUB_SECURITY_ACCOUNT 가 비어 있습니다." >&2
    echo "CHAPTER 1의 lab.env 기록(관리 계정 입력)을 먼저 진행하고 source lab.env 후 다시 실행합니다." >&2
    exit 2
  fi
  local team out members1 members2 op1
  for team in platform-approvers security-approvers; do
    out="$(gh api -H "${API_VER}" "orgs/${GITHUB_ORG}/teams/${team}" --jq '.slug' 2>/dev/null)"
    if [ "${out}" = "${team}" ]; then
      ok "team_${team%%-*}" "${team} exists"
    else
      bad "team_${team%%-*}" "${team} not found in ${GITHUB_ORG}"
    fi
  done

  op1="$(gh api -H "${API_VER}" user --jq '.login')"
  if [ -n "${GITHUB_OPERATOR1_ACCOUNT:-}" ]; then
    if [ "${op1}" = "${GITHUB_OPERATOR1_ACCOUNT}" ]; then
      ok active_is_operator1 "gh active = ${op1}"
    else
      bad active_is_operator1 "active=${op1} — gh auth switch --user \"\${GITHUB_OPERATOR1_ACCOUNT}\" 후 다시 실행"
    fi
  fi
  members1="$(gh api -H "${API_VER}" --paginate \
    "orgs/${GITHUB_ORG}/teams/platform-approvers/members?role=all&per_page=100" \
    --jq '.[].login' | sort)"
  members2="$(gh api -H "${API_VER}" --paginate \
    "orgs/${GITHUB_ORG}/teams/security-approvers/members?role=all&per_page=100" \
    --jq '.[].login' | sort)"

  if [ "${members1}" = "${OPERATOR2}" ]; then
    ok platform_members "only ${OPERATOR2}"
  else
    bad platform_members "expected only ${OPERATOR2}, got: $(printf '%s' "${members1}" | tr '\n' ' ')— 팀을 만든 관리 계정이 남아 있으면 Members에서 Remove"
  fi
  if printf '%s\n' "${members2}" | grep -qx "${SECURITY}"; then
    ok security_members "contains ${SECURITY}"
  else
    bad security_members "${SECURITY} not in security-approvers"
  fi
  if printf '%s\n%s\n' "${members1}" "${members2}" | grep -qx "${op1}"; then
    bad operator1_excluded "${op1} is inside an approver team"
  else
    ok operator1_excluded "${op1} not in either team"
  fi
  if [ -n "${GITHUB_ADMIN_ACCOUNT:-}" ]; then
    if printf '%s\n%s\n' "${members1}" "${members2}" | grep -qx "${GITHUB_ADMIN_ACCOUNT}"; then
      bad admin_excluded "${GITHUB_ADMIN_ACCOUNT} 이 승인 팀에 남아 있음 — 팀 Members 화면에서 Remove"
    else
      ok admin_excluded "${GITHUB_ADMIN_ACCOUNT} not in either team"
    fi
  fi
  OVERLAP="$(comm -12 <(printf '%s\n' "${members1}") <(printf '%s\n' "${members2}") | grep -c .)"
  if [ "${OVERLAP}" = "0" ]; then
    ok team_separation "overlap=0"
  else
    bad team_separation "overlap=${OVERLAP}"
  fi
  if [ "${op1}" != "${OPERATOR2}" ] && [ "${op1}" != "${SECURITY}" ] && [ "${OPERATOR2}" != "${SECURITY}" ]; then
    ok three_distinct_logins "${op1} ${OPERATOR2} ${SECURITY}"
  else
    bad three_distinct_logins "logins are not three distinct accounts"
  fi

  local push
  for team in platform-contributors platform-approvers security-approvers; do
    push="$(gh api -H "${API_VER}" -H "Accept: application/vnd.github.v3.repository+json" \
      "orgs/${GITHUB_ORG}/teams/${team}/repos/${GITHUB_REPOSITORY}" \
      --jq '.permissions.push' 2>/dev/null)"
    if [ "${push}" = "true" ]; then
      ok "repo_${team}" "push on ${GITHUB_REPOSITORY#*/}"
    else
      bad "repo_${team}" "no write connection (Accept 헤더 응답 기준) — Collaborators and teams에서 Write로 연결"
    fi
  done
}

## ── environments 모드: 두 Environment 보호와 값 저장 0건 ─────────────────────
run_environments() {
  local env jq_protection jq_branches
  jq_protection='{name,rule:([.protection_rules[] | select(.type == "required_reviewers")] | length),reviewers:([.protection_rules[] | select(.type == "required_reviewers") | .reviewers[] | {type,slug:(.reviewer.slug // .reviewer.login)}] | sort_by(.type,.slug)),self:([.protection_rules[] | select(.type == "required_reviewers") | .prevent_self_review] | if length == 1 then .[0] else null end),cust:.deployment_branch_policy.custom_branch_policies} | if .rule == 1 and .reviewers == [{"type":"Team","slug":"platform-approvers"}] and .self == true and .cust == true then "ok" else error("protection mismatch") end'
  jq_branches='{branches:([.branch_policies[].name] | sort),total_count} | if .total_count == 1 and .branches == ["main"] then "ok" else error("branch policy mismatch") end'

  for env in hoodi-testnet-dev hoodi-testnet-dev-teardown; do
    if gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/environments/${env}" \
        --jq "${jq_protection}" >/dev/null 2>&1; then
      ok "env_${env##*-}_protection" "1 team reviewer, prevent_self_review=true"
    else
      bad "env_${env##*-}_protection" "${env} protection mismatch"
    fi
    if gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/environments/${env}/deployment-branch-policies" \
        --jq "${jq_branches}" >/dev/null 2>&1; then
      ok "env_${env##*-}_branches" "main only"
    else
      bad "env_${env##*-}_branches" "${env} branch policy mismatch"
    fi
  done

  local scope count total=0 badscope=""
  for scope in \
    "actions/variables" \
    "environments/hoodi-testnet-dev/variables" \
    "environments/hoodi-testnet-dev/secrets" \
    "environments/hoodi-testnet-dev-teardown/variables" \
    "environments/hoodi-testnet-dev-teardown/secrets"; do
    count="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/${scope}" --jq '.total_count' 2>/dev/null)"
    total=$((total + ${count:-0}))
    [ "${count}" = "0" ] || badscope="${badscope} ${scope}"
  done
  if [ "${total}" = "0" ]; then
    ok no_stored_values "variables/secrets total_count=0 (5 scopes)"
  else
    bad no_stored_values "non-zero in:${badscope}"
  fi
}


## ── ruleset 모드: protected-main ruleset 계약과 CODEOWNERS 해석 ─────────────
# 필요 환경: GITHUB_REPOSITORY, GITHUB_ACTIONS_APP_ID(검사 앱 numeric ID)
run_ruleset() {
  local ruleset_id contract
  if ! printf '%s' "${GITHUB_ACTIONS_APP_ID:-}" | grep -qE '^[0-9]+$'; then
    bad ruleset_inputs "GITHUB_ACTIONS_APP_ID가 숫자가 아님 — 앱 ID 확보 단계로 돌아간다"
    return
  fi
  ruleset_id="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/rulesets?includes_parents=false" \
    --jq '[.[] | select(.name == "protected-main") | .id] | if length == 1 then .[0] else error("expected exactly one protected-main ruleset") end' 2>/dev/null)"
  if printf '%s' "${ruleset_id}" | grep -qE '^[0-9]+$'; then
    ok ruleset_single "protected-main id=${ruleset_id}"
  else
    bad ruleset_single "저장소 소유 protected-main ruleset이 정확히 1개가 아님"
    return
  fi
  contract='{target,enforcement,bypass_actor_count:((.bypass_actors // []) | length),include:.conditions.ref_name.include,exclude:.conditions.ref_name.exclude,pull_request:(.rules[] | select(.type == "pull_request") | .parameters | {required_approving_review_count,require_code_owner_review,dismiss_stale_reviews_on_push,require_last_push_approval,required_review_thread_resolution}),required_status_checks_rule_count:([.rules[] | select(.type == "required_status_checks")] | length),required_status_checks:([.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[] | {context,integration_id}] | sort_by(.context)),strict_required_status_checks_policy:([.rules[] | select(.type == "required_status_checks") | .parameters.strict_required_status_checks_policy] | if length == 1 then .[0] else null end),deletion_blocked:any(.rules[]; .type == "deletion"),force_push_blocked:any(.rules[]; .type == "non_fast_forward")} | if .target == "branch" and .enforcement == "active" and .bypass_actor_count == 0 and .include == ["refs/heads/main"] and .exclude == [] and .pull_request.required_approving_review_count >= 1 and .pull_request.require_code_owner_review == true and .pull_request.dismiss_stale_reviews_on_push == true and .pull_request.require_last_push_approval == true and .pull_request.required_review_thread_resolution == true and .required_status_checks_rule_count == 1 and .required_status_checks == [{"context":"docs-contract","integration_id":(env.GITHUB_ACTIONS_APP_ID | tonumber)},{"context":"terraform-static","integration_id":(env.GITHUB_ACTIONS_APP_ID | tonumber)}] and .strict_required_status_checks_policy == true and .deletion_blocked and .force_push_blocked then "ok" else error("protected-main ruleset contract mismatch") end'
  if gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/rulesets/${ruleset_id}" --jq "${contract}" >/dev/null 2>&1; then
    ok ruleset_contract "active · main only · bypass 0 · checks app_id=${GITHUB_ACTIONS_APP_ID}"
  else
    bad ruleset_contract "명세 불일치 — target/enforcement/bypass/PR 규칙/required check 앱 ID를 화면에서 교정"
  fi
  if [ "$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/codeowners/errors?ref=main" --jq '.errors | length' 2>/dev/null)" = "0" ]; then
    ok codeowners_errors "errors=0 on main"
  else
    bad codeowners_errors "main의 CODEOWNERS 해석 오류 — codeowners/errors 응답의 줄을 교정"
  fi
}

## ── handover 모드: 승인 커밋 고정과 blob 인계 대조 ──────────────────────────
# 필요 환경: GITHUB_REPOSITORY, PARAMETERS_MAIN_SHA(파라미터 PR merge 커밋),
# 로컬 파일 primary-aws/terraform/ci/runtime-inputs.json (클론 루트에서 실행)
run_handover() {
  local main_sha params_at_first params_at_main manifest_at_main manifest_local
  if [ -z "${PARAMETERS_MAIN_SHA:-}" ]; then
    bad handover_inputs "PARAMETERS_MAIN_SHA is empty or not exported — 파라미터 PR merge SHA를 export한다"
    return
  fi
  main_sha="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/commits/main" --jq '.sha' 2>/dev/null)"
  if printf '%s' "${main_sha}" | grep -qE '^[0-9a-f]{40}$'; then
    ok main_sha_format "${main_sha}"
  else
    bad main_sha_format "main SHA가 40자리 16진수가 아님: ${main_sha:-<빈 값>}"
    return
  fi
  params_at_first="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/contents/primary-aws/bootstrap/cicd/parameters.json?ref=${PARAMETERS_MAIN_SHA}" --jq '.sha' 2>/dev/null)"
  params_at_main="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/contents/primary-aws/bootstrap/cicd/parameters.json?ref=${main_sha}" --jq '.sha' 2>/dev/null)"
  if [ -n "${params_at_first}" ] && [ "${params_at_first}" = "${params_at_main}" ]; then
    ok parameters_blob_unchanged "blob ${params_at_main}"
  else
    bad parameters_blob_unchanged "첫 승인 ${params_at_first:-<없음>} vs 현재 main ${params_at_main:-<없음>} — 파라미터가 승인 뒤 바뀜"
  fi
  manifest_at_main="$(gh api -H "${API_VER}" "repos/${GITHUB_REPOSITORY}/contents/primary-aws/terraform/ci/runtime-inputs.json?ref=${main_sha}" --jq '.sha' 2>/dev/null)"
  manifest_local="$(git hash-object primary-aws/terraform/ci/runtime-inputs.json 2>/dev/null)"
  if [ -n "${manifest_local}" ] && [ "${manifest_at_main}" = "${manifest_local}" ]; then
    ok manifest_blob_matches "main == local ${manifest_local}"
  else
    bad manifest_blob_matches "main ${manifest_at_main:-<없음>} vs local ${manifest_local:-<없음>} — 검토받은 바이트와 로컬이 다름"
  fi
  printf 'P01_APPROVED_MAIN_SHA=%s\n' "${main_sha}"
}

case "${MODE}" in
  teams)        run_teams ;;
  ruleset)      run_ruleset ;;
  environments) run_environments ;;
  handover)     run_handover ;;
  *) echo "usage: --check teams|ruleset|environments|handover" >&2; exit 2 ;;
esac
done_summary
exit $?
