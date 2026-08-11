# AWS Standby 리전

Standby는 Primary와 다른 AWS 리전에 배포합니다. 평상시에는 Nethermind와 Lighthouse Beacon Node를 동기화하지만 Lighthouse VC는 `masked` 상태라 서명하지 않습니다.

1. [`terraform/README.md`](terraform/README.md)에서 VPC, EC2와 분리 EBS를 배포합니다.
2. [`ansible/README.md`](ansible/README.md)에서 호스트, EL·BN, 관측과 sealed VC를 구성합니다.
3. [`../drills/README.md`](../drills/README.md)의 승인된 계획 전환 전에는 VC mask를 해제하지 않습니다.
