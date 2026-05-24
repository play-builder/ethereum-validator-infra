#!/usr/bin/env bash
# vc-gate.sh — Validator Client ExecStartPre gate (설계 결정 D3, 계획서 §4/§7)
#
# 역할: "키가 임포트된 VC 프로세스는 전 세계에 0개 또는 1개" 불변식을
#       systemd 기동 경로에서 기계적으로 강제한다. 하나라도 실패하면 VC는 뜨지 않는다.
#
# 이 스크립트가 절대 하지 않는 것:
#   - approval token 생성/갱신 (D4: token은 사람만 만든다 — make-approval-token.md)
#   - VC 기동/정지, 키 임포트, SP DB 조작
#   - 원격 호스트 접근 (provider fence 증거는 로컬 파일로 전달받아 검증한다)
#
# 종료 코드: 0 = 모든 검사 PASS(기동 허용), 1 = 하나 이상 FAIL(기동 거부)
#
# 사고 근거: INCIDENTS.md INC-01(Staked'21), INC-02(RockLogic'23),
#            INC-03(Launchnodes'23), INC-04(SSV'25)
set -u -o pipefail

GATE_ENV="${VC_GATE_ENV:-/etc/ethereum/failover/gate.env}"
FAILURES=0
NOW_EPOCH_S="$(date -u +%s)"
SNAPSHOT_DIR=""
ABSENCE_LOCK_DIR=""
ABSENCE_LOCK_HELD=0
ABSENCE_LOCK_TOKEN=""
ABSENCE_RELEASE_DETAIL=""

say()  { printf '%s\n' "$*"; }
pass() { say "CHECK=$1 STATUS=PASS${2:+ DETAIL=$2}"; }
fail() { say "CHECK=$1 STATUS=FAIL${2:+ DETAIL=$2}"; FAILURES=$((FAILURES + 1)); }

log_evidence() {
  local line="$1"
  if [ -n "${GATE_LOG:-}" ]; then
    mkdir -p "$(dirname "$GATE_LOG")" 2>/dev/null || true
    printf '%s\n' "$line" >>"$GATE_LOG" 2>/dev/null || true
  fi
}

cleanup_snapshot() {
  [ -n "$SNAPSHOT_DIR" ] || return 0
  if [ -x "${INPUT_SNAPSHOT_HELPER:-}" ]; then
    "$INPUT_SNAPSHOT_HELPER" cleanup \
      --base-dir "$INPUT_SNAPSHOT_BASE" \
      --expected-owner "$INPUT_OWNER_EXPECTED" \
      --snapshot-dir "$SNAPSHOT_DIR" >/dev/null 2>&1 || true
  fi
  SNAPSHOT_DIR=""
}

cleanup_absence_lock() {
  [ "$ABSENCE_LOCK_HELD" -eq 1 ] || return 0
  "$EVIDENCE_STORE_HELPER" gate-release \
    --root "$EVIDENCE_ROOT" \
    --expected-owner "$EVIDENCE_OWNER_EXPECTED" \
    --out "$ABSENCE_EVIDENCE_SOURCE" \
    --token "$ABSENCE_LOCK_TOKEN" >/dev/null 2>&1 || true
  ABSENCE_LOCK_HELD=0
  ABSENCE_LOCK_TOKEN=""
}

release_absence_lock_for_verdict() {
  local release_output release_rc
  [ "$ABSENCE_LOCK_HELD" -eq 1 ] || return 0
  release_output="$("$EVIDENCE_STORE_HELPER" gate-release \
    --root "$EVIDENCE_ROOT" \
    --expected-owner "$EVIDENCE_OWNER_EXPECTED" \
    --out "$ABSENCE_EVIDENCE_SOURCE" \
    --token "$ABSENCE_LOCK_TOKEN" 2>&1)"
  release_rc=$?
  # Never retry from the EXIT trap after a verdict-bound release attempt.  A
  # failed release intentionally leaves the durable lock as a fail-closed
  # marker for every later gate/observer invocation.
  ABSENCE_LOCK_HELD=0
  ABSENCE_LOCK_TOKEN=""
  if [ "$release_rc" -ne 0 ]; then
    ABSENCE_RELEASE_DETAIL="${release_output:-release_or_directory_fsync_failed}"
    return 1
  fi
  return 0
}

cleanup_gate_state() {
  cleanup_snapshot
  cleanup_absence_lock
}

finish() {
  local verdict="GATE=FAIL"
  cleanup_snapshot
  if [ "$ABSENCE_LOCK_HELD" -eq 1 ]; then
    if release_absence_lock_for_verdict; then
      pass absence_lock_release "owned evidence lock durably released"
    else
      fail absence_lock_release "$ABSENCE_RELEASE_DETAIL"
    fi
  fi
  trap - EXIT
  [ "$FAILURES" -eq 0 ] && verdict="GATE=PASS"
  say "$verdict FAILURES=$FAILURES SCOPE=${EXPECTED_SCOPE:-unset} HOST=${HOST_ID:-unset}"
  log_evidence "{\"ts_utc\":\"$(date -u +%FT%TZ)\",\"host\":\"${HOST_ID:-unset}\",\"scope\":\"${EXPECTED_SCOPE:-unset}\",\"verdict\":\"$verdict\",\"failures\":$FAILURES}"
  [ "$FAILURES" -eq 0 ] && exit 0
  exit 1
}

trap cleanup_gate_state EXIT
trap 'fail gate_signal "received HUP"; finish' HUP
trap 'fail gate_signal "received INT"; finish' INT
trap 'fail gate_signal "received TERM"; finish' TERM

token_field() { # $1=field  — token은 "key: value" 텍스트. 첫 매치만 사용.
  awk -F': *' -v k="$1" '$1==k {sub(/^[^:]*: */,""); print; exit}' "$TOKEN_FILE" 2>/dev/null
}

iso_to_s() { # canonical UTC YYYY-MM-DDTHH:MM:SSZ + calendar roundtrip only
  local value="$1" epoch roundtrip
  printf '%s' "$value" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' \
    || return 1
  epoch="$(date -u -d "$value" +%s 2>/dev/null)" || return 1
  roundtrip="$(date -u -d "@$epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || return 1
  [ "$roundtrip" = "$value" ] || return 1
  printf '%s\n' "$epoch"
}

