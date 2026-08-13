# AWS Standby Ansible 실행 참조

Ansible은 로컬 control node에서 Standby EC2에 적용합니다. EC2 안에 Ansible을 설치해 자기 자신을 구성하지 않습니다.

## 1. 활성 파일 준비

```bash
cp standby-aws/ansible/inventory/hosts.example.yml standby-aws/ansible/inventory/hosts.yml
cp standby-aws/ansible/group_vars/all.example.yml standby-aws/ansible/group_vars/all.yml
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r standby-aws/ansible/ci-requirements.txt
export ANSIBLE_CONFIG="$PWD/standby-aws/ansible/ansible.cfg"
```

## 2. 문법 검사와 적용

```bash
ansible-playbook standby-aws/ansible/playbooks/host-foundation.yml --syntax-check
ansible-playbook standby-aws/ansible/playbooks/install-clients.yml --syntax-check
ansible-playbook standby-aws/ansible/playbooks/start-el-bn.yml --syntax-check
ansible-playbook standby-aws/ansible/playbooks/site.yml
```

## 3. 완료 확인

```bash
ansible aws_standby -b -m shell -a 'systemctl is-active nethermind lighthouse-beacon; systemctl is-enabled lighthouse-validator.service'
```

EL·BN은 동기화 대상으로 실행되고 `lighthouse-validator.service`는 `masked`여야 합니다. `vc_sealed` role은 activation token을 만들지 않습니다.
