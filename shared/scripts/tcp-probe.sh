#!/usr/bin/env bash
# tcp-probe.sh — 포트 목록을 순회하며 기대 상태(refused|closed|open)를 검증한다.
# 문서 규칙상 제어문을 강의 화면에 두지 않기 위해 순회를 이 헬퍼가 대신한다.
set -euo pipefail
HOST=""; PORTS=""; EXPECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --ports) PORTS="$2"; shift 2 ;;
    --expect) EXPECT="$2"; shift 2 ;;
    *) echo "TCP_PROBE=FAIL reason=bad_arg arg=$1" >&2; exit 2 ;;
  esac
done
[ -n "${HOST}" ] && [ -n "${PORTS}" ] || { echo "TCP_PROBE=FAIL reason=missing_arg" >&2; exit 2; }
FAILED=0; COUNT=0
for P in ${PORTS}; do
  COUNT=$((COUNT+1))
  case "${EXPECT}" in
    refused)
      set +e; OUT="$(nc -vz -w3 "${HOST}" "${P}" 2>&1)"; RC=$?; set -e
      printf '%s\n' "${OUT}"
      if [ "${RC}" -eq 0 ]; then
        printf '%s' "${OUT}" | grep -qi 'succeeded' || FAILED=$((FAILED+1))
      else
        printf '%s' "${OUT}" | grep -qi 'refused' || FAILED=$((FAILED+1))
      fi ;;
    closed)
      if timeout 3 bash -c "echo > /dev/tcp/${HOST}/${P}" 2>/dev/null; then
        echo "노출 사고: ${P}"; FAILED=$((FAILED+1))
      else
        echo "OK closed: ${P}"
      fi ;;
    open)
      if nc -vz -w3 "${HOST}" "${P}" >/dev/null 2>&1; then
        echo "OK open: ${P}"
      else
        echo "닫힘(기대는 open): ${P}"; FAILED=$((FAILED+1))
      fi ;;
    *) echo "TCP_PROBE=FAIL reason=bad_expect" >&2; exit 2 ;;
  esac
done
test "${FAILED}" -eq 0
echo "TCP_PROBE=PASS expect=${EXPECT} ports=${COUNT}"
