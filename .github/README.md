# GitHub 거버넌스와 PR 실행 순서

이 README는 강의 영상에서 현재 디렉터리의 파일을 사용할 때 함께 보는 실행 참조서입니다. 명령은 저장소 루트에서 실행하며, AWS Console과 GitHub 화면의 설명은 강의를 따릅니다.

각 단계는 위에서 아래 순서로 진행합니다. 현재 단계의 완료 확인이 끝난 뒤 다음 단계로 이동합니다.

## 1. GitHub Organization과 repository 거버넌스

- Stage: `G1`

### 사용할 파일

- `.github/CODEOWNERS`
- `.github/workflows/terraform-pr.yml`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
gh auth status
source ./lab.env
test -n "${GITHUB_REPOSITORY}"
python3 shared/scripts/render-codeowners.py \
  --file .github/CODEOWNERS \
  --repository "${GITHUB_REPOSITORY}"
```

### 완료 확인

GitHub repository에 G1 파일이 있고 PR required checks, CODEOWNERS, Environment 경계가 보이는 상태

## 2. PR 리뷰와 saved plan

- Stage: `T1`

### 사용할 파일

- `.github/workflows/terraform-pr.yml`
- `.github/workflows/terraform-deploy.yml`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
gh pr checks --watch
gh pr view --web
gh run list --workflow terraform-deploy.yml --limit 5
```

### 완료 확인

승인된 T1이 main에 merge되고 deploy workflow의 plan artifact와 요약을 확인한 상태


## AWS Standby workflow

- `standby-terraform-pr.yml`: Standby Terraform format, init, validate와 contract 검사
- `standby-terraform-deploy.yml`: 기존 OIDC PlanRole/ApplyRole과 protected `hoodi-testnet-dev` Environment로 saved plan 적용
- `standby-ansible-pr.yml`: Standby playbook syntax 검사

Primary와 Standby workflow는 같은 AWS workload account를 사용하지만 Terraform root, 리전, state key와 concurrency group이 다릅니다.
