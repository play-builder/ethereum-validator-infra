# Primary EC2 Ansible 실행 순서

이 README는 강의 영상에서 현재 디렉터리의 파일을 사용할 때 함께 보는 실행 참조서입니다. 명령은 저장소 루트에서 실행하며, AWS Console과 GitHub 화면의 설명은 강의를 따릅니다.

각 단계는 위에서 아래 순서로 진행합니다. 현재 단계의 완료 확인이 끝난 뒤 다음 단계로 이동합니다.

## 1. Ansible inventory와 JWT 경계

- Stage: `A1`

### 사용할 파일

- `primary-aws/ansible/inventory/`
- `primary-aws/ansible/roles/base_os/`
- `primary-aws/ansible/playbooks/base-os.yml`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r primary-aws/ansible/ci-requirements.txt
source ./lab.env
python3 shared/scripts/render-primary-inventory.py \
  --inventory-file primary-aws/ansible/inventory/hosts.yml \
  --node-ip "${NODE_IP}" \
  --key-file "${KEY_FILE}"
ansible-playbook -i primary-aws/ansible/inventory/hosts.yml primary-aws/ansible/playbooks/base-os.yml --syntax-check
ansible-playbook -i primary-aws/ansible/inventory/hosts.yml primary-aws/ansible/playbooks/base-os.yml --tags base_os_prepare
```

### 완료 확인

로컬 Ansible 제어 환경, primary inventory, 운영 계정·디렉터리와 수동 JWT secret이 준비된 상태

## 2. 호스트 hardening과 수렴

- Stage: `A2`

### 사용할 파일

- `primary-aws/ansible/roles/hardening/`
- `primary-aws/ansible/playbooks/host-foundation.yml`

### 실행

저장소 루트에서 아래 명령을 위에서부터 실행합니다.

```bash
ansible-playbook -i primary-aws/ansible/inventory/hosts.yml primary-aws/ansible/playbooks/host-foundation.yml --syntax-check
ansible-playbook -i primary-aws/ansible/inventory/hosts.yml primary-aws/ansible/playbooks/host-foundation.yml
```

### 완료 확인

sshd·nftables·시계·계정 경계가 적용되고 같은 playbook 재실행이 changed=0으로 수렴한 상태


## 3. Primary EL·BN 설치와 동기화

```bash
export ANSIBLE_CONFIG="$PWD/primary-aws/ansible/ansible.cfg"
ansible-playbook primary-aws/ansible/playbooks/install-clients.yml --syntax-check
ansible-playbook primary-aws/ansible/playbooks/install-clients.yml
ansible-playbook primary-aws/ansible/playbooks/nethermind.yml
ansible-playbook primary-aws/ansible/playbooks/lighthouse-bn.yml
```

Nethermind JSON-RPC, Engine API와 Lighthouse Beacon API·metrics는 loopback 또는 승인된 관리 경계만 사용합니다. 독립 Hoodi Beacon API와 finalized checkpoint를 대조한 뒤 VC 단계로 이동합니다.

## 4. Lighthouse VC와 monitoring

```bash
ansible-playbook primary-aws/ansible/playbooks/vc-gated.yml --syntax-check
ansible-playbook primary-aws/ansible/playbooks/vc-gated.yml
ansible-playbook primary-aws/ansible/playbooks/mev-boost.yml
ansible-playbook primary-aws/ansible/playbooks/install-monitoring.yml
```

`vc-gated.yml`은 VC를 설치하고 `masked` 상태로 봉인합니다. key import, slashing protection import, 승인 token과 doppelganger protection 확인은 `shared/runbooks`의 승인 절차에서만 수행합니다.
