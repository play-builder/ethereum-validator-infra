#!/bin/bash

PASS_COUNT=0
FAIL_COUNT=0
ok()  { printf 'OPENPGP_VERIFY=PASS client=%-12s version=%s\n' "$1" "$2"; PASS_COUNT=$((PASS_COUNT+1)); }
bad() { printf 'OPENPGP_VERIFY=FAIL client=%-12s reason=%s\n' "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); }

verify_client() {
  local client="$1" current="$2" keyring="$3" prefix="$4" suffix="$5"
  local version archive signature

  version="$(basename "$(readlink -f "${current}" 2>/dev/null)" 2>/dev/null)"
  [ -n "${version}" ] || { bad "${client}" "current_symlink_missing"; return; }

  archive="/opt/ethereum/dist/${prefix}${version}${suffix}"
  signature="${archive}.asc"

  [ -f "${keyring}" ]   || { bad "${client}" "keyring_missing"; return; }
  [ -f "${archive}" ]   || { bad "${client}" "archive_missing"; return; }
  [ -f "${signature}" ] || { bad "${client}" "signature_missing"; return; }

  gpgv --keyring "${keyring}" "${signature}" "${archive}" >/dev/null 2>&1 \
    && ok "${client}" "${version}" \
    || bad "${client}" "gpgv_failed"
}

verify_client nethermind \
  /opt/ethereum/nethermind/current \
  /opt/ethereum/trust/nethermind-release-key.gpg \
  "nethermind-" ".zip"

verify_client lighthouse \
  /opt/ethereum/lighthouse/current \
  /opt/ethereum/trust/lighthouse-release-key.gpg \
  "lighthouse-v" ".tar.gz"

RESULT=PASS
[ "${FAIL_COUNT}" -eq 0 ] || RESULT=FAIL
printf 'CLIENT_SIGNATURES=%s clients=%d fail=%d\n' "${RESULT}" $((PASS_COUNT+FAIL_COUNT)) "${FAIL_COUNT}"
[ "${RESULT}" = "PASS" ]
