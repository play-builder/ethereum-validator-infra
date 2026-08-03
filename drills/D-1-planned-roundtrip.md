# D-1: AWS 두 리전 계획 전환 왕복

## 목표

정상 운영 중인 AWS Primary에서 AWS Standby로 signer를 한 번 옮긴 뒤 다시 Primary로 복귀합니다. 모든 단계는 두 운영자가 함께 수행하며 한 시점에 VC 하나만 서명합니다.

## 실행

1. Primary와 Standby EL/BN sync, UTC 시각과 Hoodi genesis root를 확인합니다.
2. `shared/runbooks/RB-01-failover.md`를 순서대로 실행합니다.
3. Primary EC2가 `stopped`이고 Standby VC만 attestation을 발행하는지 기록합니다.
4. 계획한 관찰 시간이 지난 뒤 `shared/runbooks/RB-02-failback.md`를 실행합니다.
5. Standby VC가 sealed 상태이고 Primary VC만 attestation을 발행하는지 기록합니다.

## 완료 기록

- Primary와 Standby provider 상태
- source VC 정지 시각과 EIP-3076 export SHA-256
- source fence, checklist, approval token과 imported SQLite SHA-256
- 각 방향의 `GATE=PASS`
- doppelganger protection 관찰 구간과 첫 정상 attestation 시각

위 항목이 한 failover record와 한 failback record에 모두 채워지면 왕복 실습이 끝납니다.
