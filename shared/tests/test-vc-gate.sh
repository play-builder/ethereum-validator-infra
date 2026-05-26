#!/usr/bin/env bash
# test-vc-gate.sh — vc-gate.sh 행동 테스트. 부정 경로(기동 거부)가 주인공이다.
# 원칙: 각 case는 real gate의 exit/output을 검증하고 하나의 잘못된 안전 전제만 바꾼다.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GATE="$HERE/../scripts/vc-gate.sh"
WORK="$(mktemp -d)"; WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
PASS_N=0; FAIL_N=0
ok()  { echo "TEST PASS: $1"; PASS_N=$((PASS_N+1)); }
bad() { echo "TEST FAIL: $1"; FAIL_N=$((FAIL_N+1)); }

GVR="0x212f13fc4df078b6cb7db228f1c8307566dcecf900867401a92023d7ba99cb5f"
PK="0x$(printf 'ab%.0s' $(seq 48))"
PK2="0x$(printf 'cd%.0s' $(seq 48))"
REAL_DATE_BIN="$(command -v date)"

# ── 외부 경계 stub: BN/systemd/process 결과만 제어하고 real vc-gate를 실행한다 ─
mkdir -p "$WORK/bin" "$WORK/mock"
cat > "$WORK/bin/curl" <<'MOCK'
#!/usr/bin/env bash
url=""; for a in "$@"; do case "$a" in http*) url="$a";; esac; done
d="$MOCK_BN_DIR"
case "$url" in
  */eth/v1/node/syncing)
    [ -z "${GATE_TEST_CREATE_SEALED_FILE:-}" ] || : > "$GATE_TEST_CREATE_SEALED_FILE"
    cat "$d/syncing.json" ;;
  */eth/v1/beacon/genesis)                 cat "$d/genesis.json" ;;
  */eth/v1/beacon/headers/head)            cat "$d/head.json" ;;
  */eth/v1/beacon/states/head/validators*)
    if [[ "$url" == *"$PK2"* ]] && [ -s "$d/validators-pk2.json" ]; then
      cat "$d/validators-pk2.json"
    else
      cat "$d/validators.json"
    fi
    [ ! -f "$d/advance-head-on-validator" ] || cp "$d/head-next.json" "$d/head.json" ;;
  */eth/v1/validator/liveness/*)
    live_epoch="$(cat "$d/live-on-epoch" 2>/dev/null || true)"
    if [ -n "$live_epoch" ] && [ "${url##*/}" = "$live_epoch" ]; then
      echo '{"data":[{"index":"12345","is_live":true}]}'
    else
      cat "$d/liveness.json"
    fi ;;
  *) exit 22 ;;
esac
MOCK
chmod +x "$WORK/bin/curl"

cat > "$WORK/bin/systemctl" <<'SC'
#!/usr/bin/env bash
case "$1" in
  is-enabled) cat "$LEASE_ENABLED_FILE" 2>/dev/null || echo disabled ;;
  is-active)  cat "$LEASE_ACTIVE_FILE"  2>/dev/null || echo inactive ;;
esac
SC
chmod +x "$WORK/bin/systemctl"

# macOS sandbox의 process visibility와 무관하게 pgrep의 세 가지 계약을 재현한다.
cat > "$WORK/bin/pgrep" <<'PG'
#!/usr/bin/env bash
rc="$(cat "$PGREP_RC_FILE" 2>/dev/null || echo 1)"
if [ "${1:-}" = "-af" ] && [ "$rc" -eq 0 ]; then
  echo "4242 /opt/lighthouse vc --network hoodi"
fi
exit "$rc"
PG
chmod +x "$WORK/bin/pgrep"

# Final-use freshness tests advance only the gate's second exact "now" read.
# All timestamp parsing/fixture date calls still use the real GNU date binary.
cat > "$WORK/bin/date" <<'DATE'
#!/usr/bin/env bash
if [ "$#" -eq 2 ] && [ "$1" = "-u" ] && [ "$2" = "+%s" ] \
   && [ -n "${GATE_TEST_DATE_OFFSET_ON_SECOND_S:-}" ] \
   && [ -n "${GATE_TEST_DATE_COUNTER_FILE:-}" ]; then
  count="$(cat "$GATE_TEST_DATE_COUNTER_FILE" 2>/dev/null || echo 0)"
  count=$((count + 1))
  printf '%s\n' "$count" > "$GATE_TEST_DATE_COUNTER_FILE"
  now="$($REAL_DATE_BIN -u +%s)"
  if [ "$count" -ge 2 ]; then
    printf '%s\n' "$((now + GATE_TEST_DATE_OFFSET_ON_SECOND_S))"
  else
    printf '%s\n' "$now"
  fi
else
  exec "$REAL_DATE_BIN" "$@"
fi
DATE
chmod +x "$WORK/bin/date"

export PATH="$WORK/bin:$PATH"
export REAL_DATE_BIN
export PK PK2
export MOCK_BN_DIR="$WORK/mock"
export LEASE_ENABLED_FILE="$WORK/lease_enabled" LEASE_ACTIVE_FILE="$WORK/lease_active"
export PGREP_RC_FILE="$WORK/pgrep_rc"

arm_lease()    { echo enabled > "$LEASE_ENABLED_FILE"; echo active > "$LEASE_ACTIVE_FILE"; }
disarm_lease() { echo disabled > "$LEASE_ENABLED_FILE"; echo inactive > "$LEASE_ACTIVE_FILE"; }
set_pgrep_rc() { printf '%s\n' "$1" > "$PGREP_RC_FILE"; }

healthy_mocks() {
  cat > "$WORK/mock/syncing.json" <<EOF
{"data":{"is_syncing":false,"is_optimistic":false,"el_offline":false}}
EOF
  cat > "$WORK/mock/genesis.json" <<EOF
{"data":{"genesis_validators_root":"$GVR"}}
EOF
  cat > "$WORK/mock/head.json" <<EOF
{"data":{"header":{"message":{"slot":"64000"}}}}
EOF
  cat > "$WORK/mock/validators.json" <<EOF
{"data":[{"index":"12345"}]}
EOF
  cat > "$WORK/mock/validators-pk2.json" <<EOF
{"data":[{"index":"67890"}]}
EOF
  cat > "$WORK/mock/liveness.json" <<EOF
{"data":[{"index":"12345","is_live":false}]}
EOF
}

file_sha256() { sha256sum "$1" | awk '{print $1}'; }
refresh_absence_checksum() { file_sha256 "$WORK/ev/absence.json" > "$WORK/ev/absence.json.sha256"; }

