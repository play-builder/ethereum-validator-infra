# AWS Primary 실습 실행 순서

1. [`bootstrap/README.md`](bootstrap/README.md) - AWS 계정, IAM Identity Center와 운영자 확인
2. [`../.github/README.md`](../.github/README.md) - GitHub Organization, ruleset, Environment와 PR
3. [`bootstrap/cicd/README.md`](bootstrap/cicd/README.md) - GitHub OIDC와 Terraform backend
4. [`terraform/README.md`](terraform/README.md) - Primary VPC, IAM, KMS, EC2, EBS와 관측 리소스
5. [`ansible/README.md`](ansible/README.md) - Primary host, EL·BN·VC, MEV와 monitoring

`AWS_REGION`은 Primary 리전 의미로 유지합니다. Standby 리전은 `STANDBY_AWS_REGION`을 사용하며 [`../standby-aws/README.md`](../standby-aws/README.md)에서 별도 관리합니다.
