# SP Recovery Decision Record — RB-03

- incident_id: ______
- 대상 validator record(정확히 1개) / pubkey: ______
- current signer VC stopped + PID 부재 증거: ______
- 조사한 authentic source:
  - [ ] stopped current signer export — 파일/checksum: ______
  - [ ] stopped signer DB conversion/export — 원본 DB/checksum/변환 로그: ______
  - [ ] authoritative remote-signer audit/export — endpoint 정지/audit 범위/checksum: ______
- historical/stale archive diagnostic 결과(활성화 허가 아님): ______
- validator 출력: `SLASHING_INTERCHANGE=PASS` [ ] / 원문: ______
- import 대상 VC stopped/masked 증거: ______
- authentic history 복구 실패 marker:
  `NO_AUTO_ACTIVATION_WITHOUT_AUTHENTIC_SIGNED_HISTORY` [ ]
- incident commander 선택: [ ] downtime  [ ] authentic history recovery  [ ] voluntary exit
- VC remains masked / token 미발급 / start 0회 증거: ______
- distinct operators: ______ / ______  서명·UTC: ______
