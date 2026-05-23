# make-approval-token — 사람 절차 문서 (의도적으로 스크립트가 아님)

> **설계 결정 D4:** approval token은 **수동 생성물**이다. 이 문서를 스크립트로 만들자는 제안은
> 그 자체가 위험 신호다 — "자동으로 안전해 보이는" 조합이 INC-01~04의 공통 전조였다.
> CI·cron·Ansible이 이 파일을 만든 흔적이 감사에서 발견되면 그 즉시 사고로 취급한다.

## token이 증명하는 것

```
  token = "서로 다른 사람 2인이 특정 incident/host/scope에 대해,
           checklist와 counterparty hard-fence의 exact bytes를 검토했다"는 기록

  ┌──────────────┐   검토·기명    ┌──────────────┐   수동 배치    ┌──────────────┐
  │ 증거 묶음     │ ─────────────▶ │ approval.token│ ─────────────▶ │ 대상 호스트   │
  │ (F0~F5 기록) │   sha256 고정  │ (아래 형식)   │  scp+install   │ vc-gate가 검증│
  └──────────────┘               └──────────────┘               └──────────────┘
```

## 1. 형식 (vc-gate.sh가 파싱하는 필드)

`key: value` 텍스트. 필드 순서는 무관, 누락·불일치는 기동 거부.

```text
token_version: 2
host_id: aws-standby-01
network: hoodi
scope: vc-start-failover
issued_at_utc: 2026-08-09T12:00:00Z
expires_at_utc: 2026-08-11T12:00:00Z
operators: KIM Woo-i, PARK Ops
incident_id: INC-20260809-001
checklist_sha256: <checklist.txt exact-byte sha256>
source_fence_sha256: <source-fence.json exact-byte sha256>
```

- `scope`는 `vc-start-primary` 또는 `vc-start-failover`이며 두 scope의 안전 검사는 같다.
- `issued_at_utc`, `expires_at_utc`는 canonical UTC `YYYY-MM-DDTHH:MM:SSZ`만 허용하며,
  남은 유효 시간과 발급~만료 전체 수명 모두 `LEASE_MAX_TOKEN_HOURS` 이하여야 한다.
- `operators`는 Unicode NFKC normalization, trim, `casefold()` 뒤 서로 다른 non-empty
  identity가 2개 이상이어야 한다.
- `counterparty_token_state`, `absent-confirmed`, `lease-expired`는 기동 권한 증거가 아니다.
- 허용되는 hard fence는 `provider-stopped`와 `host-power-off`뿐이다.

## 2. 생성 절차 (운영자 워크스테이션에서)

1. 해당 incident의 검토 대상을 canonical `checklist.txt`로 확정한다. gate는 이 regular
   file의 exact bytes를 다시 해시하므로 배치 후 한 바이트라도 바뀌면 기동을 거부한다.

   ```bash
   cd evidence/INC-20260809-001
   find . -type f \
     ! -name 'checklist.txt' \
     ! -name 'source-fence.json' \
     ! -name 'approval.token*' \
     -print0 | sort -z | xargs -0 sha256sum > checklist.txt

   sha256sum checklist.txt
   ```

2. `source-fence.json`을 사람이 작성한다. 아래 필드는 모두 필수다.

   ```json
   {
     "schema": "source-fence-evidence/v2",
     "network": "hoodi",
     "target_scope": "vc-start-failover",
     "source_host_id": "aws-primary-01",
     "incident_id": "INC-20260809-001",
     "checklist_sha256": "<checklist.txt exact-byte sha256>",
     "fence_type": "provider-stopped",
     "provider_state": "fenced",
     "vc_process_state": "absent",
     "operators": ["KIM Woo-i", "PARK Ops"],
     "fenced_at_utc": "2026-08-09T11:45:00Z",
     "checked_at_utc": "2026-08-09T11:55:00Z",
     "evidence_ref": "INC-20260809-001/provider-fence.txt"
   }
   ```

   `source_host_id`는 `gate.env`의 `COUNTERPARTY_HOST_ID`와 같고 `HOST_ID`와 달라야 한다.
   `checked_at_utc`는 미래가 아니며 `FENCE_MAX_AGE_MIN` 이내여야 한다.
   `fenced_at_utc`는 미래가 아니며 `checked_at_utc`보다 늦을 수 없다.

   ```bash
   sha256sum source-fence.json
   ```

3. 위 token 형식대로 `approval.token`을 **텍스트 에디터로 직접** 작성한다. 두 운영자가
   `incident_id`, `host_id`, `scope`, 두 SHA-256, `expires_at_utc`를 소리 내어 대조한다.

4. (권장) GPG 서명을 별도 파일로 남긴다 — vc-gate는 검증하지 않지만 감사 증적이 된다:

   ```bash
   gpg --detach-sign --armor approval.token
   ```

## 3. 배치 (대상 호스트에서, root)

```bash
install -o root -g root -m 0400 checklist.txt /etc/ethereum/failover/checklist.txt
install -o root -g root -m 0400 source-fence.json /etc/ethereum/failover/source-fence.json
install -o root -g root -m 0400 approval.token /etc/ethereum/failover/approval.token
sha256sum /etc/ethereum/failover/checklist.txt \
  /etc/ethereum/failover/source-fence.json \
  /etc/ethereum/failover/approval.token
```

`vc-gate`는 Python 3 기반 `vc-input-snapshot`으로 token, fence, absence, absence
checksum, checklist, pubkeys를 `O_NOFOLLOW`/`fstat` 검증한 뒤 root 소유 private
`0700` snapshot에 고정한다. 원본은 root 소유이며 group/other write가 없어야 하고
token은 `0400` 또는 `0600`이어야 한다. 해시와 파싱은 이 snapshot의 동일 exact
bytes만 사용하며, observer lock 또는 checksum 불일치가 있으면 기동을 거부한다.

`observe-absence`와 `vc-gate`는 Python 3 기반 `vc-evidence-store`를 함께 사용한다.
출력은 `EVIDENCE_ROOT`(기본 `/var/lib/ethereum-maintenance/evidence`, root 소유
`0700`) 바로 아래의 안전한 basename 하나여야 한다. helper는 root의 모든 경로
요소를 `O_DIRECTORY|O_NOFOLLOW`로 열고, 이후 lock/temp/JSON/checksum을 같은
dirfd에 상대적으로 조작한다. 각 temp 파일과 lock·invalidation·rename·unlock
단계는 `fsync` 뒤에만 완료되므로 stale lock, partial pair, checksum 불일치는
항상 gate에서 fail closed한다.

## 4. 회수(revocation) — 발급의 역연산도 사람 손으로

```bash
sudo rm -f /etc/ethereum/failover/approval.token
sudo touch /etc/ethereum/failover/SEALED     # 봉인까지 원하면(F8/페일백 3항)
```

- 회수 시각·수행자·사유를 [token-record](../evidence/templates/token-record.example.md)에 기록한다.
- 어느 방향이든 새 token 발급 전에 counterparty VC hard fence와 기존 token 회수를 먼저 기록한다.

## 5. 금지 사항

- token을 저장소·백업·클립보드 매니저에 커밋/동기화하지 않는다(경로는 `.gitignore` 처리됨).
- 만료 연장은 "파일 수정"이 아니라 **재발급**이다(새 issued/expires + 재검토 + 새 기록).
- `lease-expired`, `network-isolated`, `host-masked`를 hard fence로 기록하지 않는다.
- 하나의 token을 두 호스트에 복사하는 행위는 설계 파괴다 — `host_id` 바인딩이 막지만,
  시도 자체를 사고로 기록한다.