canonical_uint() {
  local value="$1" LC_ALL=C
  printf '%s' "$value" | grep -Eq '^(0|[1-9][0-9]*)$' || return 1
  [ "${#value}" -lt 19 ] && return 0
  [ "${#value}" -gt 19 ] && return 1
  [[ "$value" < "9223372036854775808" ]]
}

canonical_minutes() {
  canonical_uint "$1" && [ "$1" -le 153722867280912930 ]
}

canonical_hours() {
  canonical_uint "$1" && [ "$1" -le 2562047788015215 ]
}

is_regular_file() { [ -f "$1" ] && [ ! -L "$1" ]; }

file_sha256() {
  sha256sum "$1" 2>/dev/null | awk 'NR==1 {print $1}'
}

text_operator_count() {
  "$INPUT_SNAPSHOT_HELPER" identity-count-text --value "$1" 2>/dev/null || true
}

json_operator_count() {
  "$INPUT_SNAPSHOT_HELPER" identity-count-json --file "$1" 2>/dev/null || true
}

curl_bn() { curl -fsS --max-time 10 "$@" 2>/dev/null; }

# ── 0. gate.env ──────────────────────────────────────────────────────────────
if [ ! -f "$GATE_ENV" ]; then
  fail gate_env_present "missing $GATE_ENV"
  finish
fi
# shellcheck disable=SC1090
. "$GATE_ENV"


# The first Primary activation happens before the Standby region is built.
# Failover and failback continue through the full evidence-bound gate below.
if [ "${EXPECTED_SCOPE:-}" = "vc-start-initial" ]; then
  exec /usr/local/sbin/vc-initial-gate
fi

REQUIRED_VARS="HOST_ID COUNTERPARTY_HOST_ID NETWORK EXPECTED_SCOPE BN_URL EXPECTED_GENESIS_VALIDATORS_ROOT PUBKEYS_FILE CHECKLIST_FILE TOKEN_FILE SEALED_MARKER VC_DATADIR DP_UNIT_PATHS"
missing=""
for v in $REQUIRED_VARS; do
  eval "val=\${$v:-}"
  [ -z "$val" ] && missing="$missing $v"
done
if [ -n "$missing" ]; then
  fail gate_env_complete "missing vars:$missing"
  finish
fi
pass gate_env_complete

MIN_ABSENT_EPOCHS="${MIN_ABSENT_EPOCHS:-3}"
ABSENCE_MAX_AGE_MIN="${ABSENCE_MAX_AGE_MIN:-30}"
ABSENCE_EVIDENCE="${ABSENCE_EVIDENCE:-/var/lib/ethereum-maintenance/evidence/current-absence.json}"
SP_IMPORT_MARKER="${SP_IMPORT_MARKER:-/etc/ethereum/failover/sp-import-approved}"
GATE_LOG="${GATE_LOG:-/var/lib/ethereum-maintenance/evidence/vc-gate.log}"
PGREP_PATTERN="${PGREP_PATTERN:-lighthouse([[:space:]]+|.*/)vc}"
FENCE_EVIDENCE="${FENCE_EVIDENCE:-/etc/ethereum/failover/source-fence.json}"  # P1-04
FENCE_MAX_AGE_MIN="${FENCE_MAX_AGE_MIN:-15}"
LEASE_TIMER="${LEASE_TIMER:-vc-lease.timer}"
LEASE_MAX_TOKEN_HOURS="${LEASE_MAX_TOKEN_HOURS:-168}"   # token 최대 수명(self-fence 실효성)
SYSTEMCTL="${SYSTEMCTL_BIN:-systemctl}"
TOKEN_OWNER_EXPECTED="${TOKEN_OWNER_EXPECTED:-root}"   # 운영 기본 root. gate.env 자체가 root 소유이므로 안전.
INPUT_OWNER_EXPECTED="${INPUT_OWNER_EXPECTED:-$TOKEN_OWNER_EXPECTED}"
INPUT_SNAPSHOT_HELPER="${INPUT_SNAPSHOT_HELPER:-/usr/local/sbin/vc-input-snapshot}"
INPUT_SNAPSHOT_BASE="${INPUT_SNAPSHOT_BASE:-/run/ethereum-vc-gate}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-/var/lib/ethereum-maintenance/evidence}"
EVIDENCE_OWNER_EXPECTED="${EVIDENCE_OWNER_EXPECTED:-$INPUT_OWNER_EXPECTED}"
EVIDENCE_STORE_HELPER="${EVIDENCE_STORE_HELPER:-/usr/local/sbin/vc-evidence-store}"
SP_STATE_OWNER_EXPECTED="${SP_STATE_OWNER_EXPECTED:-root}"
SP_DB_OWNER_EXPECTED="${SP_DB_OWNER_EXPECTED:-lighthouse-validator}"

if ! canonical_uint "$MIN_ABSENT_EPOCHS" || [ "$MIN_ABSENT_EPOCHS" -lt 1 ]; then
  fail absence_min_epochs "MIN_ABSENT_EPOCHS must be a canonical safe integer >=1"
  finish
fi

case "$EXPECTED_SCOPE" in
  vc-start-primary|vc-start-failover) pass scope_recognized "$EXPECTED_SCOPE" ;;
  *) fail scope_recognized "unknown EXPECTED_SCOPE=$EXPECTED_SCOPE"; finish ;;
esac

# collector와 gate는 같은 OUT lock을 사용한다. stale/in-progress lock이 있으면
# 성공 종료하지 않은 absence JSON일 수 있으므로 원본 evidence를 열기 전에 거부한다.
ABSENCE_EVIDENCE_SOURCE="$ABSENCE_EVIDENCE"
ABSENCE_CHECKSUM_SOURCE="$ABSENCE_EVIDENCE.sha256"
ABSENCE_LOCK_DIR="$ABSENCE_EVIDENCE.observe.lock"
SP_IMPORT_MARKER_SOURCE="$SP_IMPORT_MARKER"
SP_DB_SOURCE="$VC_DATADIR/validators/slashing_protection.sqlite"
if [ ! -x "$EVIDENCE_STORE_HELPER" ]; then
  fail absence_observation_idle "evidence store helper unavailable: $EVIDENCE_STORE_HELPER"
  finish
