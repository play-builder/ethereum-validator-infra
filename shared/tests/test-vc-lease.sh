#!/usr/bin/env bash
# test-vc-lease.sh — self-fence lease 행동 테스트 (감사 P1-04 대응 기능)
# 원칙: "만료되면 반드시 seal된다"와 "유효하면 절대 건드리지 않는다" 둘 다 증명한다.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LEASE="$HERE/../scripts/vc-lease.sh"
PRIMARY_SERVICE="$HERE/../../primary-aws/ansible/roles/lighthouse_vc_gated/templates/vc-lease.service.j2"
STANDBY_ROLE="$HERE/../../standby-aws/ansible/roles/vc_sealed/tasks/main.yml"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
PASS_N=0; FAIL_N=0
ok()  { echo "TEST PASS: $1"; PASS_N=$((PASS_N+1)); }
bad() { echo "TEST FAIL: $1"; FAIL_N=$((FAIL_N+1)); }

# systemctl 스텁 — 실제 시스템을 건드리지 않고 호출을 기록한다
mkdir -p "$W/bin"
cat > "$W/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_CALLS"
case "$1" in
  is-active) cat "$STUB_STATE" 2>/dev/null || echo inactive ;;
  stop) echo "stub: $1 $2"; exit "${STUB_STOP_RC:-0}" ;;
  disable) echo "stub: $1 $2"; exit "${STUB_DISABLE_RC:-0}" ;;
  mask) echo "stub: $1 $2"; exit "${STUB_MASK_RC:-0}" ;;
esac
STUB
chmod +x "$W/bin/systemctl"
cat > "$W/bin/pgrep" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_PGREP_CALLS"
[ -n "${STUB_PGREP_OUTPUT:-}" ] && printf '%s\n' "$STUB_PGREP_OUTPUT"
exit "${STUB_PGREP_RC:-1}"
STUB
chmod +x "$W/bin/pgrep"
export PATH="$W/bin:$PATH"

mk_env() { # $1=만료(date 표현) $2=scope
  unset STUB_STOP_RC STUB_DISABLE_RC STUB_MASK_RC STUB_PGREP_RC STUB_PGREP_OUTPUT PGREP_PATTERN
  ETC="$W/etc"; rm -rf "$ETC"; mkdir -p "$ETC"
  cat > "$ETC/gate.env" <<EOF
HOST_ID=test-host
NETWORK=hoodi
EXPECTED_SCOPE=${2:-vc-start-primary}
TOKEN_FILE=$ETC/approval.token
SEALED_MARKER=$ETC/SEALED
EOF
  cat > "$ETC/approval.token" <<EOF
token_version: 1
host_id: test-host
network: hoodi
scope: ${2:-vc-start-primary}
issued_at_utc: $(date -u -d '-1 hour' +%FT%TZ)
expires_at_utc: $(date -u -d "$1" +%FT%TZ)
operators: Alice, Bob
EOF
  export STUB_CALLS="$W/calls.txt"; : > "$STUB_CALLS"
  export STUB_PGREP_CALLS="$W/pgrep-calls.txt"; : > "$STUB_PGREP_CALLS"
  export STUB_STATE="$W/state.txt"; echo active > "$STUB_STATE"
}
run() { VC_GATE_ENV="$ETC/gate.env" LEASE_LOG="$W/lease.log" bash "$LEASE" >"$W/out.txt" 2>&1; echo $?; }

# 1) 유효한 lease → 아무 것도 하지 않음(exit 0), seal 흔적 없음
mk_env "+48 hours"
rc="$(run)"
if [ "$rc" = "0" ] && grep -q "LEASE=VALID" "$W/out.txt" && ! grep -qE '^(stop|disable|mask)' "$STUB_CALLS" && [ ! -f "$ETC/SEALED" ]; then
  ok "valid lease -> no action (D1: 자동 기동 없음, 자동 정지도 없음)"
else bad "valid lease (rc=$rc)"; cat "$W/out.txt"; fi