# ── 공통 픽스처 ──────────────────────────────────────────────────────────────
mk_env() { # $1=scope
  local scope="$1" counterparty
  ETC="$WORK/etc"
  rm -rf "$ETC" "$WORK/datadir" "$WORK/ev" "$WORK/unit.service" "$WORK/snapshot-base"
  mkdir -p "$ETC" "$WORK/datadir/validators" "$WORK/ev"
  chmod 700 "$WORK/ev"
  case "$scope" in
    vc-start-primary)  counterparty="aws-standby-01" ;;
    vc-start-failover) counterparty="aws-primary-01" ;;
    *)                 counterparty="unknown-counterparty" ;;
  esac
  echo "$PK" > "$ETC/expected-pubkeys.txt"
  printf 'INC-TEST-001 reviewed checklist\n' > "$ETC/checklist.txt"
  printf 'sqlite-not-empty' > "$WORK/datadir/validators/slashing_protection.sqlite"
  cat > "$WORK/unit.service" <<'U'
ExecStart=/opt/lighthouse vc --enable-doppelganger-protection
U
  cat > "$ETC/gate.env" <<EOF
HOST_ID=test-host
COUNTERPARTY_HOST_ID=$counterparty
NETWORK=hoodi
EXPECTED_SCOPE=$scope
BN_URL=http://127.0.0.1:5052
EXPECTED_GENESIS_VALIDATORS_ROOT=$GVR
PUBKEYS_FILE=$ETC/expected-pubkeys.txt
CHECKLIST_FILE=$ETC/checklist.txt
TOKEN_FILE=$ETC/approval.token
SEALED_MARKER=$ETC/SEALED
VC_DATADIR=$WORK/datadir
DP_UNIT_PATHS=$WORK/unit.service
SP_IMPORT_MARKER=$ETC/sp-import-approved
SP_STATE_OWNER_EXPECTED=$(id -un)
SP_DB_OWNER_EXPECTED=$(id -un)
ABSENCE_EVIDENCE=$WORK/ev/absence.json
ABSENCE_MAX_AGE_MIN=30
MIN_ABSENT_EPOCHS=3
GATE_LOG=$WORK/ev/gate.log
PGREP_PATTERN=fake-lighthouse-vc-marker
TOKEN_OWNER_EXPECTED=$(id -un)
INPUT_OWNER_EXPECTED=$(id -un)
INPUT_SNAPSHOT_HELPER=$HERE/../scripts/vc-input-snapshot.py
INPUT_SNAPSHOT_BASE=$WORK/snapshot-base
EVIDENCE_ROOT=$WORK/ev
EVIDENCE_OWNER_EXPECTED=$(id -un)
EVIDENCE_STORE_HELPER=$HERE/../scripts/vc-evidence-store.py
FENCE_EVIDENCE=$ETC/source-fence.json
FENCE_MAX_AGE_MIN=15
LEASE_TIMER=vc-lease.timer
LEASE_MAX_TOKEN_HOURS=168
EOF
  arm_lease
  set_pgrep_rc 1
}

mk_fence() { # $1=scope $2=fence_type
  local scope="$1" fence_type="${2:-provider-stopped}" counterparty checklist_sha
  counterparty="$(awk -F= '$1=="COUNTERPARTY_HOST_ID" {print $2}' "$ETC/gate.env")"
  checklist_sha="$(file_sha256 "$ETC/checklist.txt")"
  cat > "$ETC/source-fence.json" <<EOF
{
  "schema": "source-fence-evidence/v2",
  "network": "hoodi",
  "target_scope": "$scope",
  "source_host_id": "$counterparty",
  "incident_id": "INC-TEST-001",
  "checklist_sha256": "$checklist_sha",
  "fence_type": "$fence_type",
  "provider_state": "fenced",
  "vc_process_state": "absent",
  "operators": ["Alice Kim", "Bob Park"],
  "fenced_at_utc": "$(date -u -d '-10 minutes' +%FT%TZ)",
  "checked_at_utc": "$(date -u -d '-1 minute' +%FT%TZ)",
  "evidence_ref": "INC-TEST-001/provider-fence.txt"
}
EOF
}

mk_token() { # $1=scope $2=expires_offset_s
  local scope="$1" expires_offset_s="${2:-86400}"
  cat > "$ETC/approval.token" <<EOF
token_version: 2
host_id: test-host
network: hoodi
scope: $scope
issued_at_utc: $(date -u -d '-1 hour' +%FT%TZ)
expires_at_utc: $(date -u -d "$expires_offset_s seconds" +%FT%TZ)
operators: Alice Kim, Bob Park
incident_id: INC-TEST-001
checklist_sha256: $(file_sha256 "$ETC/checklist.txt")
source_fence_sha256: $(file_sha256 "$ETC/source-fence.json")
counterparty_token_state: revoked
EOF
  chmod 600 "$ETC/approval.token"
}

mk_absence() { # $1=epochs $2=age_min
  cat > "$WORK/ev/absence.json" <<EOF
{"schema":"absence-evidence/v1","result":"ABSENCE_OBSERVED","network":"hoodi",
 "genesis_validators_root":"$GVR","incident":"INC-TEST-001",
 "pubkeys":["$PK"],"validator_indices":["12345"],
 "last_checked_epoch":1998,"finalized_epoch_end":1998,
 "consecutive_absent_epochs":$1,
 "completed_at_utc":"$(date -u -d "-$2 minutes" +%FT%TZ)"}
EOF
  refresh_absence_checksum
}

mk_sp_state() {
  cat > "$ETC/sp-import-approved" <<EOF
{
  "schema": "sp-state-evidence/v1",
  "incident_id": "INC-TEST-001",
  "target_host_id": "test-host",
  "checklist_sha256": "$(file_sha256 "$ETC/checklist.txt")",
  "sp_db_sha256": "$(file_sha256 "$WORK/datadir/validators/slashing_protection.sqlite")",
  "recorded_at_utc": "$(date -u -d '-1 minute' +%FT%TZ)",
  "operators": ["Alice Kim", "Bob Park"]
}
EOF
  chmod 600 "$ETC/sp-import-approved"
}

ready() { # $1=scope $2=fence_type
  mk_env "$1"
  mk_fence "$1" "${2:-provider-stopped}"
  mk_token "$1" 86400
  mk_absence 3 1
  mk_sp_state
}

replace_token_field() { # $1=key $2=value
  local key="$1" value="$2"
  awk -v k="$key" -v v="$value" 'index($0,k ":")==1 {$0=k ": " v} {print}' \
    "$ETC/approval.token" > "$ETC/approval.token.tmp"
  mv "$ETC/approval.token.tmp" "$ETC/approval.token"
  chmod 600 "$ETC/approval.token"
}