elif ABSENCE_LOCK_TOKEN="$("$EVIDENCE_STORE_HELPER" gate-acquire \
    --root "$EVIDENCE_ROOT" \
    --expected-owner "$EVIDENCE_OWNER_EXPECTED" \
    --out "$ABSENCE_EVIDENCE_SOURCE" 2>&1)" \
    && [ -n "$ABSENCE_LOCK_TOKEN" ]; then
  ABSENCE_LOCK_HELD=1
  pass absence_observation_idle
else
  fail absence_observation_idle "collector active/crashed, output untrusted, or stale lock present: ${ABSENCE_LOCK_TOKEN:-$ABSENCE_LOCK_DIR}"
  ABSENCE_LOCK_TOKEN=""
  finish
fi

# 안전 입력과 absence completion checksum을 한 번만 열어 private snapshot으로
# 고정한다. 이후 hash/parse/loop는 exact-byte 복제본만 사용하므로 원본 path
# 교체가 한 gate 실행에 섞이지 않는다.
snapshot_args=(create
  --base-dir "$INPUT_SNAPSHOT_BASE"
  --expected-owner "$INPUT_OWNER_EXPECTED"
  --token "$TOKEN_FILE"
  --fence "$FENCE_EVIDENCE"
  --absence "$ABSENCE_EVIDENCE"
  --absence-checksum "$ABSENCE_CHECKSUM_SOURCE"
  --checklist "$CHECKLIST_FILE"
  --pubkeys "$PUBKEYS_FILE"
  --sp-marker "$SP_IMPORT_MARKER_SOURCE"
  --sp-marker-owner "$SP_STATE_OWNER_EXPECTED"
  --sp-db "$SP_DB_SOURCE"
  --sp-db-owner "$SP_DB_OWNER_EXPECTED")
if [ -n "${VC_GATE_TEST_SNAPSHOT_READY_FILE:-}" ] || [ -n "${VC_GATE_TEST_SNAPSHOT_CONTINUE_FILE:-}" ]; then
  snapshot_args+=(
    --test-ready-file "${VC_GATE_TEST_SNAPSHOT_READY_FILE:-}"
    --test-continue-file "${VC_GATE_TEST_SNAPSHOT_CONTINUE_FILE:-}")
fi
if [ -n "${VC_GATE_TEST_INPUT_OPEN_READY_FILE:-}" ] || [ -n "${VC_GATE_TEST_INPUT_OPEN_CONTINUE_FILE:-}" ]; then
  snapshot_args+=(
    --test-open-ready-file "${VC_GATE_TEST_INPUT_OPEN_READY_FILE:-}"
    --test-open-continue-file "${VC_GATE_TEST_INPUT_OPEN_CONTINUE_FILE:-}")
fi
if [ ! -x "$INPUT_SNAPSHOT_HELPER" ]; then
  snapshot_result="SNAPSHOT=FAIL INPUT=HELPER REASON=unavailable"
  snapshot_rc=1
else
  snapshot_result="$("$INPUT_SNAPSHOT_HELPER" "${snapshot_args[@]}" 2>&1)"
  snapshot_rc=$?
fi
if [ "$snapshot_rc" -ne 0 ] || [ -z "$snapshot_result" ]; then
  fail input_snapshot "$snapshot_result"
  case "$snapshot_result" in
    *INPUT=TOKEN_FILE*) fail token_present "secure snapshot rejected TOKEN_FILE" ;;
    *INPUT=FENCE_EVIDENCE*) fail source_fence_evidence "secure snapshot rejected FENCE_EVIDENCE" ;;
    *INPUT=ABSENCE_EVIDENCE*) fail absence_evidence_present "secure snapshot rejected ABSENCE_EVIDENCE" ;;
    *INPUT=ABSENCE_CHECKSUM*) fail absence_evidence_checksum "secure snapshot rejected ABSENCE_CHECKSUM" ;;
    *INPUT=CHECKLIST_FILE*) fail token_checklist_hash "secure snapshot rejected CHECKLIST_FILE" ;;
    *INPUT=PUBKEYS_FILE*) fail expected_pubkeys_nonempty "secure snapshot rejected PUBKEYS_FILE" ;;
    *INPUT=SP_IMPORT_MARKER*) fail sp_import_approved "secure snapshot rejected SP_IMPORT_MARKER" ;;
    *INPUT=SP_DB*) fail sp_db_present "secure snapshot rejected SP_DB" ;;
  esac
  finish
fi
SNAPSHOT_DIR="$snapshot_result"
TOKEN_FILE="$SNAPSHOT_DIR/approval.token"
FENCE_EVIDENCE="$SNAPSHOT_DIR/source-fence.json"
ABSENCE_EVIDENCE="$SNAPSHOT_DIR/current-absence.json"
ABSENCE_CHECKSUM="$SNAPSHOT_DIR/current-absence.json.sha256"
CHECKLIST_FILE="$SNAPSHOT_DIR/checklist.txt"
PUBKEYS_FILE="$SNAPSHOT_DIR/expected-pubkeys.txt"
SP_IMPORT_MARKER="$SNAPSHOT_DIR/sp-import-approved"
SP_DB_SNAPSHOT_SHA_FILE="$SNAPSHOT_DIR/slashing-protection.sqlite.sha256"
SP_MARKER_SNAPSHOT_SHA="$(file_sha256 "$SP_IMPORT_MARKER")"
SP_DB_SNAPSHOT_SHA="$(sed -n '1p' "$SP_DB_SNAPSHOT_SHA_FILE" 2>/dev/null)"
pass input_snapshot "authorization inputs, SP metadata, DB digest, and absence checksum pinned in private snapshot"

if [ "${VC_GATE_TEST_FATAL_AFTER_SNAPSHOT:-0}" = "1" ]; then
  unset VC_GATE_TEST_FATAL_SENTINEL
  : "$VC_GATE_TEST_FATAL_SENTINEL"
fi

# 기대 validator가 0개면 coverage/liveness의 빈 집합이 거짓 PASS가 된다.
expected_pubkey_count=0
expected_pubkeys_valid=1
if ! is_regular_file "$PUBKEYS_FILE"; then
  fail expected_pubkeys_nonempty "PUBKEYS_FILE must be an actual regular file: $PUBKEYS_FILE"
  expected_pubkeys_valid=0
