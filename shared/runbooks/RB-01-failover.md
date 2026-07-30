# RB-01: AWS Primary에서 AWS Standby로 계획 전환

이 런북은 두 리전이 모두 정상이고 최신 EIP-3076 export를 만들 수 있는 계획 전환에만 사용합니다. 자동 failover는 사용하지 않으며, Primary와 Standby가 동시에 서명하는 구간을 만들지 않습니다.

## 1. 준비 상태

- Primary가 현재 유일한 signer입니다.
- Standby EL/BN은 Hoodi 동기화가 끝났고 VC는 `inactive`와 `masked`입니다.
- 두 운영자가 `source lab.env` 후 각자 SSO 로그인을 마쳤습니다.
- `shared/evidence/templates/failover-gate-record.example.md`를 이번 작업 기록으로 복사했습니다.

## 2. Primary VC 정지와 최신 export

Primary에 접속해 VC를 먼저 정지합니다. EIP-3076 export는 VC가 멈춘 상태에서만 만듭니다.

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
  /run/ethereum-sp-export/primary-latest.json
sudo -u lighthouse-validator python3 /usr/local/sbin/validate-ethereum-slashing-interchange \
  --interchange /run/ethereum-sp-export/primary-latest.json \
  --expected-genesis-validators-root 0x212f13fc4df078b6cb7db228f1c8307566dcecf900867401a92023d7ba99cb5f \
  --expected-pubkey '<VALIDATOR_PUBKEY>'
sudo sha256sum /run/ethereum-sp-export/primary-latest.json
```

export JSON과 SHA-256을 운영자 workstation으로 가져온 뒤 Primary VC가 계속 정지 상태인지 다시 확인합니다.

## 3. Primary EC2 hard fence

로컬 저장소 루트에서 실행합니다. `instance-stopped` 대기가 끝난 뒤 Operator 2가 별도 session으로 같은 상태를 조회합니다.

```bash
source lab.env
PRIMARY_INSTANCE_ID="$(terraform -chdir=primary-aws/terraform output -raw instance_id)"
aws ec2 stop-instances --instance-ids "${PRIMARY_INSTANCE_ID}" \
  --region "${AWS_REGION}" --profile "${OP1_AWS_PROFILE}"
aws ec2 wait instance-stopped --instance-ids "${PRIMARY_INSTANCE_ID}" \
  --region "${AWS_REGION}" --profile "${OP1_AWS_PROFILE}"
aws ec2 describe-instances --instance-ids "${PRIMARY_INSTANCE_ID}" \
  --region "${AWS_REGION}" --profile "${OP2_AWS_PROFILE}" \
  --query 'Reservations[0].Instances[0].State.Name' --output text
```

정상 결과는 `stopped`입니다. 이 조회 결과를 `provider-stopped` hard fence 증거로 기록합니다.

## 4. Standby에 이력과 key 준비

`RB-04-key-ceremony-restage.md`로 validator key를 Standby에 import하되 VC는 계속 masked 상태로 둡니다. 이어서 최신 export를 Standby의 tmpfs에 놓고 import합니다.

```bash
sudo install -d -o lighthouse-validator -g ethereum -m 0700 /run/ethereum-sp-import
sudo install -o lighthouse-validator -g ethereum -m 0400 \
  /home/ubuntu/primary-latest.json /run/ethereum-sp-import/primary-latest.json
sudo -u lighthouse-validator /opt/ethereum/lighthouse/current/lighthouse \
  --network hoodi \
  --datadir /var/lib/validator-state/lighthouse/validator \
  account validator slashing-protection import \
  /run/ethereum-sp-import/primary-latest.json
sudo sha256sum /run/ethereum-sp-import/primary-latest.json \
  /var/lib/validator-state/lighthouse/validator/validators/slashing_protection.sqlite
```

## 5. 승인 gate와 Standby VC 시작

`shared/scripts/make-approval-token.md` 형식으로 `vc-start-failover` token, `source-fence.json`, checklist와 `sp-import-approved`를 배치합니다. 두 운영자가 exact SHA-256을 확인한 뒤 실행합니다.

```bash
sudo rm -f /etc/ethereum/failover/SEALED
sudo VC_GATE_ENV=/etc/ethereum/failover/gate.env /usr/local/sbin/vc-gate
sudo systemctl unmask lighthouse-validator.service
sudo systemctl enable --now lighthouse-validator.service
sudo journalctl -u lighthouse-validator.service --since '-10 min' --no-pager
```

`GATE=PASS`, doppelganger protection 관찰 구간, Standby의 정상 attestation을 순서대로 확인합니다. Primary는 `stopped`, Standby만 `active`인 상태를 기록하면 계획 전환이 끝납니다.
