# AWS Primary 인프라 Terraform 실행 순서

이 README는 강의 영상에서 현재 디렉터리의 파일을 사용할 때 함께 보는 실행 참조서입니다. 명령은 저장소 루트에서 실행하며, AWS Console과 GitHub 화면의 설명은 강의를 따릅니다.

각 단계는 위에서 아래 순서로 진행합니다. 현재 단계의 완료 확인이 끝난 뒤 다음 단계로 이동합니다.

## 1. Terraform 기반과 네트워크 정적 검사

- Stage: `T1`

### 사용할 파일

- `primary-aws/terraform/versions.tf`
- `primary-aws/terraform/network.tf`
- `primary-aws/terraform/.terraform.lock.hcl`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
terraform -chdir=primary-aws/terraform fmt -check -recursive
terraform -chdir=primary-aws/terraform init -backend=false -lockfile=readonly
terraform -chdir=primary-aws/terraform validate
```

### 완료 확인

PR-T1의 Terraform 정적 검사와 계약 배터리가 통과한 상태

## 2. 승인 apply와 AWS readback

- Stage: `DPB`

### 사용할 파일

- `.github/dependabot.yml`
- `.github/workflows/terraform-deploy.yml`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
gh workflow run terraform-deploy.yml --ref main
gh run watch
aws ec2 describe-vpcs --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
```

### 완료 확인

protected Environment 승인 뒤 exact saved plan이 적용되고 VPC·subnet·route를 AWS API로 읽은 상태

## 3. Security Group 경계 PR

- Stage: `T2`

### 사용할 파일

- `primary-aws/terraform/sg.tf`
- `primary-aws/terraform/variables-sg.tf`
- `tests/test_sg_allowlist_contract.py`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
terraform -chdir=primary-aws/terraform fmt -check -recursive
python3 -m unittest tests/test_sg_allowlist_contract.py
```

### 완료 확인

공개 P2P, /32 SSH, 조건부 standby WireGuard 규칙만 코드에 존재하는 상태

## 4. Security Group 적용과 drift 확인

- Stage: `T2`

### 사용할 파일

- `primary-aws/terraform/sg.tf`
- `primary-aws/terraform/migrations.tf`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
gh workflow run terraform-deploy.yml --ref main
aws ec2 describe-security-group-rules --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
```

### 완료 확인

AWS 규칙이 allowlist와 일치하고 후속 authoritative plan의 변경이 0건인 상태

## 5. IAM·SSM·KMS 권한 경계

- Stage: `T3 + T4`

### 사용할 파일

- `primary-aws/terraform/iam-node.tf`
- `primary-aws/terraform/ssm.tf`
- `primary-aws/terraform/kms.tf`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
terraform -chdir=primary-aws/terraform validate
python3 -m unittest tests/test_human_kms_authority_contract.py
```

### 완료 확인

노드 role, SSM 경로와 Primary·Recovery KMS key가 두 PR로 배포된 상태

## 6. 권한 probe와 fee recipient

- Stage: `T4`

### 사용할 파일

- `shared/scripts/validate-fee-recipient.py`
- `primary-aws/terraform/outputs-kms.tf`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
python3 shared/scripts/validate-fee-recipient.py "${FEE_RECIPIENT}"
aws kms list-aliases --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
aws ssm get-parameters-by-path --path /eth-staking/hoodi --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
```

### 완료 확인

KMS·SSM·IAM readback이 코드와 일치하고 검증된 fee recipient가 Parameter Store에 반영된 상태

## 7. EC2와 분리 EBS 볼륨

- Stage: `T5`

### 사용할 파일

- `primary-aws/terraform/ec2.tf`
- `primary-aws/terraform/storage.tf`
- `shared/scripts/prepare-ssh-known-host.py`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
gh workflow run terraform-deploy.yml --ref main
source ./lab.env
python3 shared/scripts/prepare-ssh-known-host.py \
  --host "${NODE_IP}" \
  --output "${HOME}/.ssh/eth-failover-node-known-hosts"
aws ec2 describe-instances --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
```

### 완료 확인

Primary EC2와 root·chain·validator 상태 볼륨이 생성되고 첫 SSH host key를 고정한 상태

## 8. EC2 보호 경계와 재생성

- Stage: `X1`

### 사용할 파일

- `.github/workflows/terraform-teardown.yml`
- `primary-aws/terraform/ci/teardown-policy.jq`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
python3 -m unittest tests/test_terraform_teardown_contract.py
bash shared/tests/test-terraform-cicd-boundary.sh
gh workflow run terraform-teardown.yml --ref main
```

### 완료 확인

stop/start 생존성과 IMDSv2·종료 보호를 확인하고 승인된 teardown/redeploy 경로를 검증한 상태


## 9. 관측 리소스 확인

```bash
terraform -chdir=primary-aws/terraform output
aws cloudwatch describe-alarms --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
aws sns list-topics --profile "${LAB_AWS_PROFILE}" --region "${AWS_REGION}"
```

Primary Terraform state는 Standby Terraform state와 분리합니다. Standby를 추가할 때 이 root를 다른 리전으로 재사용하지 않습니다.
