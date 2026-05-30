#!/usr/bin/env bash
# seal-keystores.sh — 키스토어 이중 봉투(dual-envelope) seal/unseal (계획서 A2, R2 수정)
#
# 설계:
#   DEK(32B 난수) 1개로 키스토어 tar를 암호화하고, DEK를 두 경로로 독립 래핑한다.
#     wrap-A: AWS KMS (주노드와 다른 리전의 키 — 시나리오 A/D에서 사용)
#     wrap-B: 오프라인 패스프레이즈(운영자 2인이 각자 절반을 입력 — 시나리오 B에서 사용)
#   → AWS 전체 불능이어도 wrap-B로 복호 가능. KMS만으로도, 패스프레이즈만으로도 가능.
#
# R2(IV 버그) 근본 제거: 수동 IV를 아예 쓰지 않는다. openssl enc가 -salt -pbkdf2로
#   키·IV를 내부 파생한다. 왕복 테스트: shared/tests/test-seal-roundtrip.sh
#
# P1-03(감사) 대응 — 인증(무결성) 계층 추가:
#   openssl enc는 GCM/CCM 같은 authenticated encryption을 지원하지 않는다(공식 man:
#   "does not support authenticated encryption modes like CCM and GCM, and will not
#   support such modes in the future"). CBC + 같은 디렉터리의 SHA-256만으로는
#   "변조 감지"가 되지 않는다(공격자가 해시도 함께 바꿀 수 있음).
#   → encrypt-then-MAC을 도입한다: DEK에서 파생한 MAC 키로 암호문과 manifest에
#     HMAC-SHA256을 걸고, 복호 이전에 검증한다. MAC 키를 모르면 위조가 불가능하므로
#     "DEK를 가진 자만 통과"라는 인증이 성립한다.
#
# 수동 경계(D4 준수):
#   - 이 스크립트는 시크릿을 만들지 않는다: 패스프레이즈는 사람이 입력, KMS 호출은
#     운영자 credential(MFA) 하에서만 성공한다.
#   - unseal의 출력 디렉터리는 tmpfs여야 한다(강제 검사). 디스크에 평문을 남기지 않는다.
set -euo pipefail

PBKDF2_ITER=600000
CIPHER=(-aes-256-cbc -salt -pbkdf2 -iter "$PBKDF2_ITER")

die() { echo "SEAL=FAIL reason=$*" >&2; exit 1; }

require_dek_file() { # $1=DEK파일 — 모든 소비 전에 exact 32B regular/readable 확인
  local size
  [ -f "$1" ] && [ -r "$1" ] || die dek_unreadable_or_not_regular
  size="$(wc -c <"$1" | tr -d ' ')" || die dek_unreadable_or_not_regular
  [ "$size" -eq 32 ] || die dek_must_be_32_bytes
}
dek_password_stream() { # $1=hex|legacy-raw $2=DEK파일
  local mode="$1" path="$2"
  require_dek_file "$path"
  # argv에는 encoding과 파일 경로만 둔다. Python이 다시 exact 32B를
  # 확인하고 stdout pipe로만 password bytes를 전달한다.
  python3 - "$mode" "$path" <<'PY'
from pathlib import Path
import sys

mode, path = sys.argv[1:]
try:
    dek = Path(path).read_bytes()
except OSError:
    print("SEAL=FAIL reason=dek_unreadable_or_not_regular", file=sys.stderr)
    raise SystemExit(1)
if len(dek) != 32:
    print("SEAL=FAIL reason=dek_must_be_32_bytes", file=sys.stderr)
    raise SystemExit(1)
if mode == "hex":
    sys.stdout.write(dek.hex())
elif mode == "legacy-raw":
    sys.stdout.buffer.write(dek)
else:
    print("SEAL=FAIL reason=internal_dek_password_mode", file=sys.stderr)
    raise SystemExit(1)
PY
}
dek_password_hex() { # $1=DEK파일 — canonical 64-char hex text
  dek_password_stream hex "$1"
}
dek_password_legacy_raw() { # $1=DEK파일 — v1 missing-field recovery only
  dek_password_stream legacy-raw "$1"
}
mac_over() { # $1=DEK파일 $2..=대상 파일들 → 정규화된 HMAC 한 줄
  # argv에는 파일 경로만 둔다. DEK와 파생 MAC key는 Python 프로세스 메모리
  # 안에서만 다루며, 기존 OpenSSL 구현의 domain trailing LF까지 보존한다.
  python3 - "$@" <<'PY'
import hashlib
import hmac
from pathlib import Path
import sys


def fail(reason: str) -> None:
    print(f"SEAL=FAIL reason={reason}", file=sys.stderr)
    raise SystemExit(1)


if len(sys.argv) < 3:
    fail("mac_input_missing")

try:
    dek = Path(sys.argv[1]).read_bytes()
except OSError:
    fail("dek_unreadable")
if len(dek) != 32:
    fail("dek_must_be_32_bytes")

mac_key = hmac.new(
    dek,
    b"eth-failover/envelope-mac/v1\n",
    hashlib.sha256,
).digest()
envelope_mac = hmac.new(mac_key, digestmod=hashlib.sha256)
try:
    for raw_path in sys.argv[2:]:
        with Path(raw_path).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                envelope_mac.update(chunk)
except OSError:
    fail("mac_input_unreadable")

print(envelope_mac.hexdigest())
PY
}
is_tmpfs() { [ "$(stat -f -c %T "$1" 2>/dev/null)" = "tmpfs" ]; }

