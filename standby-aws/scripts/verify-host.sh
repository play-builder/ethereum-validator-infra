#!/usr/bin/env bash
# Read-only Standby host foundation verifier. Run on the Standby EC2 host.
set -euo pipefail

test "$(id -u)" -eq 0
systemctl is-active chrony nftables
timedatectl show -p NTPSynchronized --value | grep -Fx true
findmnt -n /data
findmnt -n /var/lib/validator-state
test "$(stat -c %U /var/lib/validator-state)" = root
printf 'STANDBY_HOST_VERIFY=PASS\n'
