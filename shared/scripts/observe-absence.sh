#!/usr/bin/env bash
# observe-absence.sh — F2: 체인 관측 기반 "서명 부재"의 **보강 증거** 수집 (D6)
#
# ⚠ P1-04(감사) 이후 지위 변경: 이 스크립트의 성공 결과는 negative evidence이며
#   원격 서명자의 부재 증명이 아니다. 서명됐지만 아직 블록에 포함되지 않은
#   메시지는 나중에 포함될 수 있다. 전환 허가는 오직 검증 가능한 source fence
#   (provider-stopped / host-power-off)에서 나온다.
#
# 무엇을 하는가:
#   Standby EC2의 로컬 BN(제3의 관측자)에 대해, 대상 validator들이
#   "연속 N개의 완료된 epoch 동안 체인에서 관측되지 않았고(liveness=false)
#    그 사이 finalized checkpoint가 전진했다"는 사실을 실시간으로 관측해
#   JSON 증거 파일로 남긴다. 이 파일이 vc-gate.sh(failover scope)의 입력이다.
#
# 무엇을 절대 하지 않는가:
#   - VC 기동/정지, token 생성, 원격 호스트 접근 (읽기 전용 관측만)
#
# 종료 코드: 0=ABSENCE_OBSERVED(보강 증거 확보)  2=DUPLICATE_SUSPECTED(관측됨—즉시 중단)
#            3=시간 초과/전제 실패(finality 미전진 포함 — 네트워크 수준 사건 의심, A4 동결)
#
# 근거: INC-03(Launchnodes'23 — "unreachable ≠ dead"), INC-10(비최종화 중 전환 금지)
set -u -o pipefail
umask 077

BN_URL="http://127.0.0.1:5052"
PUBKEYS_FILE=""
NETWORK=""
EXPECTED_GVR=""
MIN_EPOCHS=3
MAX_WAIT_MIN=120
OUT=""
INCIDENT="UNSET"
POLL_S="${POLL_S:-30}"   # 테스트에서 단축 가능
EVIDENCE_ROOT="${EVIDENCE_ROOT:-/var/lib/ethereum-maintenance/evidence}"
EVIDENCE_OWNER_EXPECTED="${EVIDENCE_OWNER_EXPECTED:-root}"
EVIDENCE_STORE_HELPER="${EVIDENCE_STORE_HELPER:-/usr/local/sbin/vc-evidence-store}"
STORE_TOKEN=""
LOCK_HELD=0
ARG_ERROR=0
OUT_COUNT=0

usage() {
  cat <<'EOF'
usage: observe-absence.sh --pubkeys-file F --network N --genesis-validators-root 0x... \
         --out FILE [--bn-url URL] [--min-epochs 3] [--max-wait-min 120] [--incident ID]
EOF
  exit 64
}

while [ $# -gt 0 ]; do
  case "$1" in
    --bn-url|--pubkeys-file|--network|--genesis-validators-root|--min-epochs|--max-wait-min|--out|--incident)
      option="$1"
      [ "$option" != "--out" ] || OUT_COUNT=$((OUT_COUNT + 1))
      if [ "$#" -lt 2 ] || [[ "${2:-}" == --* ]]; then
        ARG_ERROR=1
        shift
        continue
      fi
      value="$2"
      case "$option" in
        --bn-url) BN_URL="$value" ;;
        --pubkeys-file) PUBKEYS_FILE="$value" ;;
        --network) NETWORK="$value" ;;
        --genesis-validators-root) EXPECTED_GVR="$value" ;;
        --min-epochs) MIN_EPOCHS="$value" ;;
        --max-wait-min) MAX_WAIT_MIN="$value" ;;
        --out) OUT="$value" ;;
        --incident) INCIDENT="$value" ;;
      esac
      shift 2
      ;;
    *) ARG_ERROR=1; shift ;;
  esac
done
[ "$OUT_COUNT" -eq 1 ] && [ -n "$OUT" ] || usage