delete_token_field() { # $1=key
  local key="$1"
  awk -v k="$key" 'index($0,k ":")!=1 {print}' "$ETC/approval.token" > "$ETC/approval.token.tmp"
  mv "$ETC/approval.token.tmp" "$ETC/approval.token"
  chmod 600 "$ETC/approval.token"
}

refresh_token_fence_hash() {
  replace_token_field source_fence_sha256 "$(file_sha256 "$ETC/source-fence.json")"
}

run_gate() {
  VC_GATE_ENV="$ETC/gate.env" bash "$GATE" > "$WORK/out.txt" 2>&1
  echo $?
}

expect_fail_with() { # $1=name $2=fixed output fragment
  local rc
  rc="$(run_gate)"
  if [ "$rc" = "1" ] && grep -Fq "$2" "$WORK/out.txt"; then
    ok "$1"
  else
    bad "$1 (rc=$rc, wanted=$2)"
    sed -n '1,80p' "$WORK/out.txt"
  fi
}

expect_pass() { # $1=name
  local rc
  rc="$(run_gate)"
  if [ "$rc" = "0" ] && grep -Fq "GATE=PASS" "$WORK/out.txt"; then
    ok "$1"
  else
    bad "$1 (rc=$rc)"
    sed -n '1,100p' "$WORK/out.txt"
  fi
}

mutate_snapshot_token() {
  replace_token_field host_id swapped-after-snapshot
}

mutate_snapshot_fence() {
  jq '.provider_state="swapped-after-snapshot"' "$ETC/source-fence.json" > "$ETC/f.swap"
  mv "$ETC/f.swap" "$ETC/source-fence.json"
}

mutate_snapshot_absence() {
  jq '.result="SWAPPED_AFTER_SNAPSHOT"' "$WORK/ev/absence.json" > "$WORK/ev/a.swap"
  mv "$WORK/ev/a.swap" "$WORK/ev/absence.json"
}

mutate_snapshot_checklist() {
  printf 'swapped-after-snapshot\n' >> "$ETC/checklist.txt"
}

mutate_snapshot_pubkeys() {
  printf 'not-a-pubkey-after-snapshot\n' > "$ETC/expected-pubkeys.txt"
}

mutate_live_sp_db() {
  printf 'mutated-after-snapshot\n' >> "$WORK/datadir/validators/slashing_protection.sqlite"
}

mutate_live_sp_marker() {
  jq '.incident_id="INC-SWAPPED-AFTER-SNAPSHOT"' "$ETC/sp-import-approved" \
    > "$ETC/sp-import-approved.swap"
  mv "$ETC/sp-import-approved.swap" "$ETC/sp-import-approved"
  chmod 600 "$ETC/sp-import-approved"
}

expect_snapshot_inplace_fail() {
  local name="$1" ready_file="$WORK/snapshot-open.ready" continue_file="$WORK/snapshot-open.continue"
  local pid rc marker_seen=0
  rm -f "$ready_file" "$continue_file" "$WORK/out.txt"
  VC_GATE_TEST_INPUT_OPEN_READY_FILE="$ready_file" \
  VC_GATE_TEST_INPUT_OPEN_CONTINUE_FILE="$continue_file" \
    VC_GATE_ENV="$ETC/gate.env" bash "$GATE" > "$WORK/out.txt" 2>&1 &
  pid=$!
  for _ in $(seq 1 500); do
    if [ -f "$ready_file" ]; then marker_seen=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.01
  done
  if [ "$marker_seen" -eq 1 ]; then
    printf 'in-place mutation\n' >> "$ETC/checklist.txt"
    touch "$continue_file"
  fi
  wait "$pid"; rc=$?
  if [ "$marker_seen" -eq 1 ] && [ "$rc" -eq 1 ] \
     && grep -Fq 'source_changed_while_snapshotting' "$WORK/out.txt"; then
    ok "$name"
  else
    bad "$name (rc=$rc open_marker=$marker_seen)"
    sed -n '1,100p' "$WORK/out.txt"
  fi
}

expect_snapshot_swap_pass() { # $1=name $2=mutation function
  local name="$1" mutate="$2" ready_file="$WORK/snapshot.ready" continue_file="$WORK/snapshot.continue"
  local pid rc marker_seen=0 snapshot_private=0 snapshot_path=""
  rm -f "$ready_file" "$continue_file" "$WORK/out.txt"
  VC_GATE_TEST_SNAPSHOT_READY_FILE="$ready_file" \
  VC_GATE_TEST_SNAPSHOT_CONTINUE_FILE="$continue_file" \
    VC_GATE_ENV="$ETC/gate.env" bash "$GATE" > "$WORK/out.txt" 2>&1 &
  pid=$!
  for _ in $(seq 1 500); do
    if [ -f "$ready_file" ]; then marker_seen=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.01
  done
  if [ "$marker_seen" -eq 1 ]; then
    snapshot_path="$(sed -n '1p' "$ready_file")"
    if [ -d "$snapshot_path" ] \
       && [ "$(stat -c '%a' "$snapshot_path")" = "700" ] \
       && [ "$(find "$snapshot_path" -type f -exec stat -c '%a' {} \; | sort -u)" = "400" ]; then
      snapshot_private=1
    fi
    "$mutate"
    touch "$continue_file"
  fi
  wait "$pid"; rc=$?
  if [ "$marker_seen" -eq 1 ] && [ "$snapshot_private" -eq 1 ] \
     && [ "$rc" -eq 0 ] && grep -Fq 'GATE=PASS' "$WORK/out.txt"; then
    ok "$name"
  else
    bad "$name (rc=$rc snapshot_marker=$marker_seen private_0700_0400=$snapshot_private)"
    sed -n '1,100p' "$WORK/out.txt"
  fi
}

expect_snapshot_final_fail() { # $1=name $2=mutation function $3=fixed output fragment
  local name="$1" mutate="$2" wanted="$3"
  local ready_file="$WORK/snapshot.ready" continue_file="$WORK/snapshot.continue"
  local pid rc marker_seen=0
  rm -f "$ready_file" "$continue_file" "$WORK/out.txt"
  VC_GATE_TEST_SNAPSHOT_READY_FILE="$ready_file" \
  VC_GATE_TEST_SNAPSHOT_CONTINUE_FILE="$continue_file" \
    VC_GATE_ENV="$ETC/gate.env" bash "$GATE" > "$WORK/out.txt" 2>&1 &
  pid=$!
  for _ in $(seq 1 500); do
    if [ -f "$ready_file" ]; then marker_seen=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.01
  done
  if [ "$marker_seen" -eq 1 ]; then
    "$mutate"
    touch "$continue_file"
  fi
  wait "$pid"; rc=$?
  if [ "$marker_seen" -eq 1 ] && [ "$rc" -eq 1 ] \
     && grep -Fq "$wanted" "$WORK/out.txt"; then
    ok "$name"
  else
    bad "$name (rc=$rc snapshot_marker=$marker_seen wanted=$wanted)"
    sed -n '1,100p' "$WORK/out.txt"
  fi
}

