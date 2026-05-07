# Ethereum Validator Infrastructure Course 1

이 저장소는 Hoodi에서 AWS Primary validator와 AWS 타 리전 Standby node를 Terraform, GitHub Actions OIDC와 Ansible로 구축하는 강의 실습 코드입니다. 강의 순서에 맞춰 작업 위치의 README를 실행 참조서로 사용합니다.

## 실행 순서

1. [`primary-aws/bootstrap/README.md`](primary-aws/bootstrap/README.md) - AWS 계정과 IAM Identity Center
2. [`.github/README.md`](.github/README.md) - GitHub Organization, ruleset, Environment와 PR
3. [`primary-aws/bootstrap/cicd/README.md`](primary-aws/bootstrap/cicd/README.md) - OIDC PlanRole/ApplyRole와 backend
4. [`primary-aws/terraform/README.md`](primary-aws/terraform/README.md) - AWS Primary 인프라
5. [`primary-aws/ansible/README.md`](primary-aws/ansible/README.md) - Primary EL·CL·Lighthouse VC·관측
6. [`standby-aws/terraform/README.md`](standby-aws/terraform/README.md) - AWS Standby 리전 인프라
7. [`standby-aws/ansible/README.md`](standby-aws/ansible/README.md) - Standby EL·CL과 sealed VC
8. [`shared/README.md`](shared/README.md) - key, slashing protection, gate와 운영 도구
9. [`drills/README.md`](drills/README.md) - 계획 failover와 failback

## Course 1 저장 경계

Lighthouse VC의 keystore와 SQLite slashing protection DB는 validator 상태 EBS에 둡니다. Git에는 example, template과 검증기만 저장하며 실제 `lab.env`, inventory, key, token, slashing export와 evidence는 커밋하지 않습니다.

## 배포 경계

GitHub Actions는 static PR 검증과 protected Environment의 OIDC PlanRole/ApplyRole을 사용합니다. 로컬 장기 access key는 만들지 않습니다. 실제 AWS account와 GitHub repository 값은 placeholder를 교체한 뒤 강의 순서대로 검토합니다.
