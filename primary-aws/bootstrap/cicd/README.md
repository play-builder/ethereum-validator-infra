# GitHub OIDC와 Terraform CI/CD 부트스트랩

이 README는 강의 영상에서 현재 디렉터리의 파일을 사용할 때 함께 보는 실행 참조서입니다. 명령은 저장소 루트에서 실행하며, AWS Console과 GitHub 화면의 설명은 강의를 따릅니다.

각 단계는 위에서 아래 순서로 진행합니다. 현재 단계의 완료 확인이 끝난 뒤 다음 단계로 이동합니다.

## 1. OIDC와 Terraform 배포 경계

- Stage: `G2`

### 사용할 파일

- `primary-aws/bootstrap/`
- `.github/workflows/terraform-deploy.yml`
- `primary-aws/terraform/ci/runtime-inputs.json`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
source ./lab.env
aws sso login --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

COMPACT_TEMPLATE="/tmp/cicd-bootstrap.compact.json"
jq -c . primary-aws/bootstrap/cicd/template.yaml > "${COMPACT_TEMPLATE}"
COMPACT_TEMPLATE_BYTES="$(wc -c < "${COMPACT_TEMPLATE}" | tr -d '[:space:]')"
test "${COMPACT_TEMPLATE_BYTES}" -le 51200
printf 'COMPACT_TEMPLATE_BYTES=%s\n' "${COMPACT_TEMPLATE_BYTES}"

aws cloudformation validate-template \
  --template-body "file://${COMPACT_TEMPLATE}" \
  --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"
python3 -m unittest tests/test_terraform_deploy_contract.py
```

`template.yaml`은 읽기 쉬운 들여쓰기와 줄바꿈을 포함해 51,200바이트를 넘습니다. `jq -c`는 JSON 구조를 바꾸지 않고 공백만 제거한 고정 파일 `/tmp/cicd-bootstrap.compact.json`을 만들며, `test`가 AWS CLI의 inline `TemplateBody` 제한 안인지 먼저 확인합니다. 같은 파일을 `create-change-set --template-body "file://${COMPACT_TEMPLATE}"`에도 사용합니다.

### 완료 확인

GitHub OIDC provider, PlanRole, ApplyRole와 backend가 생성되고 Platform Admin 임시 assignment가 회수된 상태

## runtime-inputs.json 값 직접 조회와 기록

`primary-aws/terraform/ci/runtime-inputs.json`을 열기 전에 아래 읽기 전용 명령으로 `REPLACE_` 13개의 실제 값을 조회합니다. 출력이 `None` 또는 빈 문자열이면 편집하지 말고 해당 리소스가 생성됐는지 먼저 확인합니다.

| 필드 | 교체할 표시 |
|---|---|
| `repository` | `REPLACE_WITH_GITHUB_ORG/ethereum-validator-infra` |
| `repository_owner_id` | `REPLACE_WITH_GITHUB_OWNER_ID` |
| `repository_id` | `REPLACE_WITH_GITHUB_REPOSITORY_ID` |
| `aws.account_id` | `REPLACE_WITH_AWS_ACCOUNT_ID` |
| `aws.plan_role_arn` | `REPLACE_WITH_TERRAFORM_PLAN_ROLE_ARN` |
| `aws.apply_role_arn` | `REPLACE_WITH_TERRAFORM_APPLY_ROLE_ARN` |
| `aws.kms_break_glass_role_arn` | `REPLACE_WITH_KMS_BREAK_GLASS_ROLE_ARN` |
| `aws.state_bucket` | `REPLACE_WITH_STATE_BUCKET` |
| `aws.state_kms_key_arn` | `REPLACE_WITH_STATE_KMS_KEY_ARN` |
| `aws.plan_artifact_bucket` | `REPLACE_WITH_PLAN_ARTIFACT_BUCKET` |
| `aws.plan_artifact_kms_key_arn` | `REPLACE_WITH_PLAN_ARTIFACT_KMS_KEY_ARN` |
| `aws.node_permissions_boundary_arn` | `REPLACE_WITH_NODE_PERMISSIONS_BOUNDARY_ARN` |
| `terraform.admin_cidrs[0]` | `REPLACE_WITH_GLOBAL_IPV4/32` |

```bash
source ./lab.env
GITHUB_ORG="${GITHUB_REPOSITORY%%/*}"

## repository, repository_owner_id, repository_id
gh api "repos/${GITHUB_REPOSITORY}" --jq '.full_name'
gh api "orgs/${GITHUB_ORG}" --jq '.id'
gh api "repos/${GITHUB_REPOSITORY}" --jq '.id'

## aws.account_id
aws sts get-caller-identity \
  --query Account --output text \
  --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.plan_role_arn
aws iam get-role \
  --role-name hoodi-testnet-dev-TerraformPlanRole \
  --query 'Role.Arn' --output text \
  --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.apply_role_arn
