#!/usr/bin/env bash
# test-observe-absence.sh — observe-absence.sh 행동 테스트 (mock BN, 시간 가속)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OBS="$HERE/../scripts/observe-absence.sh"
WORK="$(mktemp -d)"; WORK="$(cd "$WORK" && pwd -P)"; trap 'rm -rf "$WORK"' EXIT
export EVIDENCE_ROOT="$WORK"
export EVIDENCE_OWNER_EXPECTED="$(id -un)"
export EVIDENCE_STORE_HELPER="$HERE/../scripts/vc-evidence-store.py"
PASS_N=0; FAIL_N=0
ok()  { echo "TEST PASS: $1"; PASS_N=$((PASS_N+1)); }
bad() { echo "TEST FAIL: $1"; FAIL_N=$((FAIL_N+1)); }
GVR="0x212f13fc4df078b6cb7db228f1c8307566dcecf900867401a92023d7ba99cb5f"
PK="0x$(printf 'ab%.0s' $(seq 48))"
PK2="0x$(printf 'cd%.0s' $(seq 48))"
echo "$PK" > "$WORK/pubkeys.txt"

# mock curl: head slot이 호출마다 1 epoch씩 전진, finalized도 전진.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<'MOCK'
#!/usr/bin/env bash
url=""; for a in "$@"; do case "$a" in http*) url="$a";; esac; done
S="$MOCK_STATE"
[ -f "$S/network_fail" ] && exit 22
case "$url" in
  */eth/v1/node/syncing)
    if [ -f "$S/syncing.json" ]; then cat "$S/syncing.json"
    else echo '{"data":{"is_syncing":false,"is_optimistic":false,"el_offline":false}}'; fi ;;
  */eth/v1/beacon/genesis) echo "{\"data\":{\"genesis_validators_root\":\"$MOCK_GVR\"}}" ;;
  */eth/v1/beacon/states/head/validators*)
    if [ -f "$S/validators.json" ]; then cat "$S/validators.json"
    else echo '{"data":[{"index":"777"}]}'; fi ;;
  */eth/v1/beacon/states/head/finality_checkpoints)
    n=$(cat "$S/fin" 2>/dev/null || echo 997)
    [ -f "$S/finality_plus" ] && n="+$n"
    echo "{\"data\":{\"finalized\":{\"epoch\":\"$n\"}}}" ;;
  */eth/v1/beacon/headers/head)
    n=$(cat "$S/head" 2>/dev/null || echo 1000)
    echo $((n+1)) > "$S/head"
    if [ ! -f "$S/hold_finality" ]; then
      f=$(cat "$S/fin" 2>/dev/null || echo 997); echo $((f+1)) > "$S/fin"
    fi
    if [ -f "$S/head_overflow" ]; then
      slot="$(python3 -c 'import sys; print((1 << 64) + int(sys.argv[1]) * 32)' "$n")"
    elif [ -f "$S/head_expression" ]; then slot="$((n*32))+32"
    else slot="$((n*32))"; fi
    echo "{\"data\":{\"header\":{\"message\":{\"slot\":\"$slot\"}}}}" ;;
  */eth/v1/validator/liveness/*)
    if [ -f "$S/liveness.json" ]; then cat "$S/liveness.json"
    elif [ -f "$S/live" ]; then echo '{"data":[{"index":"777","is_live":true}]}'
    else echo '{"data":[{"index":"777","is_live":false}]}'; fi ;;
  *) exit 22 ;;
esac
MOCK
chmod +x "$WORK/bin/curl"
export PATH="$WORK/bin:$PATH" MOCK_GVR="$GVR"

reset_state() { # $1=name
  export MOCK_STATE="$WORK/$1"
  rm -rf "$MOCK_STATE"
  mkdir -p "$MOCK_STATE"
  echo 1000 > "$MOCK_STATE/head"
  echo 1000 > "$MOCK_STATE/fin"
  printf '%s\n' "$PK" > "$WORK/pubkeys.txt"
}

run_observe() { # $1=out $2=log [$3=pubkeys]
  local out="$1" log="$2" pubkeys="${3:-$WORK/pubkeys.txt}"
  POLL_S=0 bash "$OBS" --pubkeys-file "$pubkeys" --network hoodi \
    --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
    --incident TEST --out "$out" >"$log" 2>&1
  echo $?
}

expect_schema_fail() { # $1=name $2=liveness JSON $3=case id
  local name="$1" body="$2" case_id="$3" out log rc
  out="$WORK/$case_id.json"
  log="$WORK/$case_id.txt"
  reset_state "$case_id-state"
  printf '%s\n' "$body" > "$MOCK_STATE/liveness.json"
  rc="$(run_observe "$out" "$log")"
  if [ "$rc" = 3 ] && grep -q 'reason=liveness_response_schema' "$log" \
     && [ ! -e "$out" ] && [ ! -e "$out.sha256" ]; then
    ok "$name"
  else
    bad "$name (rc=$rc)"
    sed -n '1,80p' "$log"
  fi
}

seed_prior_success() { # $1=out
  printf '{"schema":"absence-evidence/v1","result":"ABSENCE_OBSERVED","stale":true}\n' > "$1"
  sha256sum "$1" | awk '{print $1}' > "$1.sha256"
}

expect_stale_invalidated() { # $1=name $2=expected rc $3=out $4=log
  local name="$1" expected_rc="$2" out="$3" log="$4" rc="$5"
  if [ "$rc" = "$expected_rc" ] && [ ! -e "$out" ] && [ ! -e "$out.sha256" ]; then
    ok "$name"
  else
    bad "$name (rc=$rc, stale_out=$([ -e "$out" ] && echo yes || echo no), stale_sha=$([ -e "$out.sha256" ] && echo yes || echo no))"
    sed -n '1,80p' "$log"
  fi
}

expect_untouched() { # $1=name $2=path $3=expected bytes file $4=rc $5=log
  local name="$1" path="$2" expected="$3" rc="$4" log="$5"
  if [ "$rc" != 0 ] && [ -e "$path" ] \
     && [ "$(sha256sum "$path" | awk '{print $1}')" = "$(sha256sum "$expected" | awk '{print $1}')" ]; then
    ok "$name"
  else
    bad "$name (rc=$rc path_exists=$([ -e "$path" ] && echo yes || echo no))"
    sed -n '1,80p' "$log"
  fi
}

# 1) 정상: 부재 3 epoch가 실제 finalized 범위 안 → ABSENCE_OBSERVED
export MOCK_STATE="$WORK/s1"; mkdir -p "$MOCK_STATE"; echo 1000 > "$MOCK_STATE/head"; echo 1000 > "$MOCK_STATE/fin"
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$WORK/absence.json" >"$WORK/o1.txt" 2>&1
rc=$?
if [ $rc -eq 0 ] && [ "$(jq -r '.result' "$WORK/absence.json")" = "ABSENCE_OBSERVED" ] \
   && [ "$(jq -r '.consecutive_absent_epochs' "$WORK/absence.json")" -ge 3 ] \
   && [ "$(jq -r '.last_checked_epoch <= .finalized_epoch_end' "$WORK/absence.json")" = "true" ] \
   && [ -s "$WORK/absence.json.sha256" ] \
   && jq -e --arg pk "$PK" '.pubkeys | index($pk)' "$WORK/absence.json" >/dev/null; then
  ok "absent chain -> ABSENCE_OBSERVED with valid evidence"
else bad "absent chain (rc=$rc)"; cat "$WORK/o1.txt"; fi

# 2) last_checked=1002지만 finalized_end=1001이면 성공 evidence를 쓰지 않는다.
export MOCK_STATE="$WORK/s2"; mkdir -p "$MOCK_STATE"; echo 1000 > "$MOCK_STATE/head"; echo 997 > "$MOCK_STATE/fin"
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$WORK/unfinalized.json" >"$WORK/o2.txt" 2>&1
rc=$?
if [ $rc -eq 3 ] && grep -q "reason=last_checked_not_finalized" "$WORK/o2.txt" \
   && [ ! -f "$WORK/unfinalized.json" ] && [ ! -f "$WORK/unfinalized.json.sha256" ]; then
  ok "last_checked epoch beyond finalized boundary -> no success evidence (exit 3)"
else bad "unfinalized absence boundary (rc=$rc)"; cat "$WORK/o2.txt"; fi

# 3) 좀비: liveness=true → exit 2, 증거 파일 미생성
export MOCK_STATE="$WORK/s2"; mkdir -p "$MOCK_STATE"; echo 1000 > "$MOCK_STATE/head"; echo 997 > "$MOCK_STATE/fin"; touch "$MOCK_STATE/live"
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$WORK/absence2.json" >"$WORK/o3.txt" 2>&1
rc=$?
if [ $rc -eq 2 ] && grep -q "DUPLICATE_SUSPECTED" "$WORK/o3.txt" && [ ! -f "$WORK/absence2.json" ]; then
  ok "live validator -> DUPLICATE_SUSPECTED (exit 2), no evidence written"
else bad "live validator path (rc=$rc)"; cat "$WORK/o3.txt"; fi

# 4) 네트워크 정체성 불일치 → exit 3
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root 0xwrong --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$WORK/absence3.json" >"$WORK/o4.txt" 2>&1
rc=$?
if [ $rc -eq 3 ] && grep -q "network_identity" "$WORK/o4.txt"; then
  ok "wrong genesis root -> refuse to observe (exit 3)"
else bad "wrong genesis root (rc=$rc)"; cat "$WORK/o4.txt"; fi

# 5~9) liveness 응답은 요청 index exact set / exactly-once / boolean이어야 한다.
expect_schema_fail "empty liveness rows -> fail closed" \
  '{"data":[]}' liveness-empty
expect_schema_fail "missing requested liveness index -> fail closed" \
  '{"data":[{"index":"999","is_live":false}]}' liveness-missing
expect_schema_fail "duplicate liveness index -> fail closed" \
  '{"data":[{"index":"777","is_live":false},{"index":"777","is_live":false}]}' liveness-duplicate
expect_schema_fail "additional liveness index -> fail closed" \
  '{"data":[{"index":"777","is_live":false},{"index":"999","is_live":false}]}' liveness-additional
expect_schema_fail "nonboolean liveness value -> fail closed" \
  '{"data":[{"index":"777","is_live":"false"}]}' liveness-nonboolean

# 10~13) 새 관측은 시작 즉시 이전 OUT과 checksum을 무효화하며 실패/신호에도 복구하지 않는다.
reset_state stale-network-state; touch "$MOCK_STATE/network_fail"
out="$WORK/stale-network.json"; log="$WORK/stale-network.txt"; seed_prior_success "$out"
rc="$(run_observe "$out" "$log")"
expect_stale_invalidated "network failure invalidates prior success evidence" 3 "$out" "$log" "$rc"

reset_state stale-live-state; touch "$MOCK_STATE/live"
out="$WORK/stale-live.json"; log="$WORK/stale-live.txt"; seed_prior_success "$out"
rc="$(run_observe "$out" "$log")"
expect_stale_invalidated "live validator invalidates prior success evidence" 2 "$out" "$log" "$rc"

reset_state stale-fault-state
out="$WORK/stale-fault.json"; log="$WORK/stale-fault.txt"; seed_prior_success "$out"
rc="$(run_observe "$out" "$log" "$WORK/does-not-exist-pubkeys.txt")"
expect_stale_invalidated "precheck fault invalidates prior success evidence" 3 "$out" "$log" "$rc"

reset_state stale-argument-state
out="$WORK/stale-argument.json"; log="$WORK/stale-argument.txt"; seed_prior_success "$out"
POLL_S=0 bash "$OBS" --out "$out" --incident >"$log" 2>&1
rc=$?
expect_stale_invalidated "argument failure after OUT invalidates prior success evidence" 64 "$out" "$log" "$rc"

reset_state stale-argument-before-out-state
out="$WORK/stale-argument-before-out.json"; log="$WORK/stale-argument-before-out.txt"; seed_prior_success "$out"
POLL_S=0 bash "$OBS" --incident --out "$out" >"$log" 2>&1
rc=$?
expect_stale_invalidated "missing option value before OUT still invalidates prior success evidence" 64 "$out" "$log" "$rc"

# OUT는 신뢰된 EVIDENCE_ROOT의 direct basename 하나로만 지정할 수 있다.
outside="$(mktemp -d)"
victim="$outside/victim.json"; expected="$outside/victim.expected"; log="$WORK/outside.txt"
printf 'outside victim bytes\n' > "$victim"; cp "$victim" "$expected"
POLL_S=0 bash "$OBS" --out "$victim" --bad >"$log" 2>&1; rc=$?
expect_untouched "outside OUT survives malformed argv byte-identical" "$victim" "$expected" "$rc" "$log"

victim="$outside/traversal.json"; expected="$outside/traversal.expected"; log="$WORK/traversal.txt"
printf 'traversal victim bytes\n' > "$victim"; cp "$victim" "$expected"
traversal="$WORK/../$(basename "$outside")/traversal.json"
POLL_S=0 bash "$OBS" --out "$traversal" --bad >"$log" 2>&1; rc=$?
expect_untouched "traversal OUT is rejected without victim mutation" "$victim" "$expected" "$rc" "$log"

nested="$WORK/nested/victim.json"; log="$WORK/nested.txt"
POLL_S=0 bash "$OBS" --out "$nested" --bad >"$log" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ ! -e "$WORK/nested" ]; then
  ok "nested OUT is rejected without mkdir of arbitrary parent"
else bad "nested OUT boundary (rc=$rc parent_exists=$([ -e "$WORK/nested" ] && echo yes || echo no))"; fi

first="$WORK/repeated-first.json"; second="$WORK/repeated-second.json"
printf 'first bytes\n' > "$first"; printf 'second bytes\n' > "$second"
first_sha="$(sha256sum "$first" | awk '{print $1}')"; second_sha="$(sha256sum "$second" | awk '{print $1}')"
POLL_S=0 bash "$OBS" --out "$first" --out "$second" --bad >"$WORK/repeated.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ "$first_sha" = "$(sha256sum "$first" 2>/dev/null | awk '{print $1}')" ] \
   && [ "$second_sha" = "$(sha256sum "$second" 2>/dev/null | awk '{print $1}')" ]; then
  ok "repeated --out is ambiguous and mutates neither target"
else bad "repeated --out boundary (rc=$rc)"; fi

real_root="$WORK/real-root"; link_root="$WORK/link-root"; mkdir -m 700 "$real_root"; ln -s "$real_root" "$link_root"
victim="$real_root/root-link-victim.json"; expected="$outside/root-link.expected"; printf 'root link victim\n' > "$victim"; cp "$victim" "$expected"
EVIDENCE_ROOT="$link_root" POLL_S=0 bash "$OBS" --out "$link_root/root-link-victim.json" --bad >"$WORK/root-link.txt" 2>&1; rc=$?
expect_untouched "symlinked EVIDENCE_ROOT is rejected without mutation" "$victim" "$expected" "$rc" "$WORK/root-link.txt"

parent_link="$WORK/parent-link"; ln -s "$outside" "$parent_link"
victim="$outside/parent-link-victim.json"; expected="$outside/parent-link.expected"; printf 'parent link victim\n' > "$victim"; cp "$victim" "$expected"
POLL_S=0 bash "$OBS" --out "$parent_link/parent-link-victim.json" --bad >"$WORK/parent-link.txt" 2>&1; rc=$?
expect_untouched "symlinked OUT parent is rejected without mutation" "$victim" "$expected" "$rc" "$WORK/parent-link.txt"

target="$outside/output-symlink-target"; printf 'output symlink target\n' > "$target"
ln -s "$target" "$WORK/output-symlink.json"
POLL_S=0 bash "$OBS" --out "$WORK/output-symlink.json" --bad >"$WORK/output-symlink.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ -L "$WORK/output-symlink.json" ] \
   && [ "$(cat "$target")" = "output symlink target" ]; then
  ok "output symlink is rejected without unlink or target mutation"
else bad "output symlink boundary (rc=$rc)"; fi

target="$outside/checksum-symlink-target"; printf 'checksum symlink target\n' > "$target"
printf 'trusted output\n' > "$WORK/checksum-symlink.json"
ln -s "$target" "$WORK/checksum-symlink.json.sha256"
POLL_S=0 bash "$OBS" --out "$WORK/checksum-symlink.json" --bad >"$WORK/checksum-symlink.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ -L "$WORK/checksum-symlink.json.sha256" ] \
   && [ "$(cat "$target")" = "checksum symlink target" ]; then
  ok "checksum symlink is rejected without unlink or target mutation"
else bad "checksum symlink boundary (rc=$rc)"; fi
rm -rf "$outside"

untrusted_root="$WORK/untrusted-mode-root"; mkdir -m 700 "$untrusted_root"
victim="$untrusted_root/victim.json"; printf 'untrusted mode victim\n' > "$victim"
victim_sha="$(sha256sum "$victim" | awk '{print $1}')"; chmod 0770 "$untrusted_root"
EVIDENCE_ROOT="$untrusted_root" POLL_S=0 bash "$OBS" --out "$victim" --bad >"$WORK/root-mode.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ "$victim_sha" = "$(sha256sum "$victim" | awk '{print $1}')" ]; then
  ok "group-writable EVIDENCE_ROOT is rejected without mutation"
else bad "untrusted EVIDENCE_ROOT mode (rc=$rc)"; fi

missing_root="$WORK/missing-evidence-root"
EVIDENCE_ROOT="$missing_root" POLL_S=0 bash "$OBS" --out "$missing_root/victim.json" --bad >"$WORK/root-missing.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ ! -e "$missing_root" ]; then
  ok "missing EVIDENCE_ROOT is rejected without mkdir"
else bad "missing EVIDENCE_ROOT boundary (rc=$rc root_exists=$([ -e "$missing_root" ] && echo yes || echo no))"; fi

owner_root="$WORK/owner-root"; mkdir -m 700 "$owner_root"
victim="$owner_root/victim.json"; printf 'owner victim\n' > "$victim"; victim_sha="$(sha256sum "$victim" | awk '{print $1}')"
EVIDENCE_ROOT="$owner_root" EVIDENCE_OWNER_EXPECTED=definitely-not-current-user \
  POLL_S=0 bash "$OBS" --out "$victim" --bad >"$WORK/root-owner.txt" 2>&1; rc=$?
if [ "$rc" != 0 ] && [ "$victim_sha" = "$(sha256sum "$victim" | awk '{print $1}')" ]; then
  ok "unexpected EVIDENCE_ROOT owner is rejected without mutation"
else bad "untrusted EVIDENCE_ROOT owner (rc=$rc)"; fi

reset_state stale-signal-state; echo 997 > "$MOCK_STATE/fin"; touch "$MOCK_STATE/hold_finality"
out="$WORK/stale-signal.json"; log="$WORK/stale-signal.txt"; seed_prior_success "$out"
POLL_S=1 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$out" >"$log" 2>&1 &
pid=$!
for _ in $(seq 1 500); do
  grep -q '^EPOCH=' "$log" 2>/dev/null && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
kill -TERM "$pid" 2>/dev/null || true
wait "$pid"; rc=$?
if [ "$rc" = 3 ] && grep -q 'reason=signal' "$log" \
   && [ ! -e "$out" ] && [ ! -e "$out.sha256" ]; then
  ok "signal invalidates prior success evidence"
else
  bad "signal invalidation (rc=$rc)"
  sed -n '1,80p' "$log"
fi

# publish 두 rename 뒤 signal도 성공 exit가 아니므로 현재 증거를 남기지 않는다.
reset_state post-publish-signal-state
out="$WORK/post-publish-signal.json"; log="$WORK/post-publish-signal.txt"; seed_prior_success "$out"
publish_ready="$WORK/post-publish-signal.ready"
publish_continue="$WORK/post-publish-signal.continue"
rm -f "$publish_ready" "$publish_continue"
OBSERVE_TEST_PUBLISH_READY_FILE="$publish_ready" \
OBSERVE_TEST_PUBLISH_CONTINUE_FILE="$publish_continue" \
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$out" >"$log" 2>&1 &
pid=$!; marker_seen=0
for _ in $(seq 1 500); do
  if [ -f "$publish_ready" ]; then marker_seen=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
if [ "$marker_seen" -eq 1 ]; then kill -TERM "$pid" 2>/dev/null || true; fi
wait "$pid"; rc=$?
if [ "$marker_seen" -eq 1 ] && [ "$rc" = 3 ] && grep -q 'reason=signal' "$log" \
   && [ ! -e "$out" ] && [ ! -e "$out.sha256" ]; then
  ok "signal after publish removes current success evidence"
else
  bad "post-publish signal cleanup (rc=$rc marker=$marker_seen)"
  sed -n '1,80p' "$log"
fi

# checksum은 먼저 보일 수 있지만 gate가 소비하는 JSON은 마지막 atomic commit이다.
reset_state checksum-first-state
out="$WORK/checksum-first.json"; log="$WORK/checksum-first.txt"; seed_prior_success "$out"
commit_ready="$WORK/checksum-first.ready"
commit_continue="$WORK/checksum-first.continue"
rm -f "$commit_ready" "$commit_continue"
OBSERVE_TEST_PRECOMMIT_READY_FILE="$commit_ready" \
OBSERVE_TEST_PRECOMMIT_CONTINUE_FILE="$commit_continue" \
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$out" >"$log" 2>&1 &
pid=$!; marker_seen=0; checksum_first=0
for _ in $(seq 1 500); do
  if [ -f "$commit_ready" ]; then marker_seen=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
if [ "$marker_seen" -eq 1 ]; then
  if [ ! -e "$out" ] && [ -s "$out.sha256" ] \
     && [ -d "$out.observe.lock" ]; then checksum_first=1; fi
  kill -TERM "$pid" 2>/dev/null || true
fi
wait "$pid"; rc=$?
if [ "$marker_seen" -eq 1 ] && [ "$checksum_first" -eq 1 ] && [ "$rc" = 3 ] \
   && [ ! -e "$out" ] && [ ! -e "$out.sha256" ] && [ ! -e "$out.observe.lock" ]; then
  ok "checksum publishes before JSON commit and precommit signal leaves no evidence"
else
  bad "checksum-first commit boundary (rc=$rc marker=$marker_seen checksum_first=$checksum_first)"
  sed -n '1,80p' "$log"
fi

# SIGKILL은 cleanup을 실행할 수 없으므로 durable lock이 partial pair를 닫는다.
reset_state checksum-first-kill-state
out="$WORK/checksum-first-kill.json"; log="$WORK/checksum-first-kill.txt"; seed_prior_success "$out"
commit_ready="$WORK/checksum-first-kill.ready"; commit_continue="$WORK/checksum-first-kill.continue"
rm -f "$commit_ready" "$commit_continue"
OBSERVE_TEST_PRECOMMIT_READY_FILE="$commit_ready" \
OBSERVE_TEST_PRECOMMIT_CONTINUE_FILE="$commit_continue" \
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$out" >"$log" 2>&1 &
pid=$!; marker_seen=0
for _ in $(seq 1 500); do
  if [ -f "$commit_ready" ]; then marker_seen=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
[ "$marker_seen" -ne 1 ] || kill -KILL "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null; rc=$?
if [ "$marker_seen" -eq 1 ] && [ "$rc" -ne 0 ] && [ ! -e "$out" ] \
   && [ -s "$out.sha256" ] && [ -d "$out.observe.lock" ]; then
  ok "SIGKILL after checksum publish leaves partial pair locked fail closed"
else bad "checksum-first SIGKILL state (rc=$rc marker=$marker_seen)"; fi

# JSON-last가 끝났어도 lock 제거가 최종 commit point다.
reset_state json-last-kill-state
out="$WORK/json-last-kill.json"; log="$WORK/json-last-kill.txt"; seed_prior_success "$out"
publish_ready="$WORK/json-last-kill.ready"; publish_continue="$WORK/json-last-kill.continue"
rm -f "$publish_ready" "$publish_continue"
OBSERVE_TEST_PUBLISH_READY_FILE="$publish_ready" \
OBSERVE_TEST_PUBLISH_CONTINUE_FILE="$publish_continue" \
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident TEST --out "$out" >"$log" 2>&1 &
pid=$!; marker_seen=0
for _ in $(seq 1 500); do
  if [ -f "$publish_ready" ]; then marker_seen=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
[ "$marker_seen" -ne 1 ] || kill -KILL "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null; rc=$?
if [ "$marker_seen" -eq 1 ] && [ "$rc" -ne 0 ] && [ -s "$out" ] \
   && [ -s "$out.sha256" ] && [ -d "$out.observe.lock" ] \
   && [ "$(sha256sum "$out" | awk '{print $1}')" = "$(sed -n '1p' "$out.sha256")" ]; then
  ok "SIGKILL after JSON-last leaves matching pair locked fail closed"
else bad "JSON-last SIGKILL state (rc=$rc marker=$marker_seen)"; fi

# 같은 OUT의 동시 관측은 기존 run을 건드리지 않고 즉시 fail closed한다.
reset_state concurrent-out-state
out="$WORK/concurrent-out.json"; log="$WORK/concurrent-first.txt"; log2="$WORK/concurrent-second.txt"
seed_prior_success "$out"
publish_ready="$WORK/concurrent-out.ready"
publish_continue="$WORK/concurrent-out.continue"
rm -f "$publish_ready" "$publish_continue"
OBSERVE_TEST_PUBLISH_READY_FILE="$publish_ready" \
OBSERVE_TEST_PUBLISH_CONTINUE_FILE="$publish_continue" \
POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
  --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
  --incident FIRST --out "$out" >"$log" 2>&1 &
pid=$!; marker_seen=0; second_rc=99; first_rc=99; evidence_unchanged=0
for _ in $(seq 1 500); do
  if [ -f "$publish_ready" ]; then marker_seen=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.01
done
if [ "$marker_seen" -eq 1 ]; then
  before_json="$(sha256sum "$out" | awk '{print $1}')"
  before_sha="$(sha256sum "$out.sha256" | awk '{print $1}')"
  second_rc="$(run_observe "$out" "$log2")"
  if [ "$before_json" = "$(sha256sum "$out" | awk '{print $1}')" ] \
     && [ "$before_sha" = "$(sha256sum "$out.sha256" | awk '{print $1}')" ]; then
    evidence_unchanged=1
  fi
  touch "$publish_continue"
else
  kill -TERM "$pid" 2>/dev/null || true
fi
wait "$pid"; first_rc=$?
if [ "$marker_seen" -eq 1 ] && [ "$first_rc" = 0 ] && [ "$second_rc" = 3 ] \
   && [ "$evidence_unchanged" -eq 1 ] && grep -q 'reason=observation_already_in_progress' "$log2" \
   && sha256sum -c <(awk -v path="$out" '{print $1 "  " path}' "$out.sha256") >/dev/null 2>&1; then
  ok "concurrent same-OUT observation fails without mutating active evidence"
else
  bad "concurrent same-OUT serialization (first_rc=$first_rc second_rc=$second_rc marker=$marker_seen unchanged=$evidence_unchanged)"
  sed -n '1,80p' "$log"; sed -n '1,80p' "$log2"
fi

# 14~19) BN readiness schema와 slot/epoch/index는 canonical unsigned decimal만 허용한다.
reset_state syncing-missing-opt-state
echo '{"data":{"is_syncing":false,"el_offline":false}}' > "$MOCK_STATE/syncing.json"
out="$WORK/syncing-missing-opt.json"; log="$WORK/syncing-missing-opt.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=bn_not_ready' "$log"; then ok "collector rejects missing is_optimistic"
else bad "collector missing is_optimistic (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state syncing-missing-el-state
echo '{"data":{"is_syncing":false,"is_optimistic":false}}' > "$MOCK_STATE/syncing.json"
out="$WORK/syncing-missing-el.json"; log="$WORK/syncing-missing-el.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=bn_not_ready' "$log"; then ok "collector rejects missing el_offline"
else bad "collector missing el_offline (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state syncing-string-state
echo '{"data":{"is_syncing":"false","is_optimistic":"false","el_offline":"false"}}' > "$MOCK_STATE/syncing.json"
out="$WORK/syncing-string.json"; log="$WORK/syncing-string.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=bn_not_ready' "$log"; then ok "collector rejects nonboolean readiness flags"
else bad "collector nonboolean readiness (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state head-expression-state; touch "$MOCK_STATE/head_expression"
out="$WORK/head-expression.json"; log="$WORK/head-expression.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=cannot_read_chain' "$log"; then ok "collector rejects arithmetic-expression head slot"
else bad "collector arithmetic head slot (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state head-overflow-state; touch "$MOCK_STATE/head_overflow"
out="$WORK/head-overflow.json"; log="$WORK/head-overflow.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=cannot_read_chain' "$log"; then ok "collector rejects head slot beyond signed arithmetic range"
else bad "collector overflow head slot (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state finality-plus-state; touch "$MOCK_STATE/finality_plus"
out="$WORK/finality-plus.json"; log="$WORK/finality-plus.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=cannot_read_chain' "$log"; then ok "collector rejects leading-sign finalized epoch"
else bad "collector leading-sign finalized epoch (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state index-expression-state
echo '{"data":[{"index":"777+0"}]}' > "$MOCK_STATE/validators.json"
echo '{"data":[{"index":"777+0","is_live":false}]}' > "$MOCK_STATE/liveness.json"
out="$WORK/index-expression.json"; log="$WORK/index-expression.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=index_resolution' "$log"; then ok "collector rejects arithmetic-expression validator index"
else bad "collector arithmetic validator index (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state index-leading-zero-state
echo '{"data":[{"index":"0777"}]}' > "$MOCK_STATE/validators.json"
echo '{"data":[{"index":"0777","is_live":false}]}' > "$MOCK_STATE/liveness.json"
out="$WORK/index-leading-zero.json"; log="$WORK/index-leading-zero.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=index_resolution' "$log"; then ok "collector rejects leading-zero validator index"
else bad "collector leading-zero validator index (rc=$rc)"; sed -n '1,60p' "$log"; fi

reset_state index-duplicate-state
printf '%s\n%s\n' "$PK" "$PK2" > "$WORK/pubkeys.txt"
echo '{"data":[{"index":"777","is_live":false}]}' > "$MOCK_STATE/liveness.json"
out="$WORK/index-duplicate.json"; log="$WORK/index-duplicate.txt"; rc="$(run_observe "$out" "$log")"
if [ "$rc" = 3 ] && grep -q 'reason=expected_pubkeys_exactly_one' "$log"; then ok "collector rejects more than one configured validator before resolution"
else bad "collector exactly-one validator contract (rc=$rc)"; sed -n '1,60p' "$log"; fi

# CLI 산술 입력은 canonical signed-64-safe unsigned decimal이며 1 이상이어야 한다.
for spec in \
  'min-zero|--min-epochs|0' \
  'min-expression|--min-epochs|3+0' \
  'min-leading-zero|--min-epochs|03' \
  'min-leading-plus|--min-epochs|+3' \
  'min-float|--min-epochs|3.0' \
  'min-exponent|--min-epochs|3e0' \
  'min-overflow|--min-epochs|9223372036854775808' \
  'wait-zero|--max-wait-min|0' \
  'wait-leading-zero|--max-wait-min|01' \
  'wait-negative|--max-wait-min|-1' \
  'wait-float|--max-wait-min|1.5' \
  'wait-deadline-overflow|--max-wait-min|153722867280912930' \
  'wait-overflow|--max-wait-min|9223372036854775808'; do
  IFS='|' read -r case_id option value <<EOF
$spec
EOF
  reset_state "numeric-$case_id-state"
  out="$WORK/numeric-$case_id.json"; log="$WORK/numeric-$case_id.txt"
  POLL_S=0 bash "$OBS" --pubkeys-file "$WORK/pubkeys.txt" --network hoodi \
    --genesis-validators-root "$GVR" --min-epochs 3 --max-wait-min 1 \
    "$option" "$value" --incident TEST --out "$out" >"$log" 2>&1; rc=$?
  if [ "$rc" = 3 ] && grep -q 'reason=invalid_numeric_arguments' "$log" \
     && [ ! -e "$out" ] && [ ! -e "$out.sha256" ]; then
    ok "collector rejects invalid numeric $case_id"
  else bad "collector invalid numeric $case_id (rc=$rc)"; sed -n '1,60p' "$log"; fi
done

echo "----------------------------------------"
echo "observe-absence tests: PASS=$PASS_N FAIL=$FAIL_N"
[ "$FAIL_N" -eq 0 ]
