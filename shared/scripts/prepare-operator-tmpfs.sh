#!/usr/bin/env bash
# Mount an empty tmpfs as root, then hand only that mountpoint to the operator.
set -euo pipefail

path=""
size="64m"
cleanup=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cleanup) cleanup=1; shift ;;
    --path) path="${2:-}"; shift 2 ;;
    --size) size="${2:-}"; shift 2 ;;
    *) echo "OPERATOR_TMPFS=FAIL reason=usage" >&2; exit 64 ;;
  esac
done

case "$path" in
  /*) ;;
  *) echo "OPERATOR_TMPFS=FAIL reason=path_must_be_absolute" >&2; exit 64 ;;
esac
[ "$path" != "/" ] || { echo "OPERATOR_TMPFS=FAIL reason=root_path_rejected" >&2; exit 64; }

if [ "$cleanup" -eq 1 ]; then
  [ -d "$path" ] \
    || { echo "OPERATOR_TMPFS_CLEANUP=FAIL reason=path_not_directory" >&2; exit 1; }
  filesystem="$(findmnt -n -o FSTYPE --target "$path" 2>/dev/null || true)"
  [ "$filesystem" = "tmpfs" ] \
    || { echo "OPERATOR_TMPFS_CLEANUP=FAIL reason=not_tmpfs path=$path" >&2; exit 1; }
  sudo find "$path" -xdev -type f -exec shred -u -- {} +
  sudo find "$path" -xdev -mindepth 1 -depth -delete
  [ -z "$(sudo find "$path" -xdev -mindepth 1 -print -quit)" ] \
    || { echo "OPERATOR_TMPFS_CLEANUP=FAIL reason=content_remains" >&2; exit 1; }
  sudo umount "$path"
  if findmnt -n --target "$path" >/dev/null 2>&1; then
    echo "OPERATOR_TMPFS_CLEANUP=FAIL reason=still_mounted path=$path" >&2
    exit 1
  fi
  [ -z "$(find "$path" -mindepth 1 -print -quit)" ] \
    || { echo "OPERATOR_TMPFS_CLEANUP=FAIL reason=underlay_not_empty" >&2; exit 1; }
  echo "OPERATOR_TMPFS_CLEANUP=PASS path=$path sensitive_files=0 mounted=false"
  exit 0
fi

printf '%s' "$size" | grep -Eq '^[1-9][0-9]*[mMgG]$' \
  || { echo "OPERATOR_TMPFS=FAIL reason=invalid_size" >&2; exit 64; }

if findmnt -n "$path" >/dev/null 2>&1; then
  echo "OPERATOR_TMPFS=FAIL reason=already_mounted path=$path" >&2
  exit 1
fi

operator_uid="$(id -u)"
operator_gid="$(id -g)"
sudo mkdir -p "$path"
if [ -n "$(sudo find "$path" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "OPERATOR_TMPFS=FAIL reason=mountpoint_not_empty path=$path" >&2
  exit 1
fi
sudo chmod 0700 "$path"
mounted=0
cleanup_failed_mount() {
  if [ "$mounted" -eq 1 ]; then
    sudo umount "$path" >/dev/null 2>&1 || true
  fi
}
trap cleanup_failed_mount EXIT HUP INT TERM
sudo mount -t tmpfs -o "size=$size,mode=700" tmpfs "$path"
mounted=1
sudo chown "$operator_uid:$operator_gid" "$path"
chmod 0700 "$path"

owner_match="$(find "$path" -prune -user "$operator_uid" -perm 0700 -print)"
[ "$owner_match" = "$path" ] \
  || { echo "OPERATOR_TMPFS=FAIL reason=operator_owner_or_mode_mismatch" >&2; exit 1; }
findmnt -n "$path"
mounted=0
trap - EXIT HUP INT TERM
echo "OPERATOR_TMPFS=OK path=$path uid=$operator_uid gid=$operator_gid mode=0700"
