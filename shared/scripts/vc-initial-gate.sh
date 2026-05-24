#!/usr/bin/env bash
# One-time Primary activation gate. It deliberately has no counterparty
# fence requirement because the Standby region is built after CH10.
set -u -o pipefail

GATE_ENV="${VC_GATE_ENV:-/etc/ethereum/failover/gate.env}"
FAILURES=0

pass() { printf 'CHECK=%s STATUS=PASS%s\n' "$1" "${2:+ DETAIL=$2}"; }
fail() { printf 'CHECK=%s STATUS=FAIL%s\n' "$1" "${2:+ DETAIL=$2}"; FAILURES=$((FAILURES + 1)); }
finish() {
  if [ "$FAILURES" -eq 0 ]; then
    printf 'GATE=PASS FAILURES=0 SCOPE=%s HOST=%s\n' "${EXPECTED_SCOPE:-unset}" "${HOST_ID:-unset}"
    exit 0
  fi
  printf 'GATE=FAIL FAILURES=%s SCOPE=%s HOST=%s\n' "$FAILURES" "${EXPECTED_SCOPE:-unset}" "${HOST_ID:-unset}"
  exit 1
}
token_value() {
  awk -F': *' -v key="$1" '$1 == key {sub(/^[^:]*: */, ""); print}' "$TOKEN_FILE"
}
require_one_token_field() {
  [ "$(awk -F': *' -v key="$1" '$1 == key {count++} END {print count + 0}' "$TOKEN_FILE")" -eq 1 ]
}

if [ "$(id -u)" -ne 0 ]; then
  fail root_execution_required
  finish
fi
[ -f "$GATE_ENV" ] && [ ! -L "$GATE_ENV" ] || { fail gate_env_present "$GATE_ENV"; finish; }
# shellcheck disable=SC1090
. "$GATE_ENV"

REQUIRED_VARS="HOST_ID NETWORK EXPECTED_SCOPE BN_URL EXPECTED_GENESIS_VALIDATORS_ROOT PUBKEYS_FILE TOKEN_FILE VC_DATADIR DP_UNIT_PATHS FEE_RECIPIENT"
missing=""
for name in $REQUIRED_VARS; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || missing="$missing $name"
done
if [ -n "$missing" ]; then
  fail gate_env_complete "missing:$missing"
  finish
fi
pass gate_env_complete

[ "$EXPECTED_SCOPE" = "vc-start-initial" ] \
  && pass scope_recognized "$EXPECTED_SCOPE" \
  || fail scope_recognized "$EXPECTED_SCOPE"
[ ! -e "${SEALED_MARKER:-/etc/ethereum/failover/SEALED}" ] \
  && pass sealed_marker_absent \
  || fail sealed_marker_absent

if [ -f "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ] \
   && [ "$(stat -c '%U' "$TOKEN_FILE" 2>/dev/null)" = root ] \
   && printf '%s\n' 400 600 | grep -Fxq "$(stat -c '%a' "$TOKEN_FILE" 2>/dev/null)"; then
  pass token_secure_file "$TOKEN_FILE"
else
  fail token_secure_file "$TOKEN_FILE"
fi

fields="schema host_id network scope pubkey fee_recipient builder approver expires_at_utc"
token_shape_ok=1
for field in $fields; do
  require_one_token_field "$field" || token_shape_ok=0
done
[ "$token_shape_ok" -eq 1 ] && pass token_unique_fields || fail token_unique_fields

schema="$(token_value schema)"
token_host="$(token_value host_id)"
token_network="$(token_value network)"
token_scope="$(token_value scope)"
token_pubkey="$(token_value pubkey)"
token_fee="$(token_value fee_recipient)"
token_builder="$(token_value builder)"
token_approver="$(token_value approver)"
token_expiry="$(token_value expires_at_utc)"

[ "$schema" = "initial-activation/v1" ] && pass token_schema || fail token_schema "$schema"
[ "$token_host" = "$HOST_ID" ] && pass token_host_binding || fail token_host_binding
[ "$token_network" = "$NETWORK" ] && pass token_network_binding || fail token_network_binding
[ "$token_scope" = "$EXPECTED_SCOPE" ] && pass token_scope_binding || fail token_scope_binding
[ "$token_fee" = "$FEE_RECIPIENT" ] && pass token_fee_binding || fail token_fee_binding
[ "$token_builder" = "testnet_operator_01" ] \
  && [ "$token_approver" = "testnet_operator_02" ] \
  && pass token_two_person \
  || fail token_two_person

now_s="$(date -u +%s)"
expiry_s="$(date -u -d "$token_expiry" +%s 2>/dev/null || true)"
if [ -n "$expiry_s" ] && [ "$expiry_s" -gt "$now_s" ] \
   && [ $((expiry_s - now_s)) -le 604800 ]; then
  pass token_time_window "$token_expiry"
else
  fail token_time_window "$token_expiry"
fi

pubkey_count="$(grep -Ec '^0x[0-9A-Fa-f]{96}$' "$PUBKEYS_FILE" 2>/dev/null || true)"
[ "$pubkey_count" -eq 1 ] && [ "$token_pubkey" = "$(grep -E '^0x[0-9A-Fa-f]{96}$' "$PUBKEYS_FILE")" ] \
  && pass expected_pubkey_binding "$token_pubkey" \
  || fail expected_pubkey_binding

validators_dir="$VC_DATADIR/validators"
sp_db="$validators_dir/slashing_protection.sqlite"
[ -d "$validators_dir/$token_pubkey" ] \
  && pass validator_key_present "$token_pubkey" \
  || fail validator_key_present
[ -s "$sp_db" ] && [ ! -L "$sp_db" ] \
  && [ "$(stat -c '%U' "$sp_db" 2>/dev/null)" = lighthouse-validator ] \
  && pass sp_db_present "$sp_db" \
  || fail sp_db_present "$sp_db"

sync_json="$(curl -fsS --max-time 10 "$BN_URL/eth/v1/node/syncing" 2>/dev/null || true)"
printf '%s' "$sync_json" | jq -e '.data.is_syncing == false and .data.el_offline == false' >/dev/null 2>&1 \
  && pass bn_synced \
  || fail bn_synced
genesis_root="$(curl -fsS --max-time 10 "$BN_URL/eth/v1/beacon/genesis" 2>/dev/null | jq -r '.data.genesis_validators_root // empty' 2>/dev/null || true)"
[ "$genesis_root" = "$EXPECTED_GENESIS_VALIDATORS_ROOT" ] \
  && pass network_identity "$genesis_root" \
  || fail network_identity "$genesis_root"

dp_ok=1
for unit_path in $(printf '%s' "$DP_UNIT_PATHS" | tr ',' ' '); do
  grep -Fq -- '--enable-doppelganger-protection' "$unit_path" 2>/dev/null || dp_ok=0
done
[ "$dp_ok" -eq 1 ] && pass doppelganger_flag || fail doppelganger_flag
systemctl is-active --quiet "${LEASE_TIMER:-vc-lease.timer}" \
  && pass lease_timer_active \
  || fail lease_timer_active
if pgrep -u lighthouse-validator -f '^/opt/ethereum/lighthouse/current/lighthouse vc( |$)' >/dev/null 2>&1; then
  fail no_duplicate_vc_process
else
  pass no_duplicate_vc_process
fi

finish
