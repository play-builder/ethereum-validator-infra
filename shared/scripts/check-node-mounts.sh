#!/bin/bash

WAIT_CLOUD_INIT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --wait-cloud-init) WAIT_CLOUD_INIT=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ "${WAIT_CLOUD_INIT}" -eq 0 ] || cloud-init status --wait

PASS_COUNT=0
FAIL_COUNT=0
ok()  { printf 'CHECK %-28s OK    %s\n' "$1" "$2"; PASS_COUNT=$((PASS_COUNT+1)); }
bad() { printf 'CHECK %-28s FAIL  %s\n' "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); }

check_mount() {
  local target="$1" want_label="$2" line
  line="$(findmnt -no TARGET,SOURCE,LABEL --target "${target}" 2>/dev/null)"
  [ -n "${line}" ] || { bad "${target}" "not mounted"; return; }
  printf '%s' "${line}" | grep -q "${want_label}" \
    && ok "${target}" "${line}" \
    || bad "${target}" "label mismatch: ${line}"
}

check_mount /data ETH-DATA
check_mount /var/lib/validator-state ETH-VALSTATE

RESULT=PASS
[ "${FAIL_COUNT}" -eq 0 ] || RESULT=FAIL
printf 'NODE_MOUNTS=%s checks=%d fail=%d\n' "${RESULT}" $((PASS_COUNT+FAIL_COUNT)) "${FAIL_COUNT}"
[ "${RESULT}" = "PASS" ]