store() {
  "$EVIDENCE_STORE_HELPER" "$@" \
    --root "$EVIDENCE_ROOT" \
    --expected-owner "$EVIDENCE_OWNER_EXPECTED" \
    --out "$OUT"
}

cleanup_on_exit() {
  if [ "$LOCK_HELD" -eq 1 ]; then
    store collector-abort --token "$STORE_TOKEN" >/dev/null 2>&1 || true
    LOCK_HELD=0
  fi
}

on_signal() {
  echo "OBSERVE=FAIL reason=signal_$1"
  exit 3
}

trap cleanup_on_exit EXIT
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

test_barrier() { # $1=ready path $2=continue path $3=reason prefix
  local ready="$1" proceed="$2" reason="$3" released=0
  if [ -z "$ready" ] && [ -z "$proceed" ]; then return 0; fi
  if [ -z "$ready" ] || [ -z "$proceed" ]; then
    echo "OBSERVE=FAIL reason=invalid_${reason}_test_barrier"
    return 1
  fi
  (umask 077; set -o noclobber; : > "$ready") 2>/dev/null \
    || { echo "OBSERVE=FAIL reason=${reason}_test_barrier_ready"; return 1; }
  for _ in $(seq 1 500); do
    if [ -f "$proceed" ] && [ ! -L "$proceed" ]; then
      released=1
      break
    fi
    sleep 0.01
  done
  if [ "$released" -ne 1 ]; then
    echo "OBSERVE=FAIL reason=${reason}_test_barrier_timeout"
    return 1
  fi
}

# 임의 경로를 만들거나 지우기 전에 root와 direct basename 경계를 dirfd helper가
# 검증한다. 반복 --out은 어느 대상도 신뢰할 수 없으므로 위에서 즉시 usage다.
if [ ! -x "$EVIDENCE_STORE_HELPER" ]; then
  echo "OBSERVE=FAIL reason=evidence_store_helper_unavailable"
  exit 3
fi
if ! store validate; then
  echo "OBSERVE=FAIL reason=untrusted_output_boundary"
  exit 64
fi

# lock 생성과 이전 pair 폐기는 각각 evidence directory fsync 뒤에만 완료된다.
# SIGKILL/crash가 남긴 durable lock은 gate와 후속 observer를 모두 fail closed한다.
if ! STORE_TOKEN="$(store collector-begin)" || [ -z "$STORE_TOKEN" ]; then
  echo "OBSERVE=FAIL reason=evidence_store_begin"
  exit 3
fi
LOCK_HELD=1

[ "$ARG_ERROR" -eq 0 ] || usage
[ -n "$PUBKEYS_FILE" ] && [ -n "$NETWORK" ] && [ -n "$EXPECTED_GVR" ] || usage
[ -f "$PUBKEYS_FILE" ] || { echo "OBSERVE=FAIL reason=missing_pubkeys_file"; exit 3; }

cbn() { curl -fsS --max-time 10 "$@" 2>/dev/null; }
now_utc() { date -u +%FT%TZ; }
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

DEADLINE_START_S="$(date -u +%s 2>/dev/null || true)"
deadline_max_minutes=0
if canonical_uint "$DEADLINE_START_S"; then
  deadline_max_minutes=$(((9223372036854775807 - DEADLINE_START_S) / 60))
fi
if ! canonical_uint "$MIN_EPOCHS" || [ "$MIN_EPOCHS" -lt 1 ] \
   || ! canonical_minutes "$MAX_WAIT_MIN" || [ "$MAX_WAIT_MIN" -lt 1 ] \
   || [ "$MAX_WAIT_MIN" -gt "$deadline_max_minutes" ]; then
  echo "OBSERVE=FAIL reason=invalid_numeric_arguments"
  exit 3
fi

# v2.3의 validator/interchange 검증기는 정확히 한 record만 지원한다. 관측기도
# 같은 cardinality를 BN 조회 전에 강제해 다중 집합이 권한 증거가 되지 않게 한다.
configured_pubkey_count="$(awk '
  /^[[:space:]]*$/ {next}
  /^[[:space:]]*#/ {next}
  {count++}
  END {print count + 0}