# 2) 만료된 lease → seal(exit 10): stop·disable·mask 호출 + SEALED 생성 + token 제거
mk_env "-1 minute"
export PGREP_PATTERN="validator-client-exact-pattern"
rc="$(run)"
if [ "$rc" = "10" ] && grep -q "LEASE=EXPIRED" "$W/out.txt" \
   && grep -q "^stop " "$STUB_CALLS" && grep -q "^disable " "$STUB_CALLS" && grep -q "^mask " "$STUB_CALLS" \
   && [ -f "$ETC/SEALED" ] && [ ! -f "$ETC/approval.token" ] \
   && grep -q "LEASE_FENCE_DONE" "$W/out.txt" && ! grep -q "LEASE_FENCE_FAILED" "$W/out.txt" \
   && grep -q "remaining_vc_pids=\[\]" "$W/out.txt" && grep -q "pgrep_rc=1" "$W/out.txt" \
   && grep -Fxq -- "-f $PGREP_PATTERN" "$STUB_PGREP_CALLS"; then
  ok "expired lease -> self-fence (stop+disable+mask, SEALED, token removed)"
else bad "expired lease (rc=$rc)"; cat "$W/out.txt"; cat "$STUB_CALLS"; fi

# 3) token 부재 → seal
mk_env "+48 hours"; rm -f "$ETC/approval.token"
rc="$(run)"
if [ "$rc" = "10" ] && grep -q "token_missing" "$W/out.txt" && [ -f "$ETC/SEALED" ]; then
  ok "missing token -> self-fence"
else bad "missing token (rc=$rc)"; cat "$W/out.txt"; fi

# 4) scope 불일치 → seal (다른 역할 token으로 연명 금지)
mk_env "+48 hours" vc-start-failover
sed -i.bak 's/^EXPECTED_SCOPE=.*/EXPECTED_SCOPE=vc-start-primary/' "$ETC/gate.env"
rc="$(run)"
if [ "$rc" = "10" ] && grep -q "scope_mismatch" "$W/out.txt"; then
  ok "scope mismatch -> self-fence"
else bad "scope mismatch (rc=$rc)"; cat "$W/out.txt"; fi

# 5) VC가 이미 비활성 → 조치 없음(멱등, 알림 소음 방지)
mk_env "-1 minute"; echo inactive > "$STUB_STATE"
rc="$(run)"
if [ "$rc" = "0" ] && grep -q "LEASE=NOOP" "$W/out.txt" && [ ! -f "$ETC/SEALED" ]; then
  ok "inactive VC -> noop (idempotent)"
else bad "inactive VC (rc=$rc)"; cat "$W/out.txt"; fi

# 6) DRY_RUN → 실제 fence가 아니므로 실패(20), 실제 정지 없음 (드릴용)
mk_env "-1 minute"
cp "$ETC/approval.token" "$W/dry-run-token.before"
rc="$(VC_GATE_ENV="$ETC/gate.env" LEASE_LOG="$W/lease.log" LEASE_DRY_RUN=1 bash "$LEASE" >"$W/out.txt" 2>&1; echo $?)"
if [ "$rc" = "20" ] && grep -q "LEASE_DRY_RUN=1" "$W/out.txt" \
   && grep -q "LEASE_FENCE_FAILED" "$W/out.txt" && ! grep -q "LEASE_FENCE_DONE" "$W/out.txt" \
   && ! grep -qE '^(stop|disable|mask)( |$)' "$STUB_CALLS" && [ ! -f "$ETC/SEALED" ] \
   && [ -f "$ETC/approval.token" ] && cmp -s "$W/dry-run-token.before" "$ETC/approval.token"; then
  ok "dry-run -> unproven fence, no fencing"
else bad "dry-run (rc=$rc)"; cat "$W/out.txt"; fi

# 7) gate.env 부재 → 설정 오류(2). seal하지 않는다(오탐으로 서비스를 죽이지 않음)
ETC="$W/nonexistent"; export STUB_CALLS="$W/calls2.txt"; : > "$STUB_CALLS"
rc="$(VC_GATE_ENV="$ETC/gate.env" LEASE_LOG="$W/lease.log" bash "$LEASE" >"$W/out.txt" 2>&1; echo $?)"
if [ "$rc" = "2" ] && grep -q "gate_env_missing" "$W/out.txt" && ! grep -q "^stop " "$STUB_CALLS"; then
  ok "missing gate.env -> config error, no fencing"
else bad "missing gate.env (rc=$rc)"; cat "$W/out.txt"; fi