else
  while IFS= read -r pk; do
    [ -z "$pk" ] && continue
    case "$pk" in \#*) continue ;; esac
    expected_pubkey_count=$((expected_pubkey_count + 1))
    if ! printf '%s' "$pk" | grep -Eq '^0x[0-9a-fA-F]{96}$'; then
      expected_pubkeys_valid=0
    fi
  done <"$PUBKEYS_FILE"
  [ "$expected_pubkey_count" -ge 1 ] \
    && pass expected_pubkeys_nonempty "count=$expected_pubkey_count" \
    || fail expected_pubkeys_nonempty "no validator pubkeys configured"
fi
[ "$expected_pubkey_count" -eq 1 ] \
  && pass expected_pubkeys_exactly_one "count=1" \
  || fail expected_pubkeys_exactly_one "v2.3 requires exactly one validator pubkey (count=$expected_pubkey_count)"
[ "$expected_pubkeys_valid" -eq 1 ] \
  && pass expected_pubkeys_valid \
  || fail expected_pubkeys_valid "every pubkey must be 0x + 96 hex characters"

# ── 1. SEALED 마커 부재 (SEALED가 있으면 어떤 token으로도 기동 불가) ──────────
if [ -e "$SEALED_MARKER" ]; then
  fail sealed_marker_absent "host is SEALED ($SEALED_MARKER)"
else
  pass sealed_marker_absent
fi

# ── 2. approval token 존재·소유·권한 (D4: 사람이 만든 파일) ───────────────────
if [ ! -f "$TOKEN_FILE" ] || [ -L "$TOKEN_FILE" ]; then
  fail token_present "missing or symlink: $TOKEN_FILE"
else
  perm="$(stat -c '%a' "$TOKEN_FILE" 2>/dev/null || echo 999)"
  owner="$(stat -c '%U' "$TOKEN_FILE" 2>/dev/null || echo unknown)"
  if [ "$owner" != "$TOKEN_OWNER_EXPECTED" ]; then
    fail token_ownership "owner=$owner (expected $TOKEN_OWNER_EXPECTED)"
  elif [ "$perm" != "400" ] && [ "$perm" != "600" ]; then
    fail token_permissions "mode=$perm (0400/0600 required)"
  else
    pass token_present
  fi
fi

# ── 3. token 필드 검증 (호스트·네트워크·scope·evidence 바인딩) ───────────────
t_incident=""
t_sha=""
t_fence_sha=""
t_fence_sha_valid=0
issued_s=""
expires_s=""
f_checked_s=""
f_at_s=""
done_s=""
if [ -f "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ]; then
  t_host="$(token_field host_id)"
  t_net="$(token_field network)"
  t_scope="$(token_field scope)"
  t_issued="$(token_field issued_at_utc)"
  t_expires="$(token_field expires_at_utc)"
  t_ops="$(token_field operators)"
  t_incident="$(token_field incident_id)"
  t_sha="$(token_field checklist_sha256)"
  t_fence_sha="$(token_field source_fence_sha256)"

  [ "$t_host" = "$HOST_ID" ]        && pass token_host_binding || fail token_host_binding "token=$t_host env=$HOST_ID"
  [ "$t_net" = "$NETWORK" ]         && pass token_network_binding || fail token_network_binding "token=$t_net env=$NETWORK"
  [ "$t_scope" = "$EXPECTED_SCOPE" ] && pass token_scope_binding || fail token_scope_binding "token=$t_scope expected=$EXPECTED_SCOPE"

  if ! is_regular_file "$CHECKLIST_FILE"; then
    fail token_checklist_hash "CHECKLIST_FILE must be an actual regular file: $CHECKLIST_FILE"
  elif ! printf '%s' "$t_sha" | grep -Eq '^[0-9a-f]{64}$'; then
    fail token_checklist_hash "checklist_sha256 must be 64 lowercase hex"
  else
    checklist_actual_sha="$(file_sha256 "$CHECKLIST_FILE")"
    if [ -n "$checklist_actual_sha" ] && [ "$t_sha" = "$checklist_actual_sha" ]; then
      pass token_checklist_hash "$checklist_actual_sha"
    else
      fail token_checklist_hash "token=$t_sha actual=${checklist_actual_sha:-unavailable}"
    fi
  fi

  [ -n "$t_incident" ] \
    && pass token_incident_id "$t_incident" \
    || fail token_incident_id "incident_id is required"

  if printf '%s' "$t_fence_sha" | grep -Eq '^[0-9a-f]{64}$'; then
    t_fence_sha_valid=1
  else
    fail token_source_fence_hash "source_fence_sha256 must be 64 lowercase hex"
  fi

  # 2인 원칙: trim/case-fold 뒤 서로 다른 non-empty 기명 2인 이상
  op_count="$(text_operator_count "$t_ops")"
  if [ "${op_count:-0}" -ge 2 ]; then pass token_two_person; else fail token_two_person "distinct_operators<2 ('$t_ops')"; fi

  # 각 필드는 정확히 한 번 canonical parser를 통과시켜 저장한다.
  issued_s="$(iso_to_s "$t_issued")" ; expires_s="$(iso_to_s "$t_expires")"
  if [ -z "$issued_s" ] || [ -z "$expires_s" ]; then
    fail token_time_window "issued/expires must be canonical UTC YYYY-MM-DDTHH:MM:SSZ"
  elif ! canonical_hours "$LEASE_MAX_TOKEN_HOURS"; then
    fail token_lease_bound "LEASE_MAX_TOKEN_HOURS must be canonical unsigned decimal"
  elif [ "$issued_s" -gt "$NOW_EPOCH_S" ]; then
    fail token_time_window "issued_at in the future"
  elif [ "$expires_s" -le "$issued_s" ]; then
    fail token_time_window "expires_at must be later than issued_at"
  elif [ "$expires_s" -le "$NOW_EPOCH_S" ]; then
    fail token_time_window "token expired at $t_expires"
  elif [ $((expires_s - NOW_EPOCH_S)) -gt $((LEASE_MAX_TOKEN_HOURS * 3600)) ] \
    || [ $((expires_s - issued_s)) -gt $((LEASE_MAX_TOKEN_HOURS * 3600)) ]; then
    # P1-04: 수명이 너무 길면 self-fence lease가 사실상 무력해진다.
    fail token_lease_bound "remaining or total token lifetime > ${LEASE_MAX_TOKEN_HOURS}h — 재발급 필요"
  else
    pass token_time_window "expires $t_expires"
    pass token_lease_bound "remaining and total lifetime <= ${LEASE_MAX_TOKEN_HOURS}h"
  fi

fi

# ── 4. 이 호스트에 이미 떠 있는 VC 프로세스 금지 ──────────────────────────────
pgrep -f "$PGREP_PATTERN" >/dev/null 2>&1
pgrep_rc=$?
case "$pgrep_rc" in
  0) fail no_duplicate_vc_process "running: $(pgrep -af "$PGREP_PATTERN" 2>/dev/null | head -3 | tr '\n' ';')" ;;
  1) pass no_duplicate_vc_process ;;
  *) fail no_duplicate_vc_process "pgrep probe error rc=$pgrep_rc" ;;