healthy_mocks

# ── 기본 fail-closed와 Task 2 회귀 계약 ─────────────────────────────────────
ready vc-start-failover; rm "$ETC/approval.token"
expect_fail_with "no token -> refuse" "CHECK=token_present STATUS=FAIL"

ready vc-start-failover; replace_token_field expires_at_utc "$(date -u -d '-1 minute' +%FT%TZ)"
expect_fail_with "expired token -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover; replace_token_field scope vc-start-primary
expect_fail_with "wrong token scope -> refuse" "CHECK=token_scope_binding STATUS=FAIL"

ready vc-start-failover; replace_token_field host_id other-host
expect_fail_with "token for another host -> refuse" "CHECK=token_host_binding STATUS=FAIL"

ready vc-start-failover; touch "$ETC/SEALED"
expect_fail_with "SEALED marker -> refuse" "CHECK=sealed_marker_absent STATUS=FAIL"

ready vc-start-failover; disarm_lease
expect_fail_with "lease timer disarmed -> refuse" "CHECK=lease_timer_armed STATUS=FAIL"

ready vc-start-primary; replace_token_field expires_at_utc "$(date -u -d '+200 hours' +%FT%TZ)"
expect_fail_with "token lifetime > 168h -> refuse" "CHECK=token_lease_bound STATUS=FAIL"

# ── canonical UTC timestamp와 발급~만료 전체 수명 경계 ──────────────────────
ready vc-start-failover; replace_token_field issued_at_utc now
expect_fail_with "relative issued_at_utc -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover; replace_token_field expires_at_utc tomorrow
expect_fail_with "relative expires_at_utc -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover; replace_token_field issued_at_utc '2026-02-30T12:00:00Z'
expect_fail_with "invalid calendar timestamp -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover; replace_token_field issued_at_utc "$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%S+00:00)"
expect_fail_with "noncanonical UTC offset timestamp -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover
replace_token_field issued_at_utc "$(date -u -d '-200 hours' +%FT%TZ)"
replace_token_field expires_at_utc "$(date -u -d '+24 hours' +%FT%TZ)"
expect_fail_with "token total lifetime > 168h with only 24h remaining -> refuse" "CHECK=token_lease_bound STATUS=FAIL"

ready vc-start-failover
replace_token_field issued_at_utc "$(date -u -d '-1 hour' +%FT%TZ)"
replace_token_field expires_at_utc "$(date -u -d '-2 hours' +%FT%TZ)"
expect_fail_with "expires_at_utc not later than issued_at_utc -> refuse" "CHECK=token_time_window STATUS=FAIL"

ready vc-start-failover; rm "$WORK/datadir/validators/slashing_protection.sqlite"
expect_fail_with "missing SP DB -> refuse" "CHECK=sp_db_present STATUS=FAIL"

ready vc-start-failover; sed -i 's/--enable-doppelganger-protection//' "$WORK/unit.service"
expect_fail_with "DP flag removed from unit -> refuse" "CHECK=dp_flag_in_unit STATUS=FAIL"

ready vc-start-failover; echo '{"data":{"is_syncing":true,"is_optimistic":false,"el_offline":false}}' > "$WORK/mock/syncing.json"
expect_fail_with "BN syncing -> refuse" "CHECK=bn_synced STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":{"is_syncing":false,"el_offline":false}}' > "$WORK/mock/syncing.json"
expect_fail_with "BN missing is_optimistic flag -> refuse" "CHECK=bn_synced STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":{"is_syncing":false,"is_optimistic":false}}' > "$WORK/mock/syncing.json"
expect_fail_with "BN missing el_offline flag -> refuse" "CHECK=bn_synced STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":{"is_syncing":"false","is_optimistic":"false","el_offline":"false"}}' > "$WORK/mock/syncing.json"
expect_fail_with "BN readiness flags must be booleans -> refuse" "CHECK=bn_synced STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":{"genesis_validators_root":"0xdeadbeef"}}' > "$WORK/mock/genesis.json"
expect_fail_with "wrong network genesis root -> refuse" "CHECK=network_identity STATUS=FAIL"
healthy_mocks

ready vc-start-failover; : > "$ETC/expected-pubkeys.txt"
expect_fail_with "zero expected pubkeys violates exactly-one contract" "CHECK=expected_pubkeys_exactly_one STATUS=FAIL"

ready vc-start-failover; echo 'not-a-bls-pubkey' > "$ETC/expected-pubkeys.txt"
expect_fail_with "malformed expected pubkey -> refuse" "CHECK=expected_pubkeys_valid STATUS=FAIL"

# ── 대칭 gate: primary도 동일한 counterparty/SP/absence/liveness를 요구 ──────
ready vc-start-primary host-power-off; rm "$ETC/source-fence.json"
expect_fail_with "primary missing source fence -> refuse" "CHECK=source_fence_evidence STATUS=FAIL"

ready vc-start-primary host-power-off; rm "$ETC/sp-import-approved"
expect_fail_with "primary missing SP import marker -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-primary host-power-off; rm "$WORK/ev/absence.json"
expect_fail_with "primary missing absence evidence -> refuse" "CHECK=absence_evidence_present STATUS=FAIL"

ready vc-start-primary host-power-off; echo '{"data":[{"index":"12345","is_live":true}]}' > "$WORK/mock/liveness.json"
expect_fail_with "primary final liveness live -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

# ── gate.env와 token의 evidence binding ─────────────────────────────────────
ready vc-start-failover; sed -i '/^CHECKLIST_FILE=/d' "$ETC/gate.env"
expect_fail_with "missing CHECKLIST_FILE setting -> refuse" "CHECK=gate_env_complete STATUS=FAIL"

ready vc-start-failover; sed -i '/^COUNTERPARTY_HOST_ID=/d' "$ETC/gate.env"
expect_fail_with "missing COUNTERPARTY_HOST_ID setting -> refuse" "CHECK=gate_env_complete STATUS=FAIL"

ready vc-start-failover; replace_token_field operators ' Alice Kim , alice kim '
expect_fail_with "duplicate token operator after trim/case-fold -> refuse" "CHECK=token_two_person STATUS=FAIL"