# 8) stop 실패 + VC PID 잔존 → seal을 완료했다고 주장하지 않고 실패(20)
mk_env "-1 minute"
export STUB_STOP_RC=1 STUB_PGREP_RC=0 STUB_PGREP_OUTPUT=4242
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "LEASE_FENCE_FAILED" "$W/out.txt" \
   && grep -q "stop_rc=1" "$W/out.txt" && grep -q "remaining_vc_pids=\[4242\]" "$W/out.txt" \
   && grep -q "^disable " "$STUB_CALLS" && grep -q "^mask " "$STUB_CALLS" \
   && ! grep -q "LEASE_FENCE_DONE" "$W/out.txt"; then
  ok "incomplete fence -> failed (stop error and remaining VC PID)"
else bad "incomplete fence expected rc=20, got rc=$rc"; cat "$W/out.txt"; fi

# 9) disable/mask 각각의 실제 rc를 보존하고 하나라도 실패하면 실패(20)
mk_env "-1 minute"; export STUB_DISABLE_RC=7
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "stop_rc=0" "$W/out.txt" \
   && grep -q "disable_rc=7" "$W/out.txt" && grep -q "mask_rc=0" "$W/out.txt"; then
  ok "disable failure -> failed with preserved rc"
else bad "disable failure expected preserved rc=7 (rc=$rc)"; cat "$W/out.txt"; fi

mk_env "-1 minute"; export STUB_MASK_RC=9
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "stop_rc=0" "$W/out.txt" \
   && grep -q "disable_rc=0" "$W/out.txt" && grep -q "mask_rc=9" "$W/out.txt"; then
  ok "mask failure -> failed with preserved rc"
else bad "mask failure expected preserved rc=9 (rc=$rc)"; cat "$W/out.txt"; fi

# 10) pgrep 2 이상은 process absence가 아니라 probe 오류 → 실패(20)
mk_env "-1 minute"; export STUB_PGREP_RC=2
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "LEASE_FENCE_FAILED" "$W/out.txt" \
   && grep -q "pgrep_rc=2" "$W/out.txt" && ! grep -q "LEASE_FENCE_DONE" "$W/out.txt"; then
  ok "pgrep error -> unproven process absence"
else bad "pgrep error expected rc=20, got rc=$rc"; cat "$W/out.txt"; fi

# 11) SEALED 생성과 token 제거도 실제 결과를 검증한다
mk_env "-1 minute"
printf 'SEALED_MARKER=%s\n' "$ETC/sealed-dir" >> "$ETC/gate.env"
mkdir "$ETC/sealed-dir"
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "LEASE_FENCE_FAILED" "$W/out.txt" \
   && grep -q "sealed=0" "$W/out.txt" && ! grep -q "LEASE_FENCE_DONE" "$W/out.txt"; then
  ok "SEALED creation failure -> failed"
else bad "SEALED creation failure expected rc=20, got rc=$rc"; cat "$W/out.txt"; fi

mk_env "-1 minute"
rm -f "$ETC/approval.token"; mkdir "$ETC/approval.token"
rc="$(run)"
if [ "$rc" = "20" ] && grep -q "LEASE_FENCE_FAILED" "$W/out.txt" \
   && grep -q "token_removed=0" "$W/out.txt" && [ -d "$ETC/approval.token" ] \
   && ! grep -q "LEASE_FENCE_DONE" "$W/out.txt"; then
  ok "token removal failure -> failed"
else bad "token removal failure expected rc=20, got rc=$rc"; cat "$W/out.txt"; fi

# 12) systemd는 완료된 fence(10)만 성공으로 취급하고 실패(20)는 제외한다
if grep -q '^SuccessExitStatus=0 10$' "$PRIMARY_SERVICE" \
   && ! grep -qE '^SuccessExitStatus=([^[:space:]]+[[:space:]]+)*20([[:space:]]|$)' "$PRIMARY_SERVICE" \
   && grep -q 'ansible.builtin.include_role:' "$STANDBY_ROLE" \
   && grep -q 'name: lighthouse_vc_gated' "$STANDBY_ROLE"; then
  ok "systemd success contract excludes incomplete fence exit 20"
else bad "systemd SuccessExitStatus must exclude 20"; fi

echo "----------------------------------------"
echo "vc-lease tests: PASS=$PASS_N FAIL=$FAIL_N"
[ "$FAIL_N" -eq 0 ]
