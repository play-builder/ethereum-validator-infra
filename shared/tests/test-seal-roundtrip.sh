#!/usr/bin/env bash
# test-seal-roundtrip.sh — R2(IV 버그) 회귀 테스트: 봉투 왕복 + 오답 거부
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/seal-keystores.sh"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
out="$(bash "$SCRIPT" selftest --work-dir "$W" 2>&1)"; rc=$?
echo "$out"
failed=0
if [ "$rc" -ne 0 ] || ! echo "$out" | grep -q \
    "SELFTEST=OK roundtrip=match wrong_key=rejected mac=compatible,tamper_detected short_dek=rejected"; then
  echo "TEST FAIL: seal selftest (rc=$rc)"
  failed=1
fi
if grep -Eq -- '-macopt[[:space:]].*hexkey:|-pass[[:space:]].*(file|pass|env):' "$SCRIPT"; then
  echo "TEST FAIL: secret-bearing OpenSSL argv source remains"
  failed=1
fi

# A short RNG result must fail immediately after generation, before seal writes
# any ciphertext, wrap, or manifest artifact.
REAL_OPENSSL="$(command -v openssl)"
FAKE_BIN="$W/fake-bin"
mkdir -p "$FAKE_BIN" "$W/seal-source" "$W/seal-work"
printf '{"test":true}\n' >"$W/seal-source/keystore.json"
cat >"$FAKE_BIN/openssl" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "rand" ]; then
  shift
  [ "${1:-}" = "-out" ] || exit 91
  printf '1234567890123456789012345678901' >"$2"
  exit 0
fi
exec "$REAL_OPENSSL" "$@"
EOF
cat >"$FAKE_BIN/stat" <<'EOF'
#!/usr/bin/env bash
printf 'tmpfs\n'
EOF
chmod 0755 "$FAKE_BIN/openssl" "$FAKE_BIN/stat"
set +e
short_rand_out="$(env REAL_OPENSSL="$REAL_OPENSSL" PATH="$FAKE_BIN:$PATH" \
  bash "$SCRIPT" seal --keystore-dir "$W/seal-source" \
  --out-dir "$W/sealed" --work-dir "$W/seal-work" </dev/null 2>&1)"
short_rand_rc=$?
set -e
if [ "$short_rand_rc" -eq 0 ] || ! echo "$short_rand_out" | grep -q 'dek_must_be_32_bytes'; then
  echo "TEST FAIL: short openssl rand output was not rejected immediately"
  failed=1
fi
for artifact in keystores.enc dek.offline.enc dek.kms.b64 manifest.sha256 manifest.json envelope.hmac; do
  if [ -e "$W/sealed/$artifact" ]; then
    echo "TEST FAIL: short openssl rand left artifact=$artifact"
    failed=1
  fi
done

# Use a fixed binary DEK for a real new-format seal. Leading NUL/LF proves the
# payload password is canonical hex text, not the legacy raw first line.
GOOD_BIN="$W/good-bin"
mkdir -p "$GOOD_BIN" "$W/new-work"
cat >"$GOOD_BIN/openssl" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "rand" ]; then
  shift
  [ "${1:-}" = "-out" ] || exit 92
  printf '\000\n0123456789abcdefghijklmnopqrst' >"$2"
  exit 0
fi
exec "$REAL_OPENSSL" "$@"
EOF
cat >"$GOOD_BIN/stat" <<'EOF'
#!/usr/bin/env bash
printf 'tmpfs\n'
EOF
chmod 0755 "$GOOD_BIN/openssl" "$GOOD_BIN/stat"
split_pass_input() {
  printf '%s\n%s\n%s\n%s\n' \
    'abcdefghijkl' 'abcdefghijkl' 'mnopqrstuvwx' 'mnopqrstuvwx'
}
set +e
new_seal_out="$(split_pass_input | env REAL_OPENSSL="$REAL_OPENSSL" PATH="$GOOD_BIN:$PATH" \
  bash "$SCRIPT" seal --keystore-dir "$W/seal-source" \
  --out-dir "$W/new-sealed" --work-dir "$W/new-work" 2>&1)"
new_seal_rc=$?
set -e
if [ "$new_seal_rc" -ne 0 ] || ! echo "$new_seal_out" | grep -q 'SEAL=OK'; then
  echo "TEST FAIL: new hex-password seal failed rc=$new_seal_rc"
  failed=1
elif ! jq -e '.schema == "sealed-keystores/v1" and .payload_password_encoding == "dek-hex-v1"' \
    "$W/new-sealed/manifest.json" >/dev/null; then
  echo "TEST FAIL: new seal manifest lacks payload_password_encoding=dek-hex-v1"
  failed=1
fi
mkdir -p "$W/new-unsealed"
set +e
new_unseal_out="$(split_pass_input | env REAL_OPENSSL="$REAL_OPENSSL" PATH="$GOOD_BIN:$PATH" \
  bash "$SCRIPT" unseal --sealed-dir "$W/new-sealed" \
  --out-dir "$W/new-unsealed" --via-passphrase 2>&1)"
new_unseal_rc=$?
set -e
if [ "$new_unseal_rc" -ne 0 ] || ! echo "$new_unseal_out" | grep -q 'UNSEAL=OK' \
    || ! cmp -s "$W/seal-source/keystore.json" "$W/new-unsealed/keystore.json"; then
  echo "TEST FAIL: new dek-hex-v1 bundle did not round-trip rc=$new_unseal_rc"
  failed=1