' "$PUBKEYS_FILE" 2>/dev/null)" || configured_pubkey_count=""
if [ "$configured_pubkey_count" != "1" ]; then
  echo "OBSERVE=FAIL reason=expected_pubkeys_exactly_one count=${configured_pubkey_count:-unavailable}"
  exit 3
fi

# ── 전제 1: BN 동기화·정체성 ────────────────────────────────────────────────
sy="$(cbn "$BN_URL/eth/v1/node/syncing")" || true
[ -n "$sy" ] || { echo "OBSERVE=FAIL reason=bn_unreachable url=$BN_URL"; exit 3; }
if ! printf '%s' "$sy" | jq -e '
    (.data | type) == "object" and
    (.data.is_syncing | type) == "boolean" and
    (.data.is_optimistic | type) == "boolean" and
    (.data.el_offline | type) == "boolean" and
    (.data.is_syncing == false) and
    (.data.is_optimistic == false) and
    (.data.el_offline == false)
  ' >/dev/null 2>&1; then
  echo "OBSERVE=FAIL reason=bn_not_ready detail=readiness_flags_must_be_present_boolean_false"
  exit 3
fi
genesis_json="$(cbn "$BN_URL/eth/v1/beacon/genesis")" || true
gvr="$(printf '%s' "$genesis_json" | jq -er '
  .data.genesis_validators_root | select(type == "string")
  ' 2>/dev/null)" || gvr=""
[ "$gvr" = "$EXPECTED_GVR" ] || { echo "OBSERVE=FAIL reason=network_identity got=$gvr"; exit 3; }
echo "PRECHECK=bn_ready_and_identity STATUS=PASS"

# ── 전제 2: pubkey -> validator index ───────────────────────────────────────
PUBKEYS=()
INDICES=()
while IFS= read -r pk; do
  [ -z "$pk" ] && continue
  case "$pk" in \#*) continue ;; esac
  vjson="$(cbn "$BN_URL/eth/v1/beacon/states/head/validators?id=$pk")" || true
  vidx="$(printf '%s' "$vjson" | jq -er '
    select((.data | type) == "array" and (.data | length) == 1)
    | .data[0].index
    | select(type == "string")
    | select(test("^(0|[1-9][0-9]*)$"))
    ' 2>/dev/null)" || vidx=""
  [ -n "$vidx" ] && canonical_uint "$vidx" \
    || { echo "OBSERVE=FAIL reason=index_resolution pubkey=$pk detail=noncanonical_or_malformed"; exit 3; }
  if [ "${#INDICES[@]}" -gt 0 ] \
     && printf '%s\n' "${INDICES[@]}" | grep -Fxq "$vidx"; then
    echo "OBSERVE=FAIL reason=index_resolution pubkey=$pk detail=duplicate_index index=$vidx"
    exit 3
  fi
  PUBKEYS+=("$pk"); INDICES+=("$vidx")
  echo "RESOLVED pubkey=${pk:0:18}.. index=$vidx"
done <"$PUBKEYS_FILE"
[ "${#INDICES[@]}" -eq 1 ] \
  || { echo "OBSERVE=FAIL reason=expected_pubkeys_exactly_one count=${#INDICES[@]}"; exit 3; }
IDX_JSON="$(printf '%s\n' "${INDICES[@]}" | jq -R . | jq -cs .)"

head_slot() {
  cbn "$BN_URL/eth/v1/beacon/headers/head" | jq -er '
    .data.header.message.slot
    | select(type == "string")
    | select(test("^(0|[1-9][0-9]*)$"))
  ' 2>/dev/null
}
finalized_epoch() {
  cbn "$BN_URL/eth/v1/beacon/states/head/finality_checkpoints" | jq -er '
    .data.finalized.epoch
    | select(type == "string")
    | select(test("^(0|[1-9][0-9]*)$"))
  ' 2>/dev/null
}
liveness_response() { # $1=epoch
  cbn -H 'Content-Type: application/json' -d "$IDX_JSON" \
    "$BN_URL/eth/v1/validator/liveness/$1"
}

