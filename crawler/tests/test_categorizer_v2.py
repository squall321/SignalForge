"""nlp/categorizer.py v2 회귀 — 한국어 인포멀 확장 + Galaxy 정규식 + others 옵션.

P3.6 트랙 C (2026-06-03).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.categorizer import classify_categories  # noqa: E402


def test_battery_informal_ending():
    """인포멀 어미 — '배빨리닳아요' / '충전느려' 가 battery."""
    assert "battery" in classify_categories("배빨리 닳아요 진짜로")
    assert "battery" in classify_categories("충전느려서 답답함")


def test_performance_informal_heat():
    """발열 인포멀 — '발열심함', '따끔' 이 performance."""
    assert "performance" in classify_categories("이거 발열심함 진짜")
    assert "performance" in classify_categories("따끔거리고 후끈해요")


def test_galaxy_model_re_english():
    """GALAXY_MODEL_RE 영문 매칭 — 'Galaxy S25 Ultra' → model_mention."""
    got = classify_categories("Galaxy S25 Ultra is great")
    assert "model_mention" in got
    got2 = classify_categories("My Z Fold 6 hinge cracked")
    assert "model_mention" in got2


def test_galaxy_model_re_korean():
    """한국어 모델 약어 — '폴드7', '갤s25' → model_mention."""
    assert "model_mention" in classify_categories("폴드7 힌지 별로")
    assert "model_mention" in classify_categories("갤s25 카메라 굿")


def test_others_option_long_text():
    """allow_others=True + 충분한 길이 → ['others'] 반환."""
    # 어떤 카테고리 키워드도 없는 충분히 긴 일반 문장
    text = "오늘 날씨가 참 좋아서 산책을 길게 했다 정말로요"
    assert classify_categories(text, allow_others=False) == []
    assert classify_categories(text, allow_others=True) == ["others"]


def test_others_skips_short_text():
    """짧은 텍스트는 allow_others=True 여도 빈 리스트."""
    short = "그냥"  # < MIN_OTHERS_LEN
    assert classify_categories(short, allow_others=True) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