aws iam get-role \
  --role-name hoodi-testnet-dev-TerraformApplyRole \
  --query 'Role.Arn' --output text \
  --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.kms_break_glass_role_arn
aws iam get-role \
  --role-name hoodi-testnet-dev-KmsBreakGlassRole \
  --query 'Role.Arn' --output text \
  --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.state_bucket
aws s3api list-buckets \
  --query "Buckets[?Name=='hoodi-testnet-dev-tfstate-${WORKLOAD_ACCOUNT_ID}'].Name | [0]" \
  --output text --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.state_kms_key_arn
aws s3api get-bucket-encryption \
  --bucket "hoodi-testnet-dev-tfstate-${WORKLOAD_ACCOUNT_ID}" \
  --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.KMSMasterKeyID' \
  --output text --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.plan_artifact_bucket
aws s3api list-buckets \
  --query "Buckets[?Name=='hoodi-testnet-dev-tfplans-${WORKLOAD_ACCOUNT_ID}'].Name | [0]" \
  --output text --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.plan_artifact_kms_key_arn
aws s3api get-bucket-encryption \
  --bucket "hoodi-testnet-dev-tfplans-${WORKLOAD_ACCOUNT_ID}" \
  --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.KMSMasterKeyID' \
  --output text --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## aws.node_permissions_boundary_arn
aws iam list-policies \
  --scope Local \
  --query "Policies[?PolicyName=='hoodi-testnet-dev-node-permissions-boundary'].Arn | [0]" \
  --output text --profile "${PLATFORM_BOOTSTRAP_AWS_PROFILE}"

## terraform.admin_cidrs[0]
printf '%s/32\n' "$(curl -4fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
```

`REPLACE_` 표시는 아니지만 Region, key pair, Permission Set과 email도 본인의 `lab.env` 값과 일치해야 합니다.

```bash
printf 'AWS_REGION=%s\nSTANDBY_AWS_REGION=%s\nKEY_NAME=%s\nOP1_PERMISSION_SET=%s\nOP2_PERMISSION_SET=%s\nOP1_EMAIL=%s\nOP2_EMAIL=%s\n' \
  "${AWS_REGION}" "${STANDBY_AWS_REGION}" "${KEY_NAME}" \
  "${OP1_PERMISSION_SET}" "${OP2_PERMISSION_SET}" "${OP1_EMAIL}" "${OP2_EMAIL}"
```

조회 결과를 준비한 다음 편집기를 열어 값을 직접 교체합니다. `repository`에는 첫 번째 명령의 `owner/repository` 전체 문자열을 기록합니다.

```bash
"${EDITOR:-vi}" primary-aws/terraform/ci/runtime-inputs.json
```

```bash
if rg -n 'REPLACE_' primary-aws/terraform/ci/runtime-inputs.json; then
  printf 'REPLACE_ 값이 남아 있습니다.\n' >&2
  exit 1
fi
python3 -m json.tool primary-aws/terraform/ci/runtime-inputs.json >/dev/null
printf 'REPLACE_ 값이 0개이고 JSON 형식이 정상입니다.\n'
```

`list-buckets`를 `hoodi-testnet-dev`만으로 필터링하면 두 bucket이 함께 반환됩니다. 위 명령은 계정 ID와 `tfstate`·`tfplans`가 포함된 정확한 이름으로 state bucket과 plan artifact bucket을 구분합니다. 기록 후에는 `shared/scripts/render-terraform-ci-runtime.py`로 manifest를 검증합니다. GitHub `hoodi-testnet-dev`와 `hoodi-testnet-dev-teardown` Environment는 승인 경계만 사용하므로 Environment variables/secrets 0개를 유지합니다.

## G2 PR 승인과 인계 확인

G2는 두 Code Owner 경로를 함께 변경하므로 `security-approvers`와 `platform-approvers`가 각각 자기 소유 파일을 승인해야 합니다. Security 검토자는 runtime manifest·bootstrap·shared 파일을, Platform 검토자는 workflow·Terraform CI 정책 파일을 검토합니다. required status check가 모두 초록이고 두 팀이 모두 승인됐으며 `pending review`가 0건일 때만 merge합니다.

G2에는 아직 `primary-aws/terraform/versions.tf`가 없으므로 `terraform-static`의 정상 결과는 다음과 같습니다.

```text
TERRAFORM_STATIC=SKIP reason=terraform_module_not_imported
```

파라미터 PR merge SHA는 새 Bash 프로세스에서 실행하는 인계 스크립트가 읽을 수 있도록 환경변수로 내보냅니다.

```bash
export PARAMETERS_MAIN_SHA="$(gh pr view "${PARAMETERS_PR_URL}" \
  --repo "${GITHUB_REPOSITORY}" \
  --json mergeCommit \
  --jq '.mergeCommit.oid')"
printf 'PARAMETERS_MAIN_SHA=%s\n' "${PARAMETERS_MAIN_SHA}"
bash shared/scripts/verify-github-controls.sh --check handover
```