ready vc-start-failover; replace_token_field operators '김가, 김가'
expect_fail_with "Unicode-normalization-equivalent token operators -> refuse" "CHECK=token_two_person STATUS=FAIL"

ready vc-start-failover; delete_token_field incident_id
expect_fail_with "missing token incident_id -> refuse" "CHECK=token_incident_id STATUS=FAIL"

ready vc-start-failover; delete_token_field source_fence_sha256
expect_fail_with "missing token source_fence_sha256 -> refuse" "CHECK=token_source_fence_hash STATUS=FAIL"

ready vc-start-failover; printf 'post-approval mutation\n' >> "$ETC/checklist.txt"
expect_fail_with "checklist exact bytes changed after approval -> refuse" "CHECK=token_checklist_hash STATUS=FAIL"

ready vc-start-failover; jq '.evidence_ref="INC-TEST-001/replaced.txt"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"
expect_fail_with "source fence exact bytes changed after approval -> refuse" "CHECK=token_source_fence_hash STATUS=FAIL"

ready vc-start-failover; mv "$ETC/checklist.txt" "$ETC/checklist.real"; ln -s "$ETC/checklist.real" "$ETC/checklist.txt"
expect_fail_with "checklist symlink is not an actual regular file -> refuse" "CHECK=token_checklist_hash STATUS=FAIL"

ready vc-start-failover; mv "$ETC/source-fence.json" "$ETC/source-fence.real"; ln -s "$ETC/source-fence.real" "$ETC/source-fence.json"
expect_fail_with "source fence symlink is not an actual regular file -> refuse" "CHECK=source_fence_evidence STATUS=FAIL"

# ── source-fence-evidence/v2 필드와 강한 fence 경계 ─────────────────────────
ready vc-start-failover; jq '.schema="source-fence-evidence/v1"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "wrong source fence schema -> refuse" "CHECK=source_fence_schema STATUS=FAIL"

ready vc-start-failover; jq '.network="mainnet"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "source fence network mismatch -> refuse" "CHECK=source_fence_network STATUS=FAIL"

ready vc-start-failover; jq '.target_scope="vc-start-primary"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "source fence target scope mismatch -> refuse" "CHECK=source_fence_scope STATUS=FAIL"

ready vc-start-failover; jq '.source_host_id="unexpected-other-host"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "source fence counterparty host mismatch -> refuse" "CHECK=source_fence_host STATUS=FAIL"

ready vc-start-failover; sed -i 's/COUNTERPARTY_HOST_ID=aws-primary-01/COUNTERPARTY_HOST_ID=test-host/' "$ETC/gate.env"
expect_fail_with "counterparty host cannot equal current host -> refuse" "CHECK=source_fence_host STATUS=FAIL"

ready vc-start-failover; jq '.incident_id="INC-OTHER"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "source fence incident mismatch -> refuse" "CHECK=source_fence_incident STATUS=FAIL"

ready vc-start-failover; jq '.checklist_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "source fence checklist mismatch -> refuse" "CHECK=source_fence_checklist_hash STATUS=FAIL"

ready vc-start-failover; jq '.operators=[" Alice Kim ","alice kim"]' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "duplicate fence operator after trim/case-fold -> refuse" "CHECK=source_fence_two_person STATUS=FAIL"

ready vc-start-failover; jq '.operators=["김가","김가"]' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "Unicode-normalization-equivalent fence operators -> refuse" "CHECK=source_fence_two_person STATUS=FAIL"

ready vc-start-failover lease-expired
expect_fail_with "lease-expired is not a hard fence -> refuse" "CHECK=source_fence_type STATUS=FAIL"

ready vc-start-failover network-isolated
expect_fail_with "network-isolated is not a hard fence -> refuse" "CHECK=source_fence_type STATUS=FAIL"

ready vc-start-failover; jq '.provider_state="stopping"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "provider_state must be fenced -> refuse" "CHECK=source_fence_provider_state STATUS=FAIL"

ready vc-start-failover; jq '.vc_process_state="unknown"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "vc_process_state must be absent -> refuse" "CHECK=source_fence_vc_state STATUS=FAIL"

ready vc-start-failover; old="$(date -u -d '-16 minutes' +%FT%TZ)"; jq --arg old "$old" '.fenced_at_utc=$old|.checked_at_utc=$old' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "stale checked_at_utc beyond 15 minutes -> refuse" "CHECK=source_fence_checked_at STATUS=FAIL"

ready vc-start-failover; future="$(date -u -d '+5 minutes' +%FT%TZ)"; jq --arg future "$future" '.checked_at_utc=$future' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "future checked_at_utc -> refuse" "CHECK=source_fence_checked_at STATUS=FAIL"

ready vc-start-failover; future="$(date -u -d '+5 minutes' +%FT%TZ)"; jq --arg future "$future" '.fenced_at_utc=$future|.checked_at_utc=$future' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "future fenced_at_utc -> refuse" "CHECK=source_fence_fenced_at STATUS=FAIL"

ready vc-start-failover; fenced="$(date -u -d '-1 minute' +%FT%TZ)"; checked="$(date -u -d '-2 minutes' +%FT%TZ)"; jq --arg fenced "$fenced" --arg checked "$checked" '.fenced_at_utc=$fenced|.checked_at_utc=$checked' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "fenced_at_utc after checked_at_utc -> refuse" "CHECK=source_fence_fenced_at STATUS=FAIL"

ready vc-start-failover; jq '.checked_at_utc="1 minute ago"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "relative fence checked_at_utc -> refuse" "CHECK=source_fence_checked_at STATUS=FAIL"

ready vc-start-failover; jq '.fenced_at_utc="10 minutes ago"' "$ETC/source-fence.json" > "$ETC/f.tmp" && mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
expect_fail_with "relative fence fenced_at_utc -> refuse" "CHECK=source_fence_fenced_at STATUS=FAIL"

# ── local VC absence: pgrep rc=1만 PASS ─────────────────────────────────────
ready vc-start-failover; set_pgrep_rc 0
expect_fail_with "pgrep rc=0 running VC -> refuse" "CHECK=no_duplicate_vc_process STATUS=FAIL"

ready vc-start-failover; set_pgrep_rc 3
expect_fail_with "pgrep rc=3 probe error -> refuse" "CHECK=no_duplicate_vc_process STATUS=FAIL"

# ── absence corroboration 자체의 기존 경계 ──────────────────────────────────
ready vc-start-failover; jq '.schema="absence-evidence/v0"' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "wrong absence evidence schema -> refuse" "CHECK=absence_schema STATUS=FAIL"

