# RB-02: AWS Standby에서 AWS Primary로 계획 복귀

Failback은 현재 유일한 signer인 Standby를 source로 보고 RB-01의 방향을 반대로 적용합니다. Standby가 정상 서명 중이므로 계획 정지 창에서 진행합니다.

## 1. Primary 준비

Primary EC2를 시작하고 EL/BN sync와 Hoodi genesis root를 확인합니다. Primary VC는 `inactive`, `masked`, `SEALED` 상태를 유지합니다.

## 2. Standby VC 정지와 최신 export

```bash
sudo systemctl stop lighthouse-validator.service
sudo systemctl disable lighthouse-validator.service
sudo systemctl mask lighthouse-validator.service
sudo rm -f /etc/ethereum/failover/approval.token
sudo touch /etc/ethereum/failover/SEALED
test "$(systemctl is-active lighthouse-validator.service)" = "inactive"
! pgrep -af 'lighthouse([[:space:]]+|.*/)vc'

sudo install -d -o lighthouse-validator -g ethereum -m 0700 /run/ethereum-sp-export
sudo -u lighthouse-validator /opt/ethereum/lighthouse/current/lighthouse \
  --network hoodi \
  --datadir /var/lib/validator-state/lighthouse/validator \
  account validator slashing-protection export \
  /run/ethereum-sp-export/standby-latest.json
sudo -u lighthouse-validator python3 /usr/local/sbin/validate-ethereum-slashing-interchange \
  --interchange /run/ethereum-sp-export/standby-latest.json \
  --expected-genesis-validators-root 0x212f13fc4df078b6cb7db228f1c8307566dcecf900867401a92023d7ba99cb5f \
  --expected-pubkey '<VALIDATOR_PUBKEY>'
sudo sha256sum /run/ethereum-sp-export/standby-latest.json
```

export를 운영자 workstation과 Primary의 tmpfs로 옮긴 뒤 Standby VC 정지를 다시 확인합니다.

## 3. Standby EC2 hard fence

```bash
source lab.env
aws ec2 stop-instances --instance-ids "${STANDBY_INSTANCE_ID}" \
  --region "${STANDBY_AWS_REGION}" --profile "${OP1_AWS_PROFILE}"
aws ec2 wait instance-stopped --instance-ids "${STANDBY_INSTANCE_ID}" \
  --region "${STANDBY_AWS_REGION}" --profile "${OP1_AWS_PROFILE}"
aws ec2 describe-instances --instance-ids "${STANDBY_INSTANCE_ID}" \
  --region "${STANDBY_AWS_REGION}" --profile "${OP2_AWS_PROFILE}" \
  --query 'Reservations[0].Instances[0].State.Name' --output text
```

정상 결과 `stopped`와 조회 시각을 failback 기록에 남깁니다.

## 4. Primary import와 시작

Primary VC가 계속 masked 상태인지 확인한 뒤 최신 Standby export를 import합니다.

```bash
sudo install -d -o lighthouse-validator -g ethereum -m 0700 /run/ethereum-sp-import
sudo install -o lighthouse-validator -g ethereum -m 0400 \
  /home/ubuntu/standby-latest.json /run/ethereum-sp-import/standby-latest.json
sudo -u lighthouse-validator /opt/ethereum/lighthouse/current/lighthouse \
  --network hoodi \
  --datadir /var/lib/validator-state/lighthouse/validator \
  account validator slashing-protection import \
  /run/ethereum-sp-import/standby-latest.json
sudo sha256sum /run/ethereum-sp-import/standby-latest.json \
  /var/lib/validator-state/lighthouse/validator/validators/slashing_protection.sqlite
```

`vc-start-primary` 범위로 새 source fence, checklist, `sp-import-approved`와 approval token을 배치합니다. 두 운영자가 해시를 확인한 뒤 Primary에서 실행합니다.

```bash
sudo rm -f /etc/ethereum/failover/SEALED
sudo VC_GATE_ENV=/etc/ethereum/failover/gate.env /usr/local/sbin/vc-gate
sudo systemctl unmask lighthouse-validator.service
sudo systemctl enable --now lighthouse-validator.service
sudo journalctl -u lighthouse-validator.service --since '-10 min' --no-pager
```

`GATE=PASS`, doppelganger protection 관찰 구간과 Primary attestation을 확인합니다. 이후 Standby EC2를 다시 시작하더라도 Standby VC는 `masked`와 `SEALED`를 유지합니다.