esac

# ── 5. BN 도달·동기화·낙관적 아님·EL 온라인 ───────────────────────────────────
syncing_json="$(curl_bn "$BN_URL/eth/v1/node/syncing")"
if [ -z "$syncing_json" ]; then
  fail bn_reachable "no response from $BN_URL"
else
  pass bn_reachable
  if printf '%s' "$syncing_json" | jq -e '
      (.data | type) == "object" and
      (.data.is_syncing | type) == "boolean" and
      (.data.is_optimistic | type) == "boolean" and
      (.data.el_offline | type) == "boolean" and
      (.data.is_syncing == false) and
      (.data.is_optimistic == false) and
      (.data.el_offline == false)
    ' >/dev/null 2>&1; then
    pass bn_synced
  else
    fail bn_synced "all readiness flags must be present boolean false"
  fi
fi

# ── 6. 네트워크 정체성 (다른 체인의 BN을 보고 판단하는 사고 차단) ─────────────
genesis_json="$(curl_bn "$BN_URL/eth/v1/beacon/genesis")"
gvr="$(printf '%s' "$genesis_json" | jq -r '.data.genesis_validators_root // empty' 2>/dev/null)"
if [ "$gvr" = "$EXPECTED_GENESIS_VALIDATORS_ROOT" ]; then
  pass network_identity
else
  fail network_identity "bn=$gvr expected=$EXPECTED_GENESIS_VALIDATORS_ROOT"
fi

# ── 7. stopped VC의 slashing protection DB를 snapshot에서 고정 ─────────────
if printf '%s' "$SP_DB_SNAPSHOT_SHA" | grep -Eq '^[0-9a-f]{64}$'; then
  pass sp_db_present "$SP_DB_SOURCE"
else
  fail sp_db_present "missing/empty/unhashable $SP_DB_SOURCE — 빈 DB로 기동 금지, RB-01 F3 참조"
fi

# ── 7b. self-fence lease 타이머가 무장되어 있는지 (P1-04) ─────────────────────
# 이 타이머가 없으면 "연락 두절 = 무기한 서명 가능"이 되어 시나리오 B에서
# 검증 가능한 펜스를 만들 수 없다. 따라서 기동 전 무장 상태를 강제한다.
lease_state="$("$SYSTEMCTL" is-enabled "$LEASE_TIMER" 2>/dev/null || true)"
lease_active="$("$SYSTEMCTL" is-active "$LEASE_TIMER" 2>/dev/null || true)"
if [ "$lease_state" = "enabled" ] && [ "$lease_active" = "active" ]; then
  pass lease_timer_armed "$LEASE_TIMER"
else
  fail lease_timer_armed "$LEASE_TIMER is-enabled=$lease_state is-active=$lease_active (자기봉인 미무장 — 기동 불가)"
fi

# ── 8. doppelganger 플래그가 유닛에 실재하는지 (D7 방어선 무결성) ─────────────
dp_found=0
for p in $DP_UNIT_PATHS; do
  if [ -f "$p" ] && grep -q 'enable-doppelganger-protection' "$p"; then dp_found=1; fi
done
[ "$dp_found" -eq 1 ] && pass dp_flag_in_unit || fail dp_flag_in_unit "flag not found in: $DP_UNIT_PATHS"

# ── 9~11. 두 scope 공통: counterparty fence + SP + absence + liveness ─────────
# 방향은 EXPECTED_SCOPE/COUNTERPARTY_HOST_ID로만 정해지며 안전 검사는 대칭이다.

