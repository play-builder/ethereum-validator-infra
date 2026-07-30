# RB-04: Validator key 재배치

암호화된 keystore를 대상 signer에 import하는 실행 참조입니다. 평문은 운영자 workstation과 대상 EC2의 tmpfs에서만 다루고, VC는 전체 작업 동안 `inactive`와 `masked` 상태를 유지합니다.

## 1. 대상과 session 확인

```bash
source lab.env
aws sso login --profile "${OP1_AWS_PROFILE}"
aws sts get-caller-identity --profile "${OP1_AWS_PROFILE}"
ssh "${TARGET_HOST}" \
  'systemctl is-active lighthouse-validator.service; systemctl is-enabled lighthouse-validator.service'
```

정상 시작 상태는 `inactive`와 `masked`입니다.

## 2. 로컬 tmpfs에서 봉인 해제

```bash
bash shared/scripts/prepare-operator-tmpfs.sh --path /run/ceremony --size 64m
install -d -m 0700 /run/ceremony/restored
AWS_PROFILE="${OP1_AWS_PROFILE}" bash shared/scripts/seal-keystores.sh unseal \
  --sealed-dir "${SEALED_KEY_DIR}" \
  --out-dir /run/ceremony/restored \
  --via-kms
find /run/ceremony/restored -maxdepth 2 -type f -print
```

`AUTH=OK`와 `UNSEAL=OK`를 확인합니다. keystore 내용이나 password를 화면과 로그에 출력하지 않습니다.

## 3. 대상 tmpfs로 전송하고 import

```bash
ssh "${TARGET_HOST}" \
  'sudo install -d -o lighthouse-validator -g ethereum -m 0700 /run/validator-key-import'
tar -C /run/ceremony/restored -cf - . | \
  ssh "${TARGET_HOST}" \
    'sudo -u lighthouse-validator tar -C /run/validator-key-import -xf -'
ssh "${TARGET_HOST}" sudo -u lighthouse-validator \
  /opt/ethereum/lighthouse/current/lighthouse \
  --network hoodi \
  --datadir /var/lib/validator-state/lighthouse/validator \
  account validator import \
  --directory /run/validator-key-import
```

imported validator public key가 이번 과정의 public key와 일치해야 합니다. key import만으로 VC를 시작하지 않으며, 최신 slashing protection import와 승인 gate가 별도로 필요합니다.

## 4. 평문 정리

```bash
find /run/ceremony/restored -type f -exec shred -u {} \;
ssh "${TARGET_HOST}" \
  'sudo find /run/validator-key-import -type f -exec shred -u {} \; && sudo rm -rf /run/validator-key-import'
sudo umount /run/ceremony
```

대상 VC가 여전히 `inactive`와 `masked`인지 확인하고 작업 기록에는 public identity와 정리 시각만 남깁니다.
