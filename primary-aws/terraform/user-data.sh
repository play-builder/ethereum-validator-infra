#!/usr/bin/env bash
# user-data — "포맷과 마운트까지만". 클라이언트 설치·설정은 전부 Ansible(감사 가능한 diff)로.
#
# 디스크 판별 규칙(크기 하드코딩 없음):
#   후보 = 파일시스템이 없고(blkid 실패) 마운트되지 않은 NVMe 디스크
#   후보가 정확히 2개일 때만: 큰 쪽 → ETH-DATA(chaindata), 작은 쪽 → ETH-VALSTATE(SP DB)
#   그 외(0,1,3개 이상)는 아무것도 하지 않는다 — Ansible preflight가 사람에게 보고한다.
# 멱등: 라벨이 이미 있으면 절대 다시 포맷하지 않는다(재부팅·stop/start 안전).
set -euo pipefail
exec > /var/log/user-data.log 2>&1

mapfile -t CANDIDATES < <(
  for dev in /dev/nvme[0-9]n1 /dev/nvme[0-9][0-9]n1; do
    [ -b "$dev" ] || continue
    blkid "$dev" >/dev/null 2>&1 && continue            # 이미 fs 있음(루트/기존) → 제외
    lsblk -no MOUNTPOINTS "$dev" | grep -q . && continue # 마운트됨 → 제외
    lsblk -no CHILDREN "$dev" 2>/dev/null | grep -q . && continue
    printf '%s %s\n' "$(blockdev --getsize64 "$dev")" "$dev"
  done | sort -n | awk '{print $2}'
)

echo "blank candidates: ${CANDIDATES[*]:-none}"
if [ "${#CANDIDATES[@]}" -eq 2 ]; then
  SMALL="${CANDIDATES[0]}"; BIG="${CANDIDATES[1]}"
  mkfs.ext4 -L ETH-VALSTATE -m 0 "$SMALL"
  mkfs.ext4 -L ETH-DATA -m 0 "$BIG"
  echo "formatted: $BIG=ETH-DATA $SMALL=ETH-VALSTATE"
else
  echo "skip formatting (candidates=${#CANDIDATES[@]}, expected 2) — manual/ansible review required"
fi

mkdir -p /data /var/lib/validator-state
grep -q 'LABEL=ETH-DATA' /etc/fstab     || echo 'LABEL=ETH-DATA /data ext4 defaults,nofail,noatime 0 2' >> /etc/fstab
grep -q 'LABEL=ETH-VALSTATE' /etc/fstab || echo 'LABEL=ETH-VALSTATE /var/lib/validator-state ext4 defaults,nofail,noatime 0 2' >> /etc/fstab
mount -a || true
echo "user-data done"
