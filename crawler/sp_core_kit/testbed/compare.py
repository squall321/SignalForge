#!/usr/bin/env python3
"""받은 결과를 시험장의 정답과 맞춰 본다.

    python compare.py --dump my_state.json                      # 정답은 가짜 SP 에서 읽는다
    python compare.py --dump my_state.json --server http://127.0.0.1:9000

my_state.json — 받는 쪽이 **자기 사본(층 1)에서** 내보낸다:

    {
      "objects": [
        {"type": "vendor", "key": "V-1", "label": "ACME", "status": "active", "active": true,
         "properties": {"country": "KR", "grade": "A"}}
      ],
      "relations": [
        {"src_type": "part", "src": "P-1", "relation": "supplied_by", "dst": "V-1", "dst_type": "vendor"}
      ]
    }

- objects: 받은 객체 **전부**(비활성 포함 — active 로 가른다). properties 는 SP 가 준 모양 그대로.
- relations: **지금 이어져 있는 선만**(끊은 선은 빼거나 지운다).

끝나는 값: 0 통과 · 1 불통(무엇이 왜 다른지 찍는다) · 2 파일 · 서버 문제
의존성 없음(파이썬 3.8 이상 표준 라이브러리).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any


def fetch_expected(server: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{server.rstrip('/')}/_expected", timeout=10) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def same(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return bool(a == b)


def diff(expected: dict[str, Any], dump: dict[str, Any]) -> list[str]:
    """다른 곳 — 한 줄에 하나, 어느 규칙인지까지. 비면 통과다."""
    ignore = set(expected.get("ignore_types") or [])
    previous: dict[str, dict[str, str]] = expected.get("previous_keys") or {}
    refs: dict[str, dict[str, str]] = expected.get("ref_properties") or {}
    want: dict[tuple[str, str], dict[str, Any]] = {
        (slug, one["key"]): one
        for slug, rows in (expected.get("objects") or {}).items()
        for one in rows
    }
    alive_keys = {key for key in want}
    problems: list[str] = []

    have: dict[tuple[str, str], dict[str, Any]] = {}
    for one in dump.get("objects") or []:
        slug, key = str(one.get("type")), str(one.get("key"))
        if slug in ignore or not one.get("active", True):
            continue
        if (slug, key) in have:
            problems.append(f"[같은 것 두 줄] {slug}:{key} 가 활성으로 두 번 있습니다 — (타입, key) 로 하나만 둡니다(규칙 3)")
        have[(slug, key)] = one

    for (slug, key), one in sorted(want.items()):
        got = have.get((slug, key))
        if got is None:
            problems.append(f"[빠짐] {slug}:{key}({one['label']}) 이 없거나 비활성입니다 — 규칙 2 · 5 · 7 · 8 · 12 를 보세요")
            continue
        if got.get("label") != one["label"]:
            problems.append(f"[이름] {slug}:{key} 이름이 {got.get('label')!r} — 정답 {one['label']!r}")
        if (got.get("status") or "active") != one["status"]:
            problems.append(f"[상태] {slug}:{key} status 가 {got.get('status')!r} — 정답 {one['status']!r}")
        mine = got.get("properties") or {}
        for name in sorted(set(mine) | set(one["properties"])):
            a, b = mine.get(name), one["properties"].get(name)
            if a is not None and b is not None and same(a, b):
                continue
            ref_type = (refs.get(slug) or {}).get(name)
            if ref_type and a is not None:
                # 참조 칸은 보낸 때의 key 다 — 옛 key 면 previous_keys 로 풀리면 맞다(규칙 10).
                resolved = (previous.get(ref_type) or {}).get(str(a), a)
                if b is not None and resolved == b:
                    continue
                if b is None and (ref_type, resolved) not in alive_keys:
                    continue  # 가리키던 것이 사라졌다 — 받는 쪽은 알 길이 없다
            if a is None and b is None:
                continue
            problems.append(
                f"[칸] {slug}:{key}.{name} 이 {a!r} — 정답 {b!r}"
                + ("(빈 값은 칸이 아예 안 온다 — 받은 행의 칸으로 통째로 바꿉니다, 규칙 6)" if b is None else "")
            )

    for (slug, key) in sorted(set(have) - set(want)):
        moved = (previous.get(slug) or {}).get(key)
        if moved:
            problems.append(f"[옛 식별자 남음] {slug}:{key} 는 {moved} 로 바뀌었습니다 — 행을 새 key 로 옮깁니다(규칙 5)")
        else:
            problems.append(
                f"[살아 있으면 안 됨] {slug}:{key} — 지워졌거나 · 합쳐졌거나 · 안 보이게 됐습니다(규칙 4 · 7 · 9)"
            )

    def line(one: Any) -> tuple[str, ...]:
        if isinstance(one, dict):
            return tuple(str(one.get(k)) for k in ("src_type", "src", "relation", "dst", "dst_type"))
        return tuple(str(x) for x in one)

    want_lines = {line(one) for one in expected.get("relations") or [] if not ignore & {one[0], one[4]}}
    have_lines = [line(one) for one in dump.get("relations") or []]
    have_lines = [one for one in have_lines if not ignore & {one[0], one[4]}]
    if len(have_lines) != len(set(have_lines)):
        problems.append("[같은 선 두 줄] 선은 (src_type, src, relation, dst, dst_type) 로 하나만 둡니다(규칙 15)")
    for one in sorted(want_lines - set(have_lines)):
        problems.append(f"[선 빠짐] {one[0]}:{one[1]} -{one[2]}→ {one[4]}:{one[3]} — 규칙 5 · 7 · 8 · 12 · 15 를 보세요")
    for one in sorted(set(have_lines) - want_lines):
        old = (previous.get(one[0]) or {}).get(one[1]) or (previous.get(one[4]) or {}).get(one[3])
        hint = "끝의 식별자가 바뀌었습니다 — 선의 끝도 새 key 로 옮깁니다(규칙 5)" if old else (
            "끊겼거나 · 끝이 지워졌거나 · 안 보이게 됐습니다(규칙 7 · 9 · 10)"
        )
        problems.append(f"[끊겼어야 할 선] {one[0]}:{one[1]} -{one[2]}→ {one[4]}:{one[3]} — {hint}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="받은 결과를 시험장 정답과 비교")
    parser.add_argument("--dump", required=True, help="받는 쪽이 내보낸 JSON")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="가짜 SP 주소(/api 없이)")
    args = parser.parse_args(argv)
    try:
        with open(args.dump, encoding="utf-8") as fh:
            dump = json.load(fh)
        expected = fetch_expected(args.server)
    except (OSError, ValueError) as caught:
        print(f"읽지 못했습니다: {caught}", file=sys.stderr)
        return 2
    problems = diff(expected, dump)
    step = expected.get("step")
    if problems:
        print(f"{step}단계 불통 — {len(problems)}곳")
        for one in problems:
            print(f"  {one}")
        return 1
    objects = sum(len(rows) for rows in (expected.get("objects") or {}).values())
    print(f"{step}단계 통과 — 객체 {objects} · 선 {len(expected.get('relations') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