# 9. SP import JSON metadata. Boolean/empty marker는 current incident 권한이 아니다.
sp_schema="$(jq -r '.schema // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_incident="$(jq -r '.incident_id // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_target_host="$(jq -r '.target_host_id // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_checklist_sha="$(jq -r '.checklist_sha256 // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_db_sha="$(jq -r '.sp_db_sha256 // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_recorded_at="$(jq -r '.recorded_at_utc // empty' "$SP_IMPORT_MARKER" 2>/dev/null)"
sp_ops="$(json_operator_count "$SP_IMPORT_MARKER")"
sp_recorded_s=""

if jq -e '
    type == "object" and
    (.schema | type == "string") and
    (.incident_id | type == "string" and length > 0) and
    (.target_host_id | type == "string" and length > 0) and
    (.checklist_sha256 | type == "string") and
    (.sp_db_sha256 | type == "string") and
    (.recorded_at_utc | type == "string") and
    (.operators | type == "array" and all(.[]; type == "string"))
  ' "$SP_IMPORT_MARKER" >/dev/null 2>&1 \
  && [ "$sp_schema" = "sp-state-evidence/v1" ]; then
  pass sp_import_approved "bound metadata schema=sp-state-evidence/v1"
else
  fail sp_import_approved "missing/malformed SP metadata or wrong schema"
fi

if [ -n "$t_incident" ] && [ "$sp_incident" = "$t_incident" ] \
   && [ "$sp_target_host" = "$HOST_ID" ] \
   && printf '%s' "$sp_checklist_sha" | grep -Eq '^[0-9a-f]{64}$' \
   && [ "$sp_checklist_sha" = "$t_sha" ]; then
  pass sp_import_binding "incident=$sp_incident host=$sp_target_host checklist=$sp_checklist_sha"
else
  fail sp_import_binding "marker incident/host/checklist does not match current token and target"
fi

if printf '%s' "$sp_db_sha" | grep -Eq '^[0-9a-f]{64}$' \
   && [ "$sp_db_sha" = "$SP_DB_SNAPSHOT_SHA" ]; then
  pass sp_db_hash "$sp_db_sha"
else
  fail sp_db_hash "marker=${sp_db_sha:-invalid} current_snapshot=${SP_DB_SNAPSHOT_SHA:-unavailable}"
fi

[ "${sp_ops:-0}" -ge 2 ] 2>/dev/null \
  && pass sp_state_two_person \
  || fail sp_state_two_person "distinct_operators<2"

sp_recorded_s="$(iso_to_s "$sp_recorded_at")"
if [ -n "$sp_recorded_s" ] && [ -n "$issued_s" ] \
   && [ "$sp_recorded_s" -ge "$issued_s" ] \
   && [ "$sp_recorded_s" -le "$NOW_EPOCH_S" ]; then
  pass sp_state_time "$sp_recorded_at"
else
  fail sp_state_time "recorded_at_utc must be canonical, >= token issued_at, and <= current time"
fi

# 9b. source fence 증거 — token/checklist와 exact-byte hash로 결합한다.
if ! is_regular_file "$FENCE_EVIDENCE"; then
  fail source_fence_evidence "must be an actual regular file: $FENCE_EVIDENCE"
else
  pass source_fence_evidence "$FENCE_EVIDENCE"

  if [ "$t_fence_sha_valid" -eq 1 ]; then
    fence_actual_sha="$(file_sha256 "$FENCE_EVIDENCE")"
    if [ -n "$fence_actual_sha" ] && [ "$t_fence_sha" = "$fence_actual_sha" ]; then
      pass token_source_fence_hash "$fence_actual_sha"
    else
      fail token_source_fence_hash "token=$t_fence_sha actual=${fence_actual_sha:-unavailable}"
    fi
  fi

  f_schema="$(jq -r '.schema // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_network="$(jq -r '.network // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_scope="$(jq -r '.target_scope // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_host="$(jq -r '.source_host_id // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_incident="$(jq -r '.incident_id // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_checklist_sha="$(jq -r '.checklist_sha256 // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_type="$(jq -r '.fence_type // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_provider_state="$(jq -r '.provider_state // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_vc_state="$(jq -r '.vc_process_state // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_ops="$(json_operator_count "$FENCE_EVIDENCE")"
  f_at="$(jq -r '.fenced_at_utc // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_checked="$(jq -r '.checked_at_utc // empty' "$FENCE_EVIDENCE" 2>/dev/null)"
  f_ref="$(jq -r '.evidence_ref // empty' "$FENCE_EVIDENCE" 2>/dev/null)"

  [ "$f_schema" = "source-fence-evidence/v2" ] \
    && pass source_fence_schema \
    || fail source_fence_schema "schema=$f_schema expected=source-fence-evidence/v2"
  [ "$f_network" = "$NETWORK" ] \
    && pass source_fence_network \
    || fail source_fence_network "evidence=$f_network env=$NETWORK"
  [ "$f_scope" = "$EXPECTED_SCOPE" ] \
    && pass source_fence_scope \
    || fail source_fence_scope "evidence=$f_scope expected=$EXPECTED_SCOPE"

  if [ "$COUNTERPARTY_HOST_ID" = "$HOST_ID" ]; then
    fail source_fence_host "COUNTERPARTY_HOST_ID must differ from HOST_ID ($HOST_ID)"
  elif [ "$f_host" = "$COUNTERPARTY_HOST_ID" ]; then
    pass source_fence_host "$f_host"
  else
    fail source_fence_host "evidence=$f_host expected_counterparty=$COUNTERPARTY_HOST_ID"
  fi

  [ -n "$t_incident" ] && [ "$f_incident" = "$t_incident" ] \
    && pass source_fence_incident "$f_incident" \
    || fail source_fence_incident "evidence=$f_incident token=$t_incident"
  [ -n "$t_sha" ] && [ "$f_checklist_sha" = "$t_sha" ] \
    && pass source_fence_checklist_hash "$f_checklist_sha" \
    || fail source_fence_checklist_hash "evidence=$f_checklist_sha token=$t_sha"

  case "$f_type" in
    provider-stopped|host-power-off) pass source_fence_type "$f_type" ;;
    *) fail source_fence_type "fence_type must be provider-stopped|host-power-off (got '$f_type')" ;;
  esac
  [ "$f_provider_state" = "fenced" ] \
    && pass source_fence_provider_state \
    || fail source_fence_provider_state "provider_state=$f_provider_state expected=fenced"
  [ "$f_vc_state" = "absent" ] \
    && pass source_fence_vc_state \
    || fail source_fence_vc_state "vc_process_state=$f_vc_state expected=absent"
  [ "${f_ops:-0}" -ge 2 ] 2>/dev/null \
    && pass source_fence_two_person \
    || fail source_fence_two_person "distinct_operators<2"
  [ -n "$f_ref" ] \
    && pass source_fence_ref "$f_ref" \
    || fail source_fence_ref "evidence_ref 비어 있음(명령 출력/티켓/로그 식별자 필요)"

  f_checked_s="$(iso_to_s "$f_checked")"
  if [ -z "$f_checked_s" ]; then
    fail source_fence_checked_at "checked_at_utc unparseable"
  elif [ "$f_checked_s" -gt "$NOW_EPOCH_S" ]; then
    fail source_fence_checked_at "checked_at_utc in the future"
  elif ! canonical_minutes "$FENCE_MAX_AGE_MIN"; then
    fail source_fence_checked_at "FENCE_MAX_AGE_MIN must be a non-negative integer"
  elif [ $((NOW_EPOCH_S - f_checked_s)) -gt $((FENCE_MAX_AGE_MIN * 60)) ]; then
    fail source_fence_checked_at "age_s=$((NOW_EPOCH_S - f_checked_s)) max_min=$FENCE_MAX_AGE_MIN"
  else
    pass source_fence_checked_at "age_s=$((NOW_EPOCH_S - f_checked_s))"
  fi

  f_at_s="$(iso_to_s "$f_at")"
  if [ -z "$f_at_s" ]; then
    fail source_fence_fenced_at "fenced_at_utc unparseable"
  elif [ "$f_at_s" -gt "$NOW_EPOCH_S" ]; then
    fail source_fence_fenced_at "fenced_at_utc in the future"
  elif [ -z "$f_checked_s" ] || [ "$f_at_s" -gt "$f_checked_s" ]; then
    fail source_fence_fenced_at "fenced_at_utc must be <= checked_at_utc"
  else
    pass source_fence_fenced_at "$f_at"
  fi
fi

