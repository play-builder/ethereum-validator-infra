# Planned Failover Gate Record: INC-YYYYMMDD-NNN

- 전환 방향: AWS Primary -> AWS Standby
- 판정자 2인: ______ / ______
- 시작 UTC: ______
- 종료 UTC: ______
- `incident_id`: ______
- `checklist_sha256`: ______
- `source_fence_sha256`: ______

| 순서 | 확인 항목 | PASS | UTC | 증거 파일 또는 SHA-256 |
|---|---|---|---|---|
| 1 | Primary VC stop, mask, process absent | | | |
| 2 | 최신 EIP-3076 export와 identity 검증 | | | |
| 3 | Primary EC2 `stopped` hard fence | | | |
| 4 | Standby key identity 일치 | | | |
| 5 | Standby slashing protection import | | | |
| 6 | two-person approval token과 checklist | | | |
| 7 | `vc-gate`의 `GATE=PASS` | | | |
| 8 | doppelganger protection 관찰 구간 | | | |
| 9 | Standby 첫 정상 attestation | | | |
| 10 | Primary `stopped`, Standby sole signer | | | |

기록에는 공개 identity와 checksum만 넣고 keystore, password, mnemonic, token 원문은 넣지 않습니다.
