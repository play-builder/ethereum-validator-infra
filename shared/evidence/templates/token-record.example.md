# Approval Token Record

## 발급
- token sha256: ______   scope: vc-start-primary | vc-start-failover
- incident_id: ______  host_id: ______  counterparty_host_id: ______  network: ______
- issued/expires(UTC): ______ / ______  operators(distinct 2인): ______ / ______
- checklist regular file: ______  checklist_sha256(exact bytes): ______
- source fence regular file: ______  source_fence_sha256(exact bytes): ______
- fence schema/type: source-fence-evidence/v2 / provider-stopped | host-power-off
- source host/provider/vc state: ______ / fenced / absent  checked_at_utc: ______
- 배치 명령 출력(checklist/fence/token, install -m 0400): ______

## 회수
- 회수 시각(UTC): ______  수행자: ______  사유: ______
- rm 출력 / SEALED 생성 여부: ______
- 감사 확인: 이 token을 생성한 것이 사람임을 확인(자동화 흔적 없음) [ ]