kms_region_from_key_arn() {
  local arn="${1:-}"
  if [[ "$arn" =~ ^arn:[^:]+:kms:([a-z0-9-]+):[0-9]{12}:key/.+$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  die "invalid_kms_key_arn value=${arn:-missing}"
}

usage() {
  cat <<'EOF'
usage:
  seal-keystores.sh seal   --keystore-dir DIR --out-dir DIR --work-dir TMPFS_DIR
                           [--kms-key-arn ARN]        # 있으면 wrap-A 수행(aws cli 필요)
  seal-keystores.sh unseal --sealed-dir DIR --out-dir TMPFS_DIR
                           (--via-kms | --via-passphrase)
  seal-keystores.sh selftest --work-dir DIR            # R2 회귀 테스트용(더미 데이터)
EOF
  exit 64
}

read_split_passphrase() { # 2인이 각자 절반 입력 → 결합. 화면 비표시.
  local half1 half2 confirm1 confirm2
  echo "운영자 1: 패스프레이즈 절반 입력 (기록 금지, 종이 보관본과 일치해야 함)" >&2
  read -rs half1; echo >&2
  echo "운영자 1: 재입력" >&2; read -rs confirm1; echo >&2
  [ "$half1" = "$confirm1" ] || die passphrase_half1_mismatch
  echo "운영자 2: 패스프레이즈 절반 입력" >&2
  read -rs half2; echo >&2
  echo "운영자 2: 재입력" >&2; read -rs confirm2; echo >&2
  [ "$half2" = "$confirm2" ] || die passphrase_half2_mismatch
  [ "${#half1}" -ge 12 ] && [ "${#half2}" -ge 12 ] || die passphrase_half_too_short_min12
  printf '%s%s' "$half1" "$half2"
}

do_seal() {
  local KDIR="" ODIR="" WDIR="" KMS_ARN="" KMS_REGION=""
  while [ $# -gt 0 ]; do case "$1" in
    --keystore-dir) KDIR="$2"; shift 2 ;; --out-dir) ODIR="$2"; shift 2 ;;
    --work-dir) WDIR="$2"; shift 2 ;; --kms-key-arn) KMS_ARN="$2"; shift 2 ;;
    *) usage ;; esac; done
  [ -d "$KDIR" ] && [ -n "$ODIR" ] && [ -n "$WDIR" ] || usage
  is_tmpfs "$WDIR" || die "work-dir must be tmpfs (평문 tar/DEK가 디스크에 닿으면 안 됨): $WDIR"
  if [ -n "$KMS_ARN" ]; then
    KMS_REGION="$(kms_region_from_key_arn "$KMS_ARN")"
  fi
  mkdir -p "$ODIR"; chmod 700 "$ODIR"

  local DEK="$WDIR/dek.bin" TAR="$WDIR/keystores.tar.gz"
  umask 077
  openssl rand -out "$DEK" 32
  require_dek_file "$DEK"
  tar -C "$KDIR" -czf "$TAR" .
  local tar_sha; tar_sha="$(sha256sum "$TAR" | awk '{print $1}')"

  dek_password_hex "$DEK" | openssl enc "${CIPHER[@]}" -pass stdin \
    -in "$TAR" -out "$ODIR/keystores.enc"

  # wrap-B (오프라인, 항상 수행 — 시나리오 B의 생명줄)
  local PASS; PASS="$(read_split_passphrase)"
  printf '%s' "$PASS" | openssl enc "${CIPHER[@]}" -pass stdin \
    -in "$DEK" -out "$ODIR/dek.offline.enc"
  unset PASS

  # wrap-A (KMS — 주노드와 다른 리전 키를 지정할 것)
  if [ -n "$KMS_ARN" ]; then
    command -v aws >/dev/null || die "aws cli 없음 — wrap-A를 하려면 운영자 워크스테이션에서 실행"
    aws kms encrypt --region "$KMS_REGION" --key-id "$KMS_ARN" \
      --plaintext "fileb://$DEK" --query CiphertextBlob --output text \
      >"$ODIR/dek.kms.b64" || die kms_encrypt_failed
    echo "WRAP_A=KMS_OK key=$KMS_ARN region=$KMS_REGION"
  else
    echo "WRAP_A=SKIPPED (오프라인 wrap-B만 생성됨 — 운영 정책상 wrap-A도 권장)"
  fi

  ( cd "$ODIR" && sha256sum ./keystores.enc ./dek.* >manifest.sha256 )
  cat >"$ODIR/manifest.json" <<EOF
{ "schema": "sealed-keystores/v1",
  "payload_password_encoding": "dek-hex-v1",
  "created_utc": "$(date -u +%FT%TZ)",
  "pbkdf2_iter": $PBKDF2_ITER,
  "plaintext_tar_sha256": "$tar_sha",
  "wrap_a_kms": "${KMS_ARN:-none}",
  "note": "복호는 RB-01 F4(2인 승인) 이후 tmpfs에서만. plaintext_tar_sha256은 unseal 무결성 검증용." }
