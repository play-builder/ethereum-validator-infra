#!/usr/bin/env bash
# Recovery KMS region binding: the key ARN is the only region source for wrap-A.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/seal-keystores.sh"

if [ "$(stat -f -c %T /dev/shm 2>/dev/null || true)" != "tmpfs" ]; then
  echo "TEST SKIP: /dev/shm tmpfs is required (run through shared/tests/run-all.sh on Ubuntu)"
  exit 0
fi

ROOT="$(mktemp -d /dev/shm/seal-kms-region.XXXXXX)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/source" "$ROOT/sealed" "$ROOT/unsealed"
printf '{"fixture":"validator-keystore"}\n' >"$ROOT/source/keystore-test.json"

FAKE_AWS_LOG="$ROOT/aws.argv"
export FAKE_AWS_LOG
aws() {
  printf '%s\n' "$*" >>"$FAKE_AWS_LOG"
  local operation="${1:-}:${2:-}" input=""
  shift 2
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --plaintext|--ciphertext-blob) input="${2#fileb://}"; shift 2 ;;
      *) shift ;;
    esac
  done
  case "$operation" in
    kms:encrypt|kms:decrypt)
      [ -f "$input" ] || return 91
      base64 <"$input" | tr -d '\n'
      printf '\n'
      ;;
    *) return 92 ;;
  esac
}
export -f aws

ARN="arn:aws:kms:eu-central-1:123456789012:key/00000000-1111-2222-3333-444444444444"
PASS_INPUT=$'abcdefghijkl\nabcdefghijkl\nmnopqrstuvwx\nmnopqrstuvwx\n'

seal_output="$(printf '%s' "$PASS_INPUT" | bash "$SCRIPT" seal \
  --keystore-dir "$ROOT/source" --out-dir "$ROOT/sealed" \
  --work-dir "$ROOT" --kms-key-arn "$ARN" 2>&1)"
printf '%s\n' "$seal_output"
grep -Eq '^kms encrypt .*--region eu-central-1( |$)' "$FAKE_AWS_LOG"
jq -e --arg arn "$ARN" '.wrap_a_kms == $arn' "$ROOT/sealed/manifest.json" >/dev/null

: >"$FAKE_AWS_LOG"
unseal_output="$(bash "$SCRIPT" unseal \
  --sealed-dir "$ROOT/sealed" --out-dir "$ROOT/unsealed" --via-kms 2>&1)"
printf '%s\n' "$unseal_output"
grep -Eq '^kms decrypt .*--region eu-central-1( |$)' "$FAKE_AWS_LOG"
cmp -s "$ROOT/source/keystore-test.json" "$ROOT/unsealed/keystore-test.json"

: >"$FAKE_AWS_LOG"
set +e
bad_seal_output="$(printf '%s' "$PASS_INPUT" | bash "$SCRIPT" seal \
  --keystore-dir "$ROOT/source" --out-dir "$ROOT/bad-sealed" \
  --work-dir "$ROOT" --kms-key-arn 'arn:aws:kms:not-a-key' 2>&1)"
bad_seal_rc=$?
set -e
[ "$bad_seal_rc" -ne 0 ]
printf '%s\n' "$bad_seal_output" | grep -Fq 'invalid_kms_key_arn'
[ ! -s "$FAKE_AWS_LOG" ]

for invalid_manifest_arn in none arn:aws:kms:not-a-key; do
  broken="$ROOT/broken-${invalid_manifest_arn//[:\/]/-}"
  out="$ROOT/out-${invalid_manifest_arn//[:\/]/-}"
  cp -a "$ROOT/sealed" "$broken"
  jq --arg arn "$invalid_manifest_arn" '.wrap_a_kms = $arn' \
    "$broken/manifest.json" >"$broken/manifest.json.tmp"
  mv "$broken/manifest.json.tmp" "$broken/manifest.json"
  mkdir -p "$out"
  : >"$FAKE_AWS_LOG"
  set +e
  bad_unseal_output="$(bash "$SCRIPT" unseal \
    --sealed-dir "$broken" --out-dir "$out" --via-kms 2>&1)"
  bad_unseal_rc=$?
  set -e
  [ "$bad_unseal_rc" -ne 0 ]
  printf '%s\n' "$bad_unseal_output" | grep -Fq 'invalid_kms_key_arn'
  [ ! -s "$FAKE_AWS_LOG" ]
done

echo "TEST PASS: KMS ARN region bound to encrypt/decrypt; malformed/none rejected before aws"
