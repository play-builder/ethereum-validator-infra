# AWS 계정과 운영자 권한 준비

이 README는 강의 영상에서 현재 디렉터리의 파일을 사용할 때 함께 보는 실행 참조서입니다. 명령은 저장소 루트에서 실행하며, AWS Console과 GitHub 화면의 설명은 강의를 따릅니다.

각 단계는 위에서 아래 순서로 진행합니다. 현재 단계의 완료 확인이 끝난 뒤 다음 단계로 이동합니다.

## 1. AWS 계정과 운영자 준비

- Stage: `없음 (Console 작업)`

### 사용할 파일

- `lab.env.example`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
aws --version
gh --version
```

### 완료 확인

AWS 세 계정과 세 Permission Set이 문서의 고정 이름으로 보이고 두 운영자의 MFA 등록이 끝난 상태

## 2. AWS CLI SSO 프로필 로그인 확인

- Stage: `G1`

### 사용할 파일

- `lab.env.example`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
cp lab.env.example lab.env && source ./lab.env
aws sso login --profile hoodi-testnet-dev-builder
aws sts get-caller-identity --profile hoodi-testnet-dev-builder
```

### 완료 확인

`hoodi-testnet-dev-builder` 프로필의 STS account가 `WORKLOAD_ACCOUNT_ID`와 일치하는 상태