# 10. observe-absence.sh 증거 — hard fence의 보강 증거이며 두 scope 모두 필수다.
a_checksum="$(sed -n '1p' "$ABSENCE_CHECKSUM" 2>/dev/null)"
a_checksum_lines="$(awk 'END {print NR + 0}' "$ABSENCE_CHECKSUM" 2>/dev/null)"
a_actual_sha="$(file_sha256 "$ABSENCE_EVIDENCE")"
if [ "$a_checksum_lines" = "1" ] \
   && printf '%s' "$a_checksum" | grep -Eq '^[0-9a-f]{64}$' \
   && [ -n "$a_actual_sha" ] && [ "$a_checksum" = "$a_actual_sha" ]; then
  pass absence_evidence_checksum "$a_actual_sha"
else
  fail absence_evidence_checksum "checksum=${a_checksum:-invalid} actual=${a_actual_sha:-unavailable}"
fi

if [ ! -f "$ABSENCE_EVIDENCE" ]; then
  fail absence_evidence_present "missing $ABSENCE_EVIDENCE — absence observation 미이행"
else
  a_schema="$(jq -r '.schema // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_result="$(jq -r '.result // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_net="$(jq -r '.network // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_gvr="$(jq -r '.genesis_validators_root // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_incident="$(jq -r '.incident // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_last_checked="$(jq -r '.last_checked_epoch // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_finalized_end="$(jq -r '.finalized_epoch_end // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"
  a_epochs="$("$INPUT_SNAPSHOT_HELPER" json-uint-field \
    --file "$ABSENCE_EVIDENCE" --field consecutive_absent_epochs 2>/dev/null)" \
    || a_epochs=""
  a_done="$(jq -r '.completed_at_utc // empty' "$ABSENCE_EVIDENCE" 2>/dev/null)"

  [ "$a_schema" = "absence-evidence/v1" ] \
    && pass absence_schema \
    || fail absence_schema "schema=$a_schema expected=absence-evidence/v1"
  [ "$a_result" = "ABSENCE_OBSERVED" ] && pass absence_result "negative evidence only" || fail absence_result "result=$a_result (기대: ABSENCE_OBSERVED)"
  [ "$a_net" = "$NETWORK" ] && pass absence_network || fail absence_network "evidence=$a_net env=$NETWORK"
  [ "$a_gvr" = "$EXPECTED_GENESIS_VALIDATORS_ROOT" ] \
    && pass absence_network_identity \
    || fail absence_network_identity "evidence=$a_gvr expected=$EXPECTED_GENESIS_VALIDATORS_ROOT"
  [ -n "$t_incident" ] && [ "$a_incident" = "$t_incident" ] \
    && pass absence_incident "$a_incident" \
    || fail absence_incident "evidence=$a_incident token=$t_incident"
  if jq -e '
      (.last_checked_epoch | type == "number") and
      (.finalized_epoch_end | type == "number") and
      (.last_checked_epoch >= 0) and (.finalized_epoch_end >= 0) and
      (.last_checked_epoch == (.last_checked_epoch | floor)) and
      (.finalized_epoch_end == (.finalized_epoch_end | floor)) and
      (.last_checked_epoch <= .finalized_epoch_end)
    ' "$ABSENCE_EVIDENCE" >/dev/null 2>&1; then
    pass absence_finalized_boundary "$a_last_checked<=$a_finalized_end"
  else
    fail absence_finalized_boundary "last_checked=$a_last_checked finalized_end=$a_finalized_end"
  fi
  if [ -n "$a_epochs" ] && canonical_uint "$a_epochs" \
     && [ "$a_epochs" -ge "$MIN_ABSENT_EPOCHS" ]; then
    pass absence_min_epochs "$a_epochs>=$MIN_ABSENT_EPOCHS"
  else
    fail absence_min_epochs "observed=$a_epochs required>=$MIN_ABSENT_EPOCHS"
  fi
  done_s="$(iso_to_s "$a_done")"
  if ! canonical_minutes "$ABSENCE_MAX_AGE_MIN"; then
    fail absence_freshness "ABSENCE_MAX_AGE_MIN must be a safe canonical unsigned decimal"
  elif [ -n "$done_s" ] && [ "$done_s" -le "$NOW_EPOCH_S" ] \
     && [ $((NOW_EPOCH_S - done_s)) -le $((ABSENCE_MAX_AGE_MIN * 60)) ]; then
    pass absence_freshness "age_s=$((NOW_EPOCH_S - done_s))"
  else
    fail absence_freshness "evidence stale, future, or unparseable (completed_at_utc=$a_done, max ${ABSENCE_MAX_AGE_MIN}min)"
  fi
  # 커버리지: 기대 pubkey 전부가 증거에 포함되어야 함
  if [ -f "$PUBKEYS_FILE" ]; then
    cover_fail=0
    while IFS= read -r pk; do
      [ -z "$pk" ] && continue
      case "$pk" in \#*) continue ;; esac
      if ! jq -e --arg pk "$pk" '.pubkeys | index($pk)' "$ABSENCE_EVIDENCE" >/dev/null 2>&1; then
        cover_fail=1
      fi
    done <"$PUBKEYS_FILE"
    [ "$cover_fail" -eq 0 ] && pass absence_pubkey_coverage || fail absence_pubkey_coverage "some expected pubkeys absent from evidence"
  else
    fail absence_pubkey_coverage "missing PUBKEYS_FILE=$PUBKEYS_FILE"
  fi
fi

# 11. 최종 라이브니스 재확인 — index 집합을 먼저 고정한 뒤 최신 head에서
# 완료 epoch를 선택한다. 느린 pubkey lookup 전에 읽은 stale head는 사용하지 않는다.
indices='[]'
resolve_fail=0
resolve_detail=""
while IFS= read -r pk; do
  [ -z "$pk" ] && continue
  case "$pk" in \#*) continue ;; esac
  vjson="$(curl_bn "$BN_URL/eth/v1/beacon/states/head/validators?id=$pk")"
  vidx="$(printf '%s' "$vjson" | jq -er '
    select((.data | type) == "array" and (.data | length) == 1)
    | .data[0].index
    | select(type == "string")
    | select(test("^(0|[1-9][0-9]*)$"))
    ' 2>/dev/null)" || vidx=""
  if [ -z "$vidx" ] || ! canonical_uint "$vidx"; then
    resolve_fail=1
    resolve_detail="noncanonical validator index"
  elif printf '%s' "$indices" | jq -e --arg i "$vidx" 'index($i) != null' >/dev/null 2>&1; then
    resolve_fail=1
    resolve_detail="duplicate validator index $vidx"
  else
    indices="$(printf '%s' "$indices" | jq -c --arg i "$vidx" '. + [$i]')"
  fi
done <"$PUBKEYS_FILE"

if [ "$resolve_fail" -eq 1 ]; then
  fail final_liveness_recheck "pubkey->index resolution failed: ${resolve_detail:-unknown}"
else
  head_json="$(curl_bn "$BN_URL/eth/v1/beacon/headers/head")"
  head_slot="$(printf '%s' "$head_json" | jq -er '
    .data.header.message.slot
    | select(type == "string")
    | select(test("^(0|[1-9][0-9]*)$"))
    ' 2>/dev/null)" || head_slot=""
  if [ -z "$head_slot" ] || ! canonical_uint "$head_slot"; then
    fail final_liveness_recheck "head slot must be a canonical unsigned-decimal string within signed arithmetic range"
  else
    cur_epoch=$((head_slot / 32))
    if [ "$cur_epoch" -lt 1 ]; then
      fail final_liveness_recheck "head epoch must be >= 1"
    else
      prev_epoch=$((cur_epoch - 1))
      live_json="$(curl -fsS --max-time 10 -H 'Content-Type: application/json' \
        -d "$indices" "$BN_URL/eth/v1/validator/liveness/$prev_epoch" 2>/dev/null)"
      if [ -z "$live_json" ]; then
        fail final_liveness_recheck "liveness endpoint no response (epoch $prev_epoch)"
      elif ! printf '%s' "$live_json" | jq -e --argjson expected "$indices" '
          if (.data | type) != "array" then false
          else
            .data as $rows
            | (($expected | type) == "array")
              and (($expected | length) > 0)
              and (($rows | length) == ($expected | length))
              and all($rows[];
                ((.index | type) == "string")
                and (.index | test("^(0|[1-9][0-9]*)$"))
                and ((.is_live | type) == "boolean"))
              and (([$rows[].index] | sort) == ($expected | sort))
              and (([$rows[].index] | unique | length) == ($rows | length))
          end
        ' >/dev/null 2>&1; then
        fail final_liveness_recheck "response must contain every requested index exactly once with boolean is_live"
      else
        any_live="$(printf '%s' "$live_json" | jq -r '[.data[]?.is_live] | any')"
        if [ "$any_live" = "false" ]; then
          pass final_liveness_recheck "epoch=$prev_epoch all_absent"
        else
          fail final_liveness_recheck "validator ALIVE on chain at epoch $prev_epoch — 좀비/이중 서명 위험(시나리오 C), 즉시 중단"
        fi
      fi
    fi
  fi
fi

# 12. 실제 ExecStart 직전 시간·SEALED·SP artifact 경계 재확인.
FINAL_NOW_EPOCH_S="$(date -u +%s 2>/dev/null || true)"
if ! canonical_uint "$FINAL_NOW_EPOCH_S"; then
  fail final_time_window "cannot read final current time"
  fail final_fence_freshness "cannot read final current time"
  fail final_absence_freshness "cannot read final current time"
else
  if [ -n "$issued_s" ] && [ -n "$expires_s" ] \
     && [ "$issued_s" -le "$FINAL_NOW_EPOCH_S" ] \
     && [ "$expires_s" -gt "$FINAL_NOW_EPOCH_S" ]; then
    pass final_time_window "token valid at final use"
  else
    fail final_time_window "token invalid or expired at final use"
  fi

  if [ -n "$f_checked_s" ] && canonical_minutes "$FENCE_MAX_AGE_MIN" \
     && [ "$f_checked_s" -le "$FINAL_NOW_EPOCH_S" ] \
     && [ $((FINAL_NOW_EPOCH_S - f_checked_s)) -le $((FENCE_MAX_AGE_MIN * 60)) ]; then
    pass final_fence_freshness "age_s=$((FINAL_NOW_EPOCH_S - f_checked_s))"
  else
    fail final_fence_freshness "source fence stale, future, or invalid at final use"
  fi

  if [ -n "$done_s" ] && canonical_minutes "$ABSENCE_MAX_AGE_MIN" \
     && [ "$done_s" -le "$FINAL_NOW_EPOCH_S" ] \
     && [ $((FINAL_NOW_EPOCH_S - done_s)) -le $((ABSENCE_MAX_AGE_MIN * 60)) ]; then
    pass final_absence_freshness "age_s=$((FINAL_NOW_EPOCH_S - done_s))"
  else
    fail final_absence_freshness "absence evidence stale, future, or invalid at final use"
  fi
fi

final_marker_sha="$($INPUT_SNAPSHOT_HELPER file-sha256 \
  --file "$SP_IMPORT_MARKER_SOURCE" \
  --expected-owner "$SP_STATE_OWNER_EXPECTED" \
  --input-name SP_IMPORT_MARKER \
  --require-nonempty 2>/dev/null)" || final_marker_sha=""
final_sp_db_sha="$($INPUT_SNAPSHOT_HELPER file-sha256 \
  --file "$SP_DB_SOURCE" \
  --expected-owner "$SP_DB_OWNER_EXPECTED" \
  --input-name SP_DB \
  --require-nonempty 2>/dev/null)" || final_sp_db_sha=""
if [ -n "$SP_MARKER_SNAPSHOT_SHA" ] \
   && [ "$final_marker_sha" = "$SP_MARKER_SNAPSHOT_SHA" ] \
   && [ -n "$sp_db_sha" ] \
   && [ "$final_sp_db_sha" = "$sp_db_sha" ] \
   && [ -n "$sp_recorded_s" ] \
   && canonical_uint "$FINAL_NOW_EPOCH_S" \
   && [ "$sp_recorded_s" -le "$FINAL_NOW_EPOCH_S" ]; then
  pass final_sp_state "marker exact bytes and live SP DB remain bound"
else
  fail final_sp_state "marker replaced, SP DB mutated, metadata time invalid, or secure final hash unavailable"
fi

if [ -e "$SEALED_MARKER" ]; then
  fail final_sealed_state "host became SEALED during gate checks ($SEALED_MARKER)"
else
  pass final_sealed_state
fi

finish
