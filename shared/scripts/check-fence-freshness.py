#!/usr/bin/env python3
"""provider fence 증거의 신선도를 판정한다 (미래 시각·유효기간 초과 거부).

강의 화면에서 파이썬 heredoc을 걷어내기 위한 헬퍼. 판정 실패 시 비정상 종료하며
사유를 토큰으로 남긴다.
"""
from __future__ import annotations
import argparse, sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--max-age-seconds", type=int, default=900)
    ap.add_argument("--label", default="PROVIDER_FENCE")
    a = ap.parse_args()

    try:
        fields = dict(
            line.split("=", 1)
            for line in Path(a.file).read_text(encoding="utf-8").splitlines()
            if line
        )
        checked = datetime.strptime(
            fields["checked_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except (OSError, KeyError, ValueError) as exc:
        print(f"{a.label}=FAIL reason=unreadable detail={exc}", file=sys.stderr)
        return 1

    age = (datetime.now(timezone.utc) - checked).total_seconds()
    if age != abs(age) or age > a.max_age_seconds:
        print(f"{a.label}=FAIL age_seconds={age:.0f} max={a.max_age_seconds}", file=sys.stderr)
        return 1
    print(f"{a.label}=PASS type={fields.get('fence_type','?')} age_seconds={age:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