start_fin="$(finalized_epoch)"; hs="$(head_slot)"
[ -n "$start_fin" ] && canonical_uint "$start_fin" \
  && [ -n "$hs" ] && canonical_uint "$hs" \
  || { echo "OBSERVE=FAIL reason=cannot_read_chain"; exit 3; }
start_epoch=$((hs / 32))
echo "BASELINE head_epoch=$start_epoch finalized_epoch=$start_fin min_epochs=$MIN_EPOCHS incident=$INCIDENT"

deadline=$((DEADLINE_START_S + MAX_WAIT_MIN * 60))
consec=0
last_checked=$((start_epoch - 1))   # 관측은 "완료된" epoch만: current-1부터
results='[]'

while :; do
  [ "$(date -u +%s)" -lt "$deadline" ] || { echo "OBSERVE=FAIL reason=timeout waited_min=$MAX_WAIT_MIN consec=$consec"; exit 3; }
  hs="$(head_slot)"; fin="$(finalized_epoch)"
  if [ -z "$hs" ] || ! canonical_uint "$hs" || [ -z "$fin" ] || ! canonical_uint "$fin"; then
    echo "OBSERVE=FAIL reason=chain_response_schema"
    exit 3
  fi
  cur=$((hs / 32))
  target=$((cur - 1))              # 직전(완료된) epoch
  if [ "$target" -gt "$last_checked" ]; then
    e=$((last_checked + 1))
    while [ "$e" -le "$target" ]; do
      live_json="$(liveness_response "$e")" || live_json=""
      if [ -z "$live_json" ] || ! printf '%s' "$live_json" | jq -e --argjson expected "$IDX_JSON" '
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
        echo "OBSERVE=FAIL reason=liveness_response_schema epoch=$e"
        exit 3
      fi
      any="$(printf '%s' "$live_json" | jq -r '[.data[].is_live] | any')"
      if [ "$any" = "true" ]; then
        echo "OBSERVED validator LIVE at epoch=$e — DUPLICATE_SUSPECTED"
        echo "OBSERVE=DUPLICATE_SUSPECTED epoch=$e"
        echo "행동 지침: 페일오버 절대 금지. part-08 duplicate-suspicion 트랙으로 전환(시나리오 C)."
        exit 2
      else
        consec=$((consec + 1))
        results="$(printf '%s' "$results" | jq -c --argjson e "$e" '. + [{"epoch":$e,"any_live":false}]')"
        echo "EPOCH=$e all_absent=true consec=$consec finalized=$fin"
      fi
      e=$((e + 1))
    done
    last_checked="$target"
  fi
  fin_adv=$((fin - start_fin))
  if [ "$consec" -ge "$MIN_EPOCHS" ] && [ "$fin_adv" -ge "$MIN_EPOCHS" ]; then
    break
  fi
  if [ "$fin_adv" -lt 0 ]; then echo "OBSERVE=FAIL reason=finality_regressed"; exit 3; fi
  sleep "$POLL_S"
done

# finality 전진 검증: 관측 구간이 finalized 안에 들어와야 "체인의 결론"이 됨
end_fin="$(finalized_epoch)"
if [ -z "$end_fin" ] || ! canonical_uint "$end_fin"; then
  echo "OBSERVE=FAIL reason=cannot_read_finalized_boundary"
  exit 3
fi
if [ "$last_checked" -gt "$end_fin" ]; then
  echo "OBSERVE=FAIL reason=last_checked_not_finalized last_checked_epoch=$last_checked finalized_epoch_end=$end_fin"
  exit 3
