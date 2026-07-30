# RB-03: Slashing protection 이력의 권한 기준

Course 1에서 서명 재개를 허가하는 자료는 **정지된 현재 signer에서 방금 export한 EIP-3076 JSON**입니다. 이 파일은 signed block과 attestation 이력을 옮기며, Lighthouse SQLite DB는 `$datadir/validators/slashing_protection.sqlite`에 있습니다.

## 사용할 수 있는 자료

1. 현재 signer VC를 정지한 뒤 만든 최신 EIP-3076 export
2. 현재 signer의 보존된 `slashing_protection.sqlite`에서 공식 Lighthouse 절차로 만든 export

JSON 구조, Hoodi genesis validators root, validator public key와 SHA-256을 모두 확인합니다. export와 import 동안 source와 target VC는 정지 상태여야 합니다.

## 서명 재개 금지 기준

오래된 주기 백업, explorer 관측, 새로 만든 빈 SQLite DB, doppelganger protection만으로는 최신 signed history 전체와 반대편 signer의 hard fence를 증명할 수 없습니다. 이때 Course 1의 결정은 `NO_SIGNING_WITHOUT_FRESH_AUTHENTIC_HISTORY`이며 VC는 `masked`와 `SEALED` 상태를 유지합니다.

공식 기준은 [EIP-3076](https://eips.ethereum.org/EIPS/eip-3076)과 [Lighthouse slashing protection](https://lighthouse-book.sigmaprime.io/validator_slashing_protection.html)입니다.
