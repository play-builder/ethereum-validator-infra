#!/usr/bin/env bash
# Course 1 lab static and local-fixture test battery. No AWS mutation.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TOTAL=0

run() {
  TOTAL=$((TOTAL + 1))
  printf '\n=== %s ===\n' "$1"
  shift
  "$@"
}

run "enabled repository contracts" bash -c '
  set -euo pipefail
  cd "$1"
  while IFS= read -r suite; do
    case "$suite" in ""|\#*) continue ;; esac
    test -f "$suite"
    case "$suite" in
      *.py) python3 "$suite" ;;
      *.sh) bash "$suite" ;;
      *) printf "unsupported suite: %s\n" "$suite" >&2; exit 1 ;;
    esac
  done < tests/enabled.txt
' _ "$ROOT"

run "client release lock" python3 "$HERE/test_client_release_lock.py"
run "active-standby state authority" python3 "$HERE/test_active_standby_state_contract.py"
run "incident classification" python3 "$HERE/test_incident_classification.py"
run "durable evidence store" python3 "$HERE/test-vc-evidence-store.py"
run "validator gate fixtures" bash "$HERE/test-vc-gate.sh"
run "absence observation fixtures" bash "$HERE/test-observe-absence.sh"
run "keystore envelope roundtrip" bash "$HERE/test-seal-roundtrip.sh"
run "KMS ARN region binding" bash "$HERE/test-seal-kms-region.sh"
run "validator lease self-fence" bash "$HERE/test-vc-lease.sh"
run "Terraform CI boundary" bash "$HERE/test-terraform-cicd-boundary.sh"
run "Terraform version and lock" bash "$HERE/test-terraform-contract.sh"

printf '\nSUITES=%s FAILED=0\nSHARED_TESTS=PASS\n' "$TOTAL"
