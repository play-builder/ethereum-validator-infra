#!/usr/bin/env bash
# vc-lease.sh — self-fence lease. 감사 P1-04 대응으로 신설.
#
# 문제: 시나리오 B(주노드에 어떤 경로로도 접근 불가)에서 "체인에서 안 보인다"는
#       원격 서명자의 부재를 증명하지 못한다. 서명됐지만 아직 포함되지 않은
#       메시지는 나중에 포함될 수 있다.
#
# 해법: 서명 권한에 시한을 건다. VC는 유효한(만료 전) approval token이 있는
#       동안만 살아 있을 수 있고, 이 검사기가 주기적으로 그것을 강제한다.
#       → "연락이 안 된다"가 "시각 T 이후로는 서명이 불가능했다"로 바뀐다.
#         (token 갱신은 사람이 하므로, 접근 불능이 곧 만료로 이어진다)
#
# 방향성: 이 스크립트는 오직 "정지"만 한다. 기동은 절대 하지 않는다(D1 유지).
#         자동 stop은 언제나 안전한 방향이다 — 00_PLAN §1 비대칭 원칙.
#
# 종료 코드: 0 = lease 유효(아무 것도 하지 않음), 10 = 만료 감지·seal 완료,
#            20 = seal을 시도했으나 완료를 입증하지 못함,
#            2 = 설정/전제 오류(seal하지 않음, 알림 대상)
set -u -o pipefail

GATE_ENV="${VC_GATE_ENV:-/etc/ethereum/failover/gate.env}"
VC_UNIT="${VC_UNIT:-lighthouse-validator.service}"
LEASE_LOG="${LEASE_LOG:-/var/lib/ethereum-maintenance/evidence/vc-lease.log}"
GRACE_S="${LEASE_GRACE_S:-0}"          # 만료 후 유예(기본 0 — 만료 즉시 seal)
DRY_RUN="${LEASE_DRY_RUN:-0}"          # 1이면 판정만 하고 정지하지 않음(드릴/테스트)

log() {
  printf '%s\n' "$*"
  mkdir -p "$(dirname "$LEASE_LOG")" 2>/dev/null || true
  printf '{"ts_utc":"%s","msg":%s}\n' "$(date -u +%FT%TZ)" "$(printf '%s' "$*" | sed 's/"/\\"/g;s/^/"/;s/$/"/')" \
    >>"$LEASE_LOG" 2>/dev/null || true
}

log_command_output() { # $1=명령 이름 $2=캡처한 출력
  local command_name="$1" output="$2" line
  [ -n "$output" ] || return 0
  while IFS= read -r line; do
    log "$command_name: $line"
  done <<<"$output"
}

