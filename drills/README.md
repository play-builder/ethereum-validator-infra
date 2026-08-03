# AWS Primary/Standby 계획 전환 실행 참조

Course 1에서는 두 리전이 모두 정상인 상태에서 계획된 failover와 failback 한 번만 수행합니다. 장애를 일부러 만들거나 최신 slashing protection 없이 VC를 시작하는 실습은 하지 않습니다.

## 진행 순서

1. `D-1-planned-roundtrip.md`에서 전체 왕복 순서를 확인합니다.
2. `shared/runbooks/RB-01-failover.md`로 Primary에서 Standby로 전환합니다.
3. Standby가 유일한 signer인지 확인합니다.
4. `shared/runbooks/RB-02-failback.md`로 Primary에 복귀합니다.
5. Primary가 유일한 signer이고 Standby VC가 sealed 상태인지 기록합니다.

자동 failover는 구현하지 않습니다. fresh export, source EC2 hard fence, slashing protection import, two-person approval과 doppelganger protection을 모두 통과해야 다음 signer를 시작합니다.