fi
manifest_paths_safe=1
while IFS= read -r manifest_line; do
  manifest_member="${manifest_line#*  }"
  case "$manifest_member" in
    ./*) ;;
    *) manifest_paths_safe=0 ;;
  esac
  case "$manifest_member" in
    *'/../'*|'../'*|*/..|/*) manifest_paths_safe=0 ;;
  esac
done <"$W/new-sealed/manifest.sha256"
if [ "$manifest_paths_safe" -ne 1 ]; then
  echo "TEST FAIL: seal manifest contains absolute or unsafe checksum member"
  failed=1
fi
mv "$W/new-sealed" "$W/relocated-sealed"
if ! ( cd "$W/relocated-sealed" && sha256sum -c manifest.sha256 >/dev/null ); then
  echo "TEST FAIL: relocated sealed directory checksum validation failed"
  failed=1
fi

# Build an old v1 fixture exactly as the former implementation did: printable
# 32-byte DEK, payload encrypted with -pass file, and no encoding field.
LEGACY="$W/legacy-sealed"
mkdir -p "$LEGACY" "$W/legacy-unsealed"
printf '0123456789abcdefghijklmnopqrstuv' >"$W/legacy-dek"
tar -C "$W/seal-source" -czf "$W/legacy-plain.tar.gz" .
openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 \
  -pass "file:$W/legacy-dek" -in "$W/legacy-plain.tar.gz" -out "$LEGACY/keystores.enc"
printf '%s' 'abcdefghijklmnopqrstuvwx' | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 \
  -pass stdin -in "$W/legacy-dek" -out "$LEGACY/dek.offline.enc"
legacy_tar_sha="$(sha256sum "$W/legacy-plain.tar.gz" | awk '{print $1}')"
cat >"$LEGACY/manifest.json" <<EOF
{ "schema": "sealed-keystores/v1",
  "created_utc": "2026-08-14T00:00:00Z",
  "pbkdf2_iter": 600000,
  "plaintext_tar_sha256": "$legacy_tar_sha",
  "wrap_a_kms": "none",
  "note": "legacy fixture without payload_password_encoding" }
EOF
( cd "$LEGACY" && sha256sum ./keystores.enc ./dek.offline.enc >manifest.sha256 )
fixture_mac() {
  python3 - "$@" <<'PY'
import hashlib
import hmac
from pathlib import Path
import sys

dek = Path(sys.argv[1]).read_bytes()
key = hmac.new(dek, b"eth-failover/envelope-mac/v1\n", hashlib.sha256).digest()
tag = hmac.new(key, digestmod=hashlib.sha256)
for name in sys.argv[2:]:
    tag.update(Path(name).read_bytes())
print(tag.hexdigest())
PY
}
fixture_mac "$W/legacy-dek" "$LEGACY/keystores.enc" "$LEGACY/dek.offline.enc" \
  "$LEGACY/manifest.json" "$LEGACY/manifest.sha256" >"$LEGACY/envelope.hmac"
set +e
legacy_out="$(split_pass_input | env REAL_OPENSSL="$REAL_OPENSSL" PATH="$GOOD_BIN:$PATH" \
  bash "$SCRIPT" unseal --sealed-dir "$LEGACY" \
  --out-dir "$W/legacy-unsealed" --via-passphrase 2>&1)"
legacy_rc=$?
set -e
if [ "$legacy_rc" -ne 0 ] || ! echo "$legacy_out" | grep -q 'UNSEAL=OK' \
    || ! cmp -s "$W/seal-source/keystore.json" "$W/legacy-unsealed/keystore.json"; then
  echo "TEST FAIL: legacy missing-field bundle did not recover rc=$legacy_rc"
  failed=1
fi

# An explicit unknown encoding is authenticated, then rejected before payload
# decryption/extraction. Only a genuinely missing field selects legacy mode.
UNKNOWN="$W/unknown-sealed"
cp -R "$LEGACY" "$UNKNOWN"
jq '.payload_password_encoding = "unknown-v9"' "$UNKNOWN/manifest.json" \
  >"$UNKNOWN/manifest.json.tmp"
mv "$UNKNOWN/manifest.json.tmp" "$UNKNOWN/manifest.json"
fixture_mac "$W/legacy-dek" "$UNKNOWN/keystores.enc" "$UNKNOWN/dek.offline.enc" \
  "$UNKNOWN/manifest.json" "$UNKNOWN/manifest.sha256" >"$UNKNOWN/envelope.hmac"
mkdir -p "$W/unknown-unsealed"
set +e
unknown_out="$(split_pass_input | env REAL_OPENSSL="$REAL_OPENSSL" PATH="$GOOD_BIN:$PATH" \
  bash "$SCRIPT" unseal --sealed-dir "$UNKNOWN" \
  --out-dir "$W/unknown-unsealed" --via-passphrase 2>&1)"
unknown_rc=$?
set -e
if [ "$unknown_rc" -eq 0 ] || ! echo "$unknown_out" | grep -q 'unknown_payload_password_encoding' \
    || [ -e "$W/unknown-unsealed/keystores.tar.gz" ] \
    || [ -e "$W/unknown-unsealed/keystore.json" ]; then
  echo "TEST FAIL: unknown authenticated encoding was not rejected before payload"
  failed=1
fi
[ "$failed" -eq 0 ] || exit 1
echo "TEST PASS: envelope roundtrip + wrong-key/short-DEK rejection + argv-safe MAC (R2 regression)"
