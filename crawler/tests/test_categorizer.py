"""nlp/categorizer.py 단위 테스트 — 12 카테고리 멀티라벨 분류.

P3.5 강화 (2026-06-03): 한국어 어미·조사 변이, Galaxy 모델 정규식.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.categorizer import classify_categories  # noqa: E402


def test_battery_korean_endings():
    """한국어 어미 변이 — '배터리가' 도 잡혀야 함."""
    assert classify_categories("배터리가 빨리 닳아요") == ["battery"]


def test_camera_korean_with_keyword():
    """카메라 + 야간 (camera 키워드 다중 적중)."""
    assert classify_categories("카메라 야간 모드 좋네요") == ["camera"]


def test_unrelated_returns_empty():
    """제품 키워드 없는 일반 문장 → 빈 리스트."""
    assert classify_categories("삼성전자 좋다") == []


def test_multi_label_korean():
    """멀티라벨 — display+battery+performance (뜨겁→performance, 화면→display, 배터리→battery)."""
    got = classify_categories("화면이 뜨겁고 배터리 빨리 닳음")
    assert set(got) >= {"display", "battery", "performance"}, got


def test_empty_text_returns_empty():
    """빈 본문 → []."""
    assert classify_categories("") == []
    assert classify_categories(None) == []  # type: ignore[arg-type]


# -- 추가 회귀 (강화 효과 검증) ----------------------------------------

def test_galaxy_model_triggers_comparison():
    """Galaxy 모델만 언급해도 comparison 으로 잡혀야 함 (이전엔 [])."""
    assert "comparison" in classify_categories("galaxy s22+")
    assert "comparison" in classify_categories("Fold 8 always coming?")


def test_korean_model_alias():
    """갤s25, 폴드7 같은 한국어 모델 약어도 인식."""
    assert "comparison" in classify_categories("갤s25 카메라 좋네")
    assert "comparison" in classify_categories("폴드7 힌지 망가짐")


def test_korean_slang_keywords():
    """한국어 슬랭/조어 — 가성비, 삼성덱스, 발열."""
    assert classify_categories("가성비 갑") == ["price"]
    assert classify_categories("삼성덱스 진짜 좋다") == ["software"]
    assert classify_categories("발열 진짜 심해요") == ["performance"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