EOF
  # encrypt-then-MAC: 암호문 + wrap들 + manifest 전체에 대한 HMAC (P1-03)
  ( cd "$ODIR" && mac_over "$DEK" keystores.enc dek.offline.enc $( [ -f dek.kms.b64 ] && echo dek.kms.b64 ) manifest.json manifest.sha256 ) \
    >"$ODIR/envelope.hmac"
  chmod 0400 "$ODIR/envelope.hmac"
  shred -u "$DEK" "$TAR"
  echo "SEAL=OK out=$ODIR mac=envelope.hmac"
  echo "평문(tar/DEK)은 tmpfs에서 shred 후 언마운트할 것 — shred는 파기 시도이지 파기 보증이 아니다(P2-03)."
}

do_unseal() {
  local SDIR="" ODIR="" VIA=""
  while [ $# -gt 0 ]; do case "$1" in
    --sealed-dir) SDIR="$2"; shift 2 ;; --out-dir) ODIR="$2"; shift 2 ;;
    --via-kms) VIA=kms; shift ;; --via-passphrase) VIA=pass; shift ;;
    *) usage ;; esac; done
  [ -d "$SDIR" ] && [ -n "$ODIR" ] && [ -n "$VIA" ] || usage
  mkdir -p "$ODIR"
  is_tmpfs "$ODIR" || die "unseal out-dir must be tmpfs (RB-01 F4): $ODIR"
  umask 077
  ( cd "$SDIR" && sha256sum -c manifest.sha256 >/dev/null ) || die manifest_checksum_mismatch

  local DEK="$ODIR/.dek.bin"
  if [ "$VIA" = "kms" ]; then
    local KMS_ARN KMS_REGION
    KMS_ARN="$(jq -r '.wrap_a_kms // "none"' "$SDIR/manifest.json")" \
      || die manifest_wrap_a_kms_unreadable
    KMS_REGION="$(kms_region_from_key_arn "$KMS_ARN")"
    command -v aws >/dev/null || die aws_cli_missing
    base64 -d "$SDIR/dek.kms.b64" >"$ODIR/.dek.cipher"
    aws kms decrypt --region "$KMS_REGION" \
      --ciphertext-blob "fileb://$ODIR/.dek.cipher" \
      --query Plaintext --output text | base64 -d >"$DEK" || die kms_decrypt_failed
    rm -f "$ODIR/.dek.cipher"
  else
    local PASS; PASS="$(read_split_passphrase)"
    printf '%s' "$PASS" | openssl enc -d "${CIPHER[@]}" -pass stdin \
      -in "$SDIR/dek.offline.enc" -out "$DEK" || die offline_unwrap_failed_wrong_passphrase
    unset PASS
  fi

  # P1-03: 복호 "이전에" 인증. MAC 불일치면 암호문을 건드리지 않고 즉시 중단한다.
  [ -f "$SDIR/envelope.hmac" ] || { shred -u "$DEK" 2>/dev/null; die envelope_hmac_missing; }
  want_mac="$(cat "$SDIR/envelope.hmac")"
  got_mac="$( cd "$SDIR" && mac_over "$DEK" keystores.enc dek.offline.enc $( [ -f dek.kms.b64 ] && echo dek.kms.b64 ) manifest.json manifest.sha256 )"
  if [ "$want_mac" != "$got_mac" ]; then
    shred -u "$DEK" 2>/dev/null || true
    die "envelope_authentication_failed (변조 또는 잘못된 키 — 복호를 수행하지 않음)"
  fi
  echo "AUTH=OK envelope_hmac=verified"

  # The encoding selector is trusted only after the manifest itself passed the
  # envelope HMAC. Missing means legacy v1 raw first-line semantics; any
  # explicit value other than dek-hex-v1 is rejected before payload decrypt.
  jq -e 'type == "object"' "$SDIR/manifest.json" >/dev/null \
    || { shred -u "$DEK" 2>/dev/null || true; die manifest_json_invalid; }
  local PASSWORD_READER PAYLOAD_PASSWORD_ENCODING
  if jq -e 'has("payload_password_encoding")' "$SDIR/manifest.json" >/dev/null; then
    PAYLOAD_PASSWORD_ENCODING="$(jq -r '.payload_password_encoding' "$SDIR/manifest.json")" \
      || { shred -u "$DEK" 2>/dev/null || true; die manifest_payload_password_encoding_unreadable; }
    case "$PAYLOAD_PASSWORD_ENCODING" in
      dek-hex-v1) PASSWORD_READER=dek_password_hex ;;
      *)
        shred -u "$DEK" 2>/dev/null || true
        die "unknown_payload_password_encoding value=$PAYLOAD_PASSWORD_ENCODING"
        ;;
    esac
  else
    PASSWORD_READER=dek_password_legacy_raw
  fi

  "$PASSWORD_READER" "$DEK" | openssl enc -d "${CIPHER[@]}" -pass stdin \
    -in "$SDIR/keystores.enc" -out "$ODIR/keystores.tar.gz" || die payload_decrypt_failed
  local want got
  want="$(jq -r '.plaintext_tar_sha256' "$SDIR/manifest.json")"
  got="$(sha256sum "$ODIR/keystores.tar.gz" | awk '{print $1}')"
  [ "$want" = "$got" ] || die "plaintext_integrity_mismatch want=$want got=$got"
  tar -C "$ODIR" -xzf "$ODIR/keystores.tar.gz"
  shred -u "$DEK" "$ODIR/keystores.tar.gz"
  echo "UNSEAL=OK out=$ODIR authentication=hmac-verified plaintext_digest=match"
  echo "다음 단계는 RB-01 F6: stopped 상태 SP import → lighthouse 키 임포트 → 사용 후 shred."
}

