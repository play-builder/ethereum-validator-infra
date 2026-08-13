#!/usr/bin/env bash
# Read-only Hoodi EL/BN and sealed VC verifier. Run on Standby EC2.
set -euo pipefail

chain_id="$(curl -fsS -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  http://127.0.0.1:8545 | jq -r .result)"
test "$chain_id" = "0x88bb0"
curl -fsS http://127.0.0.1:5052/eth/v1/node/syncing \
  | jq -e '.data.is_syncing == false and .data.is_optimistic == false and .data.el_offline == false' >/dev/null
systemctl is-active nethermind.service lighthouse-beacon.service
test "$(systemctl is-enabled lighthouse-validator.service)" = masked
test "$(systemctl is-active lighthouse-validator.service || true)" = inactive
printf 'STANDBY_NODE_VERIFY=PASS chain_id=%s vc=masked\n' "$chain_id"
