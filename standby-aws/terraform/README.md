# AWS Standby Terraform 실행 참조

## 1. 입력값 준비

`ci/runtime-inputs.json`의 repository·account·role·bucket placeholder를 Primary bootstrap output과 일치시킵니다. `.aws.region`은 Primary `AWS_REGION`과 다른 리전이고 `.aws.state_key`는 Standby 전용 key여야 합니다.

## 2. 로컬 정적 검사

```bash
terraform -chdir=standby-aws/terraform fmt -check -recursive
terraform -chdir=standby-aws/terraform init -backend=false -lockfile=readonly
terraform -chdir=standby-aws/terraform validate
python3 standby-aws/terraform/tests/test_standby_contract.py
```

## 3. PR과 saved plan

`standby-terraform-static` required check를 통과시킵니다. merge 뒤 `Standby Terraform deploy` workflow를 `main`에서 실행하고 plan diff의 리전, VPC, EC2, EBS와 Security Group을 검토합니다. 승인자는 protected `hoodi-testnet-dev` Environment에서 apply를 승인합니다.

## 4. 완료 확인

```bash
terraform -chdir=standby-aws/terraform output
aws ec2 describe-instances --profile hoodi-testnet-dev-builder --region eu-central-1
aws ec2 describe-volumes --profile hoodi-testnet-dev-builder --region eu-central-1
```

EC2는 IMDSv2 required와 termination protection, chain·validator EBS는 encryption과 `prevent_destroy`를 유지해야 합니다.
