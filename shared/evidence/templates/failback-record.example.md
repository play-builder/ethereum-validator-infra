# Failback Record — FB-YYYYMMDD-NNN

- 전제 게이트: P1' 원인 규명 보고서 [ ] 승인(링크)  P2' AWS 재구축 검증 [ ]  P3' 동결 창 밖 [ ]
- 게이트 표는 failover-gate-record와 동일 구조(F0'~F8'), 차이 항목만 추가 기록:
- binding: incident_id ______ / checklist_sha256 ______ / source_fence_sha256 ______

| 항목 | 증거 |
|---|---|
| F3' Standby VC stopped + authentic fresh export checksum/validate + token 회수 | |
| F1' Standby provider-stopped 또는 host-power-off (`source-fence-evidence/v2`, `provider_state=fenced`, `vc_process_state=absent`) | |
| F1' incident/scope/host/checklist binding, `checked_at_utc` freshness, distinct operators | |
| F2' finalized 관측 부재 (`last_checked_epoch <= finalized_epoch_end`, JSON/checksum, observer lock 부재; supplemental only) | |
| F5' checklist/fence/token exact-byte sha256 + token total lifetime ≤168h | |
| F6' AWS stopped import checksum → root-only `sp-state-evidence/v1` → `final_liveness_recheck` PASS | |
| F6' SP binding: incident_id / target_host_id / checklist_sha256 / current sp_db_sha256 / recorded_at_utc / distinct operators / marker exact-byte SHA-256 | |
| F7' manual start → DP 2–3 epoch → 첫 attestation/`p-aws` 2소스 | |
| F8'-a Standby 키 파기 시도 기록 (shred 로그 + umount + findmnt 부재 확인 — shred는 보증 아님) | |
| F8'-b Standby SP DB stopped export 보관본 sha256 | |
| F8'-c 재스테이징 예약(RB-04 일정) | |

- S0 선언 시각(UTC): ______  선언자 2인: ______ / ______