ready vc-start-failover; jq '.incident="INC-OTHER"' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "absence incident mismatch -> refuse" "CHECK=absence_incident STATUS=FAIL"

ready vc-start-failover; jq '.genesis_validators_root="0xwrong"' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "absence genesis root mismatch -> refuse" "CHECK=absence_network_identity STATUS=FAIL"

ready vc-start-failover; jq '.last_checked_epoch=2002|.finalized_epoch_end=2001' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "absence last_checked beyond finalized boundary -> refuse" "CHECK=absence_finalized_boundary STATUS=FAIL"

ready vc-start-failover; jq '.last_checked_epoch="not-an-epoch"' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "absence finalized boundary must use integer epochs -> refuse" "CHECK=absence_finalized_boundary STATUS=FAIL"

ready vc-start-failover; mk_absence 3 45
expect_fail_with "stale absence evidence -> refuse" "CHECK=absence_freshness STATUS=FAIL"

ready vc-start-failover; jq '.completed_at_utc="1 minute ago"' "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"; refresh_absence_checksum
expect_fail_with "relative absence completed_at_utc -> refuse" "CHECK=absence_freshness STATUS=FAIL"

ready vc-start-failover; mk_absence 2 1
expect_fail_with "insufficient absent epochs -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

ready vc-start-failover; sed -i 's/^MIN_ABSENT_EPOCHS=.*/MIN_ABSENT_EPOCHS=0/' "$ETC/gate.env"
expect_fail_with "MIN_ABSENT_EPOCHS zero -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

ready vc-start-failover; sed -i 's/^MIN_ABSENT_EPOCHS=.*/MIN_ABSENT_EPOCHS=3+0/' "$ETC/gate.env"
expect_fail_with "MIN_ABSENT_EPOCHS expression -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

ready vc-start-failover; sed -i 's/^MIN_ABSENT_EPOCHS=.*/MIN_ABSENT_EPOCHS=9223372036854775808/' "$ETC/gate.env"
expect_fail_with "MIN_ABSENT_EPOCHS overflow -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

ready vc-start-failover; sed -i 's/^MIN_ABSENT_EPOCHS=.*/MIN_ABSENT_EPOCHS=-1/' "$ETC/gate.env"
expect_fail_with "MIN_ABSENT_EPOCHS negative -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

ready vc-start-failover; sed -i 's/^MIN_ABSENT_EPOCHS=.*/MIN_ABSENT_EPOCHS=03/' "$ETC/gate.env"
expect_fail_with "MIN_ABSENT_EPOCHS leading zero -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"

for value in '"3"' '3.5' '-1' '"3+0"' '9223372036854775808'; do
  ready vc-start-failover
  jq ".consecutive_absent_epochs=$value" "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" \
    && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"
  refresh_absence_checksum
  expect_fail_with "noncanonical consecutive_absent_epochs=$value -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"
done

for value in '3.0' '3e0' 'true'; do
  ready vc-start-failover
  sed -i "s/\"consecutive_absent_epochs\":3,/\"consecutive_absent_epochs\":$value,/" \
    "$WORK/ev/absence.json"
  refresh_absence_checksum
  expect_fail_with "non-integer JSON consecutive_absent_epochs=$value -> refuse" "CHECK=absence_min_epochs STATUS=FAIL"
done

ready vc-start-failover; rm "$WORK/ev/absence.json"
expect_fail_with "failover missing absence evidence -> refuse" "CHECK=absence_evidence_present STATUS=FAIL"

ready vc-start-failover; rm "$WORK/ev/absence.json.sha256"
expect_fail_with "missing absence completion checksum -> refuse" "CHECK=absence_evidence_checksum STATUS=FAIL"

ready vc-start-failover; printf '%064d\n' 0 > "$WORK/ev/absence.json.sha256"
expect_fail_with "absence completion checksum mismatch -> refuse" "CHECK=absence_evidence_checksum STATUS=FAIL"

ready vc-start-failover; mkdir "$WORK/ev/absence.json.observe.lock"
expect_fail_with "active or stale absence observer lock -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"
if [ -d "$WORK/ev/absence.json.observe.lock" ]; then
  ok "failed gate never removes another observer's lock"
else bad "failed gate removed unowned observer lock"; fi

ready vc-start-failover; rm "$ETC/sp-import-approved"
expect_fail_with "failover missing SP import marker -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

# ── SP import authorization is immutable metadata bound to this activation ──
ready vc-start-failover
jq '.incident_id="INC-PAST-001"' "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "past incident SP marker -> refuse" "CHECK=sp_import_binding STATUS=FAIL"

ready vc-start-failover
jq '.target_host_id="other-host"' "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "SP marker target host mismatch -> refuse" "CHECK=sp_import_binding STATUS=FAIL"

ready vc-start-failover
jq '.checklist_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"' \
  "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "SP marker checklist mismatch -> refuse" "CHECK=sp_import_binding STATUS=FAIL"

ready vc-start-failover
printf 'changed-after-import\n' >> "$WORK/datadir/validators/slashing_protection.sqlite"
expect_fail_with "current SP DB differs from approved hash -> refuse" "CHECK=sp_db_hash STATUS=FAIL"

ready vc-start-failover
jq '.operators=["Alice Kim"," alice kim "]' "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "SP marker operators must be distinct -> refuse" "CHECK=sp_state_two_person STATUS=FAIL"

ready vc-start-failover
jq '.recorded_at_utc="2026-08-12T12:00:00+00:00"' "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "SP marker timestamp must be canonical UTC -> refuse" "CHECK=sp_state_time STATUS=FAIL"

ready vc-start-failover
mv "$ETC/sp-import-approved" "$ETC/sp-import-approved.real"
ln -s "$ETC/sp-import-approved.real" "$ETC/sp-import-approved"
expect_fail_with "SP marker symlink -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover
rm "$ETC/sp-import-approved" && mkdir "$ETC/sp-import-approved"
expect_fail_with "SP marker directory -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover; chmod 666 "$ETC/sp-import-approved"
expect_fail_with "group-writable SP marker -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover; : > "$ETC/sp-import-approved"; chmod 600 "$ETC/sp-import-approved"
expect_fail_with "legacy empty SP marker -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover
jq '.schema="sp-state-evidence/v0"' "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "wrong SP metadata schema -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover
sed -i 's/^SP_STATE_OWNER_EXPECTED=.*/SP_STATE_OWNER_EXPECTED=nobody/' "$ETC/gate.env"
expect_fail_with "SP marker owner mismatch -> refuse" "CHECK=sp_import_approved STATUS=FAIL"