do_selftest() { # R2 회귀: 왕복 + 오답 거부. 실제 키를 쓰지 않는다.
  local WDIR=""
  while [ $# -gt 0 ]; do case "$1" in --work-dir) WDIR="$2"; shift 2 ;; *) usage ;; esac; done
  [ -n "$WDIR" ] || usage
  mkdir -p "$WDIR"; umask 077
  local D="$WDIR/dek" P="$WDIR/plain" E="$WDIR/enc" R="$WDIR/round"
  local M1="$WDIR/mac-a" M2="$WDIR/mac-b" SHORT="$WDIR/dek.short"
  # Deterministic binary DEK: a raw-password implementation must fail on the
  # leading NUL/newline instead of passing or failing probabilistically.
  printf '\000\n0123456789abcdefghijklmnopqrst' >"$D"
  [ "$(wc -c <"$D" | tr -d ' ')" -eq 32 ] || die selftest_dek_fixture_size
  head -c 4096 /dev/urandom >"$P"
  dek_password_hex "$D" | openssl enc "${CIPHER[@]}" -pass stdin -in "$P" -out "$E"
  dek_password_hex "$D" | openssl enc -d "${CIPHER[@]}" -pass stdin -in "$E" -out "$R"
  cmp -s "$P" "$R" || die selftest_roundtrip_mismatch
  printf 'ABCDEFGHIJKLMNOPQRSTUVWXYZ012345' >"$D.wrong"
  if dek_password_hex "$D.wrong" | openssl enc -d "${CIPHER[@]}" -pass stdin \
      -in "$E" -out /dev/null 2>/dev/null; then
    die selftest_wrong_key_accepted
  fi
  # Preserve the pre-migration envelope MAC bytes while rejecting malformed
  # DEKs before HMAC calculation.
  printf 'compat\000\npayload' >"$M1"
  printf '\377tail\n' >"$M2"
  [ "$(mac_over "$D" "$M1" "$M2")" = \
    "6018860970343fed6de568e029920bb0a6598eaf084b0a90b5c10ab238a99514" ] \
    || die selftest_mac_compatibility_mismatch
  head -c 31 "$D" >"$SHORT"
  if { dek_password_hex "$SHORT" | openssl enc "${CIPHER[@]}" -pass stdin \
      -in "$P" -out /dev/null; } 2>"$WDIR/short-pass.err"; then
    die selftest_short_dek_password_accepted
  fi
  grep -q 'dek_must_be_32_bytes' "$WDIR/short-pass.err" \
    || die selftest_short_dek_password_reason_missing
  if mac_over "$SHORT" "$M1" >/dev/null 2>"$WDIR/short-dek.err"; then
    die selftest_short_dek_accepted
  fi
  # P1-03: MAC 왕복 + 1바이트 변조 거부
  m1="$(mac_over "$D" "$E")"
  printf 'x' >>"$E"
  m2="$(mac_over "$D" "$E")"
  [ "$m1" != "$m2" ] || die selftest_mac_insensitive_to_tamper
  rm -f "$D" "$D.wrong" "$P" "$E" "$R" "$M1" "$M2" "$SHORT" \
    "$WDIR/short-pass.err" "$WDIR/short-dek.err"
  echo "SELFTEST=OK roundtrip=match wrong_key=rejected mac=compatible,tamper_detected short_dek=rejected iter=$PBKDF2_ITER"
}

case "${1:-}" in
  seal) shift; do_seal "$@" ;;
  unseal) shift; do_unseal "$@" ;;
  selftest) shift; do_selftest "$@" ;;
  *) usage ;;
esac
