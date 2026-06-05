# Shared 운영 도구 실행 참조

`shared/scripts`는 Primary와 Standby가 함께 사용하는 validator gate, slashing protection 검증, key 봉인과 evidence 도구입니다.

## 주요 도구

- `validate-slashing-interchange.py`: EIP-3076 JSON 구조와 대상 validator identity 확인
- `vc-gate.sh`: 승인 token, source fence, BN identity, slashing import와 lease 검사
- `observe-absence.sh`: 독립 Beacon API에서 서명 부재를 관찰하는 보강 증거
- `seal-keystores.sh`: KMS 이중 봉투 ciphertext 생성
- `transition-active-standby-state.py`: 현재 signer 상태 전이 기록

## 정적 검사

```bash
bash shared/tests/run-all.sh
```

정적 검사가 실제 Hoodi 서명, AWS KMS 호출이나 리전 failover를 증명하지는 않습니다.

## 오프라인 key ceremony용 deposit CLI 준비

온라인 PC에서 EthStaker v1.3.0 Linux amd64 자산을 받고, 저장소의 고정 SHA-256과 GitHub artifact attestation을 모두 확인합니다.

```bash
DEPOSIT_CLI_ARCHIVE="ethstaker_deposit-cli-d8016bc-linux-amd64.tar.gz"
DEPOSIT_CLI_URL="https://github.com/ethstaker/ethstaker-deposit-cli/releases/download/v1.3.0/${DEPOSIT_CLI_ARCHIVE}"
DEPOSIT_CLI_SHA256="89ecdfd5bb312c723b1feb7e09762be2510fd75df03d91876fad7f247b7238f2"

curl --fail --location --proto '=https' --tlsv1.2 \
  "$DEPOSIT_CLI_URL" --output "$DEPOSIT_CLI_ARCHIVE"
printf '%s  %s\n' "$DEPOSIT_CLI_SHA256" "$DEPOSIT_CLI_ARCHIVE" \
  | shasum -a 256 --check
gh attestation verify "$DEPOSIT_CLI_ARCHIVE" \
  --repo ethstaker/ethstaker-deposit-cli
```

정상 결과는 checksum의 `OK`와 attestation 명령의 종료 코드 `0`입니다. 검증한 archive만 오프라인 PC로 옮기고, 오프라인 PC에서도 같은 SHA-256을 다시 확인한 후 풉니다.

```bash
printf '%s  %s\n' "$DEPOSIT_CLI_SHA256" "$DEPOSIT_CLI_ARCHIVE" \
  | shasum -a 256 --check
tar -xzf "$DEPOSIT_CLI_ARCHIVE"
./ethstaker_deposit-cli-d8016bc-linux-amd64/deposit --version
```