fi
PUB_JSON="$(printf '%s\n' "${PUBKEYS[@]}" | jq -R . | jq -cs .)"
started_at="$(now_utc)"
completed_at="$(now_utc)"
if ! evidence_json="$(jq -n \
  --arg network "$NETWORK" --arg gvr "$EXPECTED_GVR" --arg incident "$INCIDENT" \
  --arg started "$started_at" --arg completed "$completed_at" \
  --argjson pubkeys "$PUB_JSON" --argjson indices "$IDX_JSON" \
  --argjson start_epoch "$start_epoch" --argjson last_checked "$last_checked" \
  --argjson consec "$consec" --argjson start_fin "$start_fin" --argjson end_fin "${end_fin:-0}" \
  --argjson per_epoch "$results" \
  '{schema: "absence-evidence/v1",
    result: "ABSENCE_OBSERVED",
    network: $network, genesis_validators_root: $gvr, incident: $incident,
    pubkeys: $pubkeys, validator_indices: $indices,
    baseline_head_epoch: $start_epoch, last_checked_epoch: $last_checked,
    consecutive_absent_epochs: $consec,
    finalized_epoch_start: $start_fin, finalized_epoch_end: $end_fin,
    per_epoch: $per_epoch,
    completed_at_utc: $completed,
    evidence_class: "negative-observation",
    not_a_proof_of: "원격 서명자의 부재. 미포함 서명은 나중에 포함될 수 있다(P1-04).",
    requires: "source fence evidence (RB-01 F1) — 이 파일 단독으로는 vc-gate를 통과시키지 못함",
    operator_crosscheck: "REQUIRED — 외부 탐색기에서 동일 validator의 최근 attestation 부재를 확인하고 URL·스크린샷을 증거 묶음에 첨부할 것"}')"; then
  echo "OBSERVE=FAIL reason=evidence_serialization"
  exit 3
fi

# helper가 stdin exact bytes와 그 checksum을 각각 0600 temp file에 쓰고 fsync한
# 뒤 directory를 fsync한다. Shell은 temp path를 직접 열지 않는다.
if ! prepared_token="$(printf '%s\n' "$evidence_json" | \
    store collector-prepare --token "$STORE_TOKEN")" \
   || [ "$prepared_token" != "$STORE_TOKEN" ]; then
  echo "OBSERVE=FAIL reason=evidence_prepare"
  exit 3
fi

# checksum-first와 JSON-last rename 뒤에도 각각 directory를 fsync한다. 두 단계
# 어느 사이에서 crash해도 durable lock 때문에 gate는 증거를 승인하지 않는다.
store collector-commit-checksum --token "$STORE_TOKEN" \
  || { echo "OBSERVE=FAIL reason=checksum_publish"; exit 3; }
test_barrier "${OBSERVE_TEST_PRECOMMIT_READY_FILE:-}" \
  "${OBSERVE_TEST_PRECOMMIT_CONTINUE_FILE:-}" precommit || exit 3
store collector-commit-json --token "$STORE_TOKEN" \
  || { echo "OBSERVE=FAIL reason=evidence_publish"; exit 3; }

# 결정적 signal/concurrency 테스트 경계. 둘 중 하나만 설정되거나 timeout이면
# fail closed하며 EXIT cleanup이 방금 publish한 두 파일을 모두 제거한다.
test_barrier "${OBSERVE_TEST_PUBLISH_READY_FILE:-}" \
  "${OBSERVE_TEST_PUBLISH_CONTINUE_FILE:-}" publish || exit 3

# shell→helper return 사이의 signal 창을 없앤다. helper 프로세스가 lock 제거와
# directory fsync를 최종 commit point로 완료한 뒤 성공 한 줄을 출력하고 종료한다.
exec "$EVIDENCE_STORE_HELPER" collector-finish \
  --root "$EVIDENCE_ROOT" --expected-owner "$EVIDENCE_OWNER_EXPECTED" --out "$OUT" \
  --token "$STORE_TOKEN" --consecutive "$consec" \
  --finalized-advance "$((end_fin - start_fin))"
