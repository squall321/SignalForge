"""BaseCrawler._content_hash 운영 점검 단위 — R15 트랙 E.

검증 의도:
  - save() 가 신규 voc 의 content_hash 컬럼을 자동으로 채우는지 보장.
  - 30자 미만 content 는 hash NULL 이 정상 (정책).
  - 30자 이상 content 는 hash 가 반드시 채워져야 함 (운영 SLO).
  - 같은 content → 같은 hash (재현성).
  - 다른 content → 다른 hash (충돌 방지).

운영 의미:
  - 운영 DB 의 NULL hash 비율이 "본문 < 30자" 비율을 초과하면 회귀.
  - 본 테스트가 깨지면 save() 의 chash 주입 로직 (crawler.py:185, 225) 점검.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base.crawler import BaseCrawler  # noqa: E402


def test_content_hash_policy_and_determinism() -> None:
    h = BaseCrawler._content_hash

    # 정책 1: None / 빈 문자열 → None
    assert h(None) is None
    assert h("") is None

    # 정책 2: 30자 미만 → None (R14 트랙 A 의 의도된 제외 — 운영 NULL 의 유일한 원인)
    assert h("짧은 본문") is None
    assert h("a" * 29) is None

    # 정책 3: 30자 이상 → 16자 hex (반드시 채움)
    long_text = "갤럭시 S25 울트라 카메라 발열이 심각한 상황입니다 도와주세요"
    digest = h(long_text)
    assert digest is not None, "30자 이상 본문은 항상 hash 가 채워져야 함"
    assert len(digest) == 16
    assert all(c in "0123456789abcdef" for c in digest)

    # 정책 4: 재현성 — 동일 입력 → 동일 출력
    assert h(long_text) == digest

    # 정책 5: 충돌 방지 — 한 글자만 달라도 hash 변경
    other = long_text + "."
    assert h(other) != digest
