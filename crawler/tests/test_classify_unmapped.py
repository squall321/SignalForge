"""classify_unmapped — Track E 분류 로직 단위 테스트.

DB 미사용, classify_reason() 순수함수 만 검증.

요구 분류:
  1. too_short        : len < 10
  2. noise            : 잠금/회원전용/삭제됨/로그인 필요
  3. non_galaxy       : iPhone 만 언급, Samsung 부재
  4. no_model_mention : 정상 길이 본문이지만 모델명 없음

추가 경계:
  5. galaxy 컨텍스트가 있으면 non_galaxy 가 아님 (Samsung vs iPhone 비교 글).
"""
from __future__ import annotations

import pytest

from scripts.classify_unmapped import classify_reason


@pytest.mark.parametrize(
    "content, expected",
    [
        # 1. too_short
        ("짧음", "too_short"),
        ("", "too_short"),
        (None, "too_short"),
        (" " * 20, "too_short"),  # 공백만 — strip 후 길이 0.
        # 2. noise (잠금/회원전용/삭제됨/로그인 필요)
        ("이 글은 회원만 볼 수 있는 글입니다.", "noise"),
        ("1시간 내 작성된 글입니다. 로그인 후 확인하세요.", "noise"),
        ("관리자에 의해 삭제된 글입니다.", "noise"),
        # 3. non_galaxy (iPhone/Pixel 만 + Samsung 부재)
        ("iPhone 15 Pro 배터리 후기 — 정말 좋습니다.", "non_galaxy"),
        ("Pixel 9 Pro 카메라 비교 리뷰 작성합니다.", "non_galaxy"),
        ("샤오미 미밴드 7 후기 — 가성비 최고", "non_galaxy"),
        # 4. no_model_mention (정상 후기, 모델명 없음)
        ("오늘 백화점 갔다왔는데 사람이 너무 많았어요. 정말 짜증나네요.", "no_model_mention"),
        ("배터리 빨리 닳아서 새 핸드폰 사야할 것 같아요. 추천 좀.", "no_model_mention"),
        # 5. galaxy 컨텍스트 — iPhone 언급해도 non_galaxy 가 아님
        ("Galaxy S24 vs iPhone 15 비교 후기 — 카메라는 갤럭시가 낫네요.", "no_model_mention"),
        ("삼성 갤럭시 노트10 쓰다가 아이폰으로 갈아탔습니다.", "no_model_mention"),
    ],
)
def test_classify_reason(content, expected):
    assert classify_reason(content) == expected
