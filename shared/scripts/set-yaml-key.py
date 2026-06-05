#!/usr/bin/env python3
"""최상위 YAML 키 하나의 값을 정확히 1회 치환한다.

강의 화면에서 heredoc + 조건문을 걷어내기 위한 헬퍼. 키가 없거나 2회 이상이면
치환하지 않고 실패하므로, group_vars 같은 단일 원천 파일의 오염을 막는다.
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--value", required=True)
    ap.add_argument("--label", default="YAML_KEY")
    a = ap.parse_args()

    path = Path(a.file)
    if not path.is_file():
        print(f"{a.label}=FAIL reason=file_not_found path={a.file}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(a.key)}:.*$", re.M)
    found = len(pattern.findall(text))
    if found != 1:
        print(f"{a.label}=FAIL reason=key_count key={a.key} count={found} expected=1", file=sys.stderr)
        return 1
    updated = pattern.sub(f'{a.key}: "{a.value}"', text, count=1)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
    print(f"{a.label}=SET key={a.key} value={a.value} file={a.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