fence() { # $1=사유
  local reason="$1" stop_output disable_output mask_output pgrep_output pid_line
  local stop_rc disable_rc mask_rc sealed_rc token_remove_rc pgrep_rc
  local sealed=0 token_removed=0 pids=""
  log "LEASE=EXPIRED reason=$reason action=fencing unit=$VC_UNIT"
  if [ "$DRY_RUN" = "1" ]; then
    log "LEASE_DRY_RUN=1 — 실제 정지·봉인을 수행하지 않음(판정만)"
    log "LEASE_FENCE_FAILED reason=dry_run sealed=0 token_removed=0 remaining_vc_pids=[]"
    exit 20
  fi

  stop_output="$(systemctl stop "$VC_UNIT" 2>&1)"
  stop_rc=$?
  log_command_output stop "$stop_output"

  disable_output="$(systemctl disable "$VC_UNIT" 2>&1)"
  disable_rc=$?
  log_command_output disable "$disable_output"

  mask_output="$(systemctl mask "$VC_UNIT" 2>&1)"
  mask_rc=$?
  log_command_output mask "$mask_output"

  : 2>/dev/null >"$SEALED_MARKER"
  sealed_rc=$?
  if [ "$sealed_rc" -eq 0 ] && [ -f "$SEALED_MARKER" ]; then
    sealed=1
  fi

  # 만료된 token은 남겨 두지 않는다 — 다음 기동은 새 token(사람)으로만.
  rm -f "$TOKEN_FILE" 2>/dev/null
  token_remove_rc=$?
  if [ "$token_remove_rc" -eq 0 ] && [ ! -e "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ]; then
    token_removed=1
  fi

  pgrep_output="$(pgrep -f "${PGREP_PATTERN:-lighthouse([[:space:]]+|.*/)vc}" 2>&1)"
  pgrep_rc=$?
  if [ "$pgrep_rc" -eq 0 ]; then
    while IFS= read -r pid_line; do
      [ -n "$pid_line" ] && pids="${pids}${pids:+ }${pid_line}"
    done <<<"$pgrep_output"
  elif [ "$pgrep_rc" -ge 2 ]; then
    log_command_output pgrep "$pgrep_output"
  fi

  if [ "$stop_rc" -eq 0 ] && [ "$disable_rc" -eq 0 ] && [ "$mask_rc" -eq 0 ] \
     && [ "$sealed" -eq 1 ] && [ "$token_removed" -eq 1 ] && [ "$pgrep_rc" -eq 1 ]; then
    log "LEASE_FENCE_DONE sealed=1 token_removed=1 remaining_vc_pids=[] stop_rc=0 disable_rc=0 mask_rc=0 pgrep_rc=1"
    exit 10
  fi

  log "LEASE_FENCE_FAILED sealed=$sealed token_removed=$token_removed remaining_vc_pids=[$pids] stop_rc=$stop_rc disable_rc=$disable_rc mask_rc=$mask_rc sealed_rc=$sealed_rc token_remove_rc=$token_remove_rc pgrep_rc=$pgrep_rc"
  exit 20
}

[ -f "$GATE_ENV" ] || { log "LEASE=ERROR reason=gate_env_missing path=$GATE_ENV"; exit 2; }
# shellcheck disable=SC1090
. "$GATE_ENV"

TOKEN_FILE="${TOKEN_FILE:-/etc/ethereum/failover/approval.token}"
SEALED_MARKER="${SEALED_MARKER:-/etc/ethereum/failover/SEALED}"

# VC가 애초에 활성이 아니면 강제할 것이 없다(멱등).
state="$(systemctl is-active "$VC_UNIT" 2>/dev/null || true)"
if [ "$state" != "active" ] && [ "$state" != "activating" ]; then
  log "LEASE=NOOP vc_state=$state (활성 아님 — 조치 없음)"
  exit 0
fi

# 1) token 부재 = lease 없음 → seal
[ -f "$TOKEN_FILE" ] || fence "token_missing"

# 2) token 만료 확인 (+유예)
expires="$(awk -F': *' '$1=="expires_at_utc" {sub(/^[^:]*: */,""); print; exit}' "$TOKEN_FILE" 2>/dev/null)"
exp_s="$(date -u -d "$expires" +%s 2>/dev/null || true)"
[ -n "$exp_s" ] || fence "token_expiry_unparseable(value='${expires}')"
now_s="$(date -u +%s)"
if [ "$now_s" -ge $((exp_s + GRACE_S)) ]; then
  fence "token_expired_at=${expires} now=$(date -u +%FT%TZ) grace_s=${GRACE_S}"
fi

# 3) scope 일치 (다른 역할의 token으로 연명 금지)
scope="$(awk -F': *' '$1=="scope" {sub(/^[^:]*: */,""); print; exit}' "$TOKEN_FILE" 2>/dev/null)"
[ "$scope" = "${EXPECTED_SCOPE:-}" ] || fence "scope_mismatch(token=${scope} env=${EXPECTED_SCOPE:-unset})"

left=$(( exp_s - now_s ))
log "LEASE=VALID expires_at=${expires} remaining_s=${left} vc_state=${state}"
[ "$left" -gt 86400 ] || log "LEASE_WARN 남은 시간 24h 이하 — 재발급(사람) 준비 필요"
exit 0