ready vc-start-failover
old_recorded="$(date -u -d '-2 hours' +%FT%TZ)"
jq --arg recorded "$old_recorded" '.recorded_at_utc=$recorded' \
  "$ETC/sp-import-approved" > "$ETC/sp.tmp" \
  && mv "$ETC/sp.tmp" "$ETC/sp-import-approved" && chmod 600 "$ETC/sp-import-approved"
expect_fail_with "SP metadata recorded before current token issuance -> refuse" "CHECK=sp_state_time STATUS=FAIL"

ready vc-start-failover; echo '{"data":[{"index":"12345","is_live":true}]}' > "$WORK/mock/liveness.json"
expect_fail_with "failover final liveness live -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":[]}' > "$WORK/mock/liveness.json"
expect_fail_with "empty final liveness response -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":[{"index":"99999","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "final liveness response missing requested index -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":[{"index":"12345"}]}' > "$WORK/mock/liveness.json"
expect_fail_with "final liveness response missing boolean -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":[{"index":"12345","is_live":false},{"index":"12345","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "duplicate final liveness rows -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":[{"index":"12345","is_live":false},{"index":"99999","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "additional final liveness row -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

# ── BN 숫자 문법과 validator index 집합 ────────────────────────────────────
ready vc-start-failover; echo '{"data":{"header":{"message":{"slot":"64000+32"}}}}' > "$WORK/mock/head.json"
expect_fail_with "arithmetic-expression head slot -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover; echo '{"data":{"header":{"message":{"slot":"18446744073709615616"}}}}' > "$WORK/mock/head.json"
expect_fail_with "head slot beyond signed arithmetic range -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover
echo '{"data":{"header":{"message":{"slot":"64032"}}}}' > "$WORK/mock/head-next.json"
touch "$WORK/mock/advance-head-on-validator"
echo 2000 > "$WORK/mock/live-on-epoch"
expect_fail_with "liveness epoch selected after validator index resolution -> refuse newly live validator" "CHECK=final_liveness_recheck STATUS=FAIL"
rm -f "$WORK/mock/advance-head-on-validator" "$WORK/mock/head-next.json" "$WORK/mock/live-on-epoch"
healthy_mocks

ready vc-start-failover
echo '{"data":[{"index":"12345+0"}]}' > "$WORK/mock/validators.json"
echo '{"data":[{"index":"12345+0","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "arithmetic-expression validator index -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover
echo '{"data":[{"index":"012345"}]}' > "$WORK/mock/validators.json"
echo '{"data":[{"index":"012345","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "noncanonical leading-zero validator index -> refuse" "CHECK=final_liveness_recheck STATUS=FAIL"
healthy_mocks

ready vc-start-failover
printf '%s\n%s\n' "$PK" "$PK2" > "$ETC/expected-pubkeys.txt"
jq --arg pk2 "$PK2" '.pubkeys += [$pk2] | .validator_indices += ["67890"]' \
  "$WORK/ev/absence.json" > "$WORK/ev/a.tmp" && mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"
refresh_absence_checksum
echo '{"data":[{"index":"12345","is_live":false},{"index":"67890","is_live":false}]}' > "$WORK/mock/liveness.json"
expect_fail_with "two valid distinct validators violate exactly-one contract" "CHECK=expected_pubkeys_exactly_one STATUS=FAIL"
healthy_mocks

# ── secure coherent input snapshot: owner/mode/symlink/unavailable/swap ─────
ready vc-start-failover; chmod 666 "$ETC/source-fence.json"
expect_fail_with "group-or-other-writable fence input -> refuse" "CHECK=input_snapshot STATUS=FAIL"

ready vc-start-failover
mkdir -m 700 "$WORK/outside-evidence"
cp "$WORK/ev/absence.json" "$WORK/outside-evidence/absence.json"
cp "$WORK/ev/absence.json.sha256" "$WORK/outside-evidence/absence.json.sha256"
outside_json_sha="$(file_sha256 "$WORK/outside-evidence/absence.json")"
sed -i "s#^ABSENCE_EVIDENCE=.*#ABSENCE_EVIDENCE=$WORK/outside-evidence/absence.json#" "$ETC/gate.env"
expect_fail_with "absence path outside EVIDENCE_ROOT -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"
if [ "$outside_json_sha" = "$(file_sha256 "$WORK/outside-evidence/absence.json")" ] \
   && [ ! -e "$WORK/outside-evidence/absence.json.observe.lock" ]; then
  ok "outside evidence pair is byte-identical and never locked"
else bad "outside evidence path was mutated or locked"; fi

ready vc-start-failover
mv "$WORK/ev" "$WORK/symlink-evidence-real"; ln -s "$WORK/symlink-evidence-real" "$WORK/ev"
expect_fail_with "symlinked EVIDENCE_ROOT -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"

ready vc-start-failover; chmod 0770 "$WORK/ev"
expect_fail_with "group-writable EVIDENCE_ROOT -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"

ready vc-start-failover; sed -i 's#^EVIDENCE_STORE_HELPER=.*#EVIDENCE_STORE_HELPER=/definitely/missing/vc-evidence-store#' "$ETC/gate.env"
expect_fail_with "evidence store helper unavailable -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"

ready vc-start-failover; export VC_EVIDENCE_STORE_TEST_NO_NOFOLLOW=1
expect_fail_with "evidence store fails closed when O_NOFOLLOW is unavailable" "CHECK=absence_observation_idle STATUS=FAIL"
unset VC_EVIDENCE_STORE_TEST_NO_NOFOLLOW

export REAL_EVIDENCE_STORE_HELPER="$HERE/../scripts/vc-evidence-store.py"
cat > "$WORK/release-fail-evidence-store" <<'WRAPPER'
#!/usr/bin/env bash
if [ "${1:-}" = "gate-release" ]; then
  echo "EVIDENCE_STORE=FAIL reason=injected_release_failure" >&2
  exit 1
fi
exec "$REAL_EVIDENCE_STORE_HELPER" "$@"
WRAPPER
chmod +x "$WORK/release-fail-evidence-store"
ready vc-start-failover
sed -i "s#^EVIDENCE_STORE_HELPER=.*#EVIDENCE_STORE_HELPER=$WORK/release-fail-evidence-store#" "$ETC/gate.env"
expect_fail_with "absence lock release/fsync failure -> refuse" "CHECK=absence_lock_release STATUS=FAIL"
if [ -d "$WORK/ev/absence.json.observe.lock" ]; then
  ok "release failure leaves durable fail-closed lock"
else
  bad "release failure unexpectedly removed the lock"
fi

ready vc-start-failover; sed -i 's/^INPUT_OWNER_EXPECTED=.*/INPUT_OWNER_EXPECTED=definitely-not-current-user/' "$ETC/gate.env"
expect_fail_with "unexpected input owner -> refuse" "CHECK=input_snapshot STATUS=FAIL"

ready vc-start-failover; mv "$WORK/ev/absence.json" "$WORK/ev/absence.real"; ln -s "$WORK/ev/absence.real" "$WORK/ev/absence.json"
expect_fail_with "absence evidence symlink -> refuse" "CHECK=absence_observation_idle STATUS=FAIL"

ready vc-start-failover; sed -i 's#^INPUT_SNAPSHOT_HELPER=.*#INPUT_SNAPSHOT_HELPER=/definitely/missing/vc-input-snapshot#' "$ETC/gate.env"
expect_fail_with "snapshot helper unavailable -> refuse" "CHECK=input_snapshot STATUS=FAIL"

ready vc-start-failover; export VC_INPUT_SNAPSHOT_TEST_NO_NOFOLLOW=1
expect_fail_with "snapshot fails closed when O_NOFOLLOW is unavailable" "CHECK=input_snapshot STATUS=FAIL"
unset VC_INPUT_SNAPSHOT_TEST_NO_NOFOLLOW

ready vc-start-failover
expect_snapshot_swap_pass "token replacement after snapshot cannot mix fields" mutate_snapshot_token

ready vc-start-failover
expect_snapshot_swap_pass "fence replacement after snapshot cannot mix hash and JSON" mutate_snapshot_fence

ready vc-start-failover
expect_snapshot_swap_pass "absence replacement after snapshot cannot mix evidence" mutate_snapshot_absence

ready vc-start-failover
expect_snapshot_swap_pass "checklist replacement after snapshot cannot mix hash bytes" mutate_snapshot_checklist

ready vc-start-failover
expect_snapshot_swap_pass "pubkeys replacement after snapshot cannot mix coverage/index set" mutate_snapshot_pubkeys

ready vc-start-failover
expect_snapshot_final_fail "SP DB mutation after snapshot -> refuse at final use" \
  mutate_live_sp_db "CHECK=final_sp_state STATUS=FAIL"

ready vc-start-failover
expect_snapshot_final_fail "SP marker replacement after snapshot -> refuse at final use" \
  mutate_live_sp_marker "CHECK=final_sp_state STATUS=FAIL"

ready vc-start-failover
expect_snapshot_inplace_fail "in-place input mutation during snapshot -> refuse"

# SEALED는 snapshot authorization 이후에도 단조로운 긴급 중지 경계다.
ready vc-start-failover
export GATE_TEST_CREATE_SEALED_FILE="$ETC/SEALED"
expect_fail_with "SEALED created during BN checks -> refuse before start" "CHECK=final_sealed_state STATUS=FAIL"
unset GATE_TEST_CREATE_SEALED_FILE

# 시작 시점에는 유효했어도 실제 ExecStart 직전 만료된 안전 증거는 거부한다.
ready vc-start-failover
replace_token_field expires_at_utc "$(date -u -d '+5 seconds' +%FT%TZ)"
rm -f "$WORK/final-token-time.count"
export GATE_TEST_DATE_OFFSET_ON_SECOND_S=10 GATE_TEST_DATE_COUNTER_FILE="$WORK/final-token-time.count"
expect_fail_with "token expires during gate checks -> refuse before start" "CHECK=final_time_window STATUS=FAIL"
unset GATE_TEST_DATE_OFFSET_ON_SECOND_S GATE_TEST_DATE_COUNTER_FILE

ready vc-start-failover
checked="$(date -u -d '895 seconds ago' +%FT%TZ)"
fenced="$(date -u -d '900 seconds ago' +%FT%TZ)"
jq --arg checked "$checked" --arg fenced "$fenced" \
  '.checked_at_utc=$checked | .fenced_at_utc=$fenced' "$ETC/source-fence.json" > "$ETC/f.tmp"
mv "$ETC/f.tmp" "$ETC/source-fence.json"; refresh_token_fence_hash
rm -f "$WORK/final-fence-time.count"
export GATE_TEST_DATE_OFFSET_ON_SECOND_S=10 GATE_TEST_DATE_COUNTER_FILE="$WORK/final-fence-time.count"
expect_fail_with "source fence expires during gate checks -> refuse before start" "CHECK=final_fence_freshness STATUS=FAIL"
unset GATE_TEST_DATE_OFFSET_ON_SECOND_S GATE_TEST_DATE_COUNTER_FILE

ready vc-start-failover
completed="$(date -u -d '1795 seconds ago' +%FT%TZ)"
jq --arg completed "$completed" '.completed_at_utc=$completed' \
  "$WORK/ev/absence.json" > "$WORK/ev/a.tmp"
mv "$WORK/ev/a.tmp" "$WORK/ev/absence.json"
refresh_absence_checksum
rm -f "$WORK/final-absence-time.count"
export GATE_TEST_DATE_OFFSET_ON_SECOND_S=10 GATE_TEST_DATE_COUNTER_FILE="$WORK/final-absence-time.count"
expect_fail_with "absence evidence expires during gate checks -> refuse before start" "CHECK=final_absence_freshness STATUS=FAIL"
unset GATE_TEST_DATE_OFFSET_ON_SECOND_S GATE_TEST_DATE_COUNTER_FILE

ready vc-start-failover
rm -f "$WORK/out.txt"
VC_GATE_TEST_FATAL_AFTER_SNAPSHOT=1 VC_GATE_ENV="$ETC/gate.env" \
  bash "$GATE" > "$WORK/out.txt" 2>&1
rc=$?
leftovers="$(find "$WORK/snapshot-base" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
if [ "$rc" -ne 0 ] && [ "${leftovers:-0}" -eq 0 ] \
   && [ ! -e "$WORK/ev/absence.json.observe.lock" ]; then
  ok "unexpected gate exit cleans private snapshot and absence lock"
else
  bad "unexpected gate exit cleanup (rc=$rc snapshot_leftovers=${leftovers:-unknown})"
  sed -n '1,80p' "$WORK/out.txt"
fi

# ── 두 방향의 유일한 차이는 counterparty/target 방향이다 ────────────────────
ready vc-start-primary host-power-off
expect_pass "primary full-evidence path -> allow"

ready vc-start-failover provider-stopped
delete_token_field counterparty_token_state
expect_pass "failover full-evidence path -> allow"

if [ -s "$WORK/ev/gate.log" ]; then ok "gate evidence log written"; else bad "gate evidence log"; fi

echo "----------------------------------------"
echo "vc-gate tests: PASS=$PASS_N FAIL=$FAIL_N"
[ "$FAIL_N" -eq 0 ]
