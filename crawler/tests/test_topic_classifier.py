"""nlp/topic_classifier.py 단위 테스트 — Track B (R8, 2026-06-04).

10 topic multi-label 분류기. 한국어 + 영문 사전.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


def test_positive_general_korean():
    """긍정 일반 — '좋네요'."""
    got = classify_topic("진짜 좋네요 추천합니다")
    assert "positive_general" in got


def test_negative_general_korean():
    """부정 일반 — '실망', '비추'."""
    got = classify_topic("정말 실망입니다 비추합니다")
    assert "negative_general" in got


def test_question_korean():
    """질문 — '어디서', '?'."""
    got = classify_topic("이거 어디서 사나요 가격 궁금합니다")
    assert "question" in got


def test_comparison_english():
    """비교 — 'vs', 'better than'."""
    got = classify_topic("iphone vs galaxy which is better than")
    assert "comparison" in got


def test_price_purchase_korean():
    """가격/구매 — '샀', '할인'."""
    got = classify_topic("어제 할인받아서 구매했어요")
    assert "price_purchase" in got


def test_service_repair_korean():
    """AS/수리 — '서비스센터'."""
    got = classify_topic("서비스센터에서 수리 받고 왔습니다")
    assert "service_repair" in got


def test_experience_korean():
    """장기 사용 후기 — '한달', '쓰고 있'."""
    got = classify_topic("한달 쓰고 있는데 전반적으로 만족")
    assert "experience" in got


def test_expectation_korean():
    """기대/출시 — '기대', '출시'."""
    got = classify_topic("다음 모델 출시 기대됩니다 정말")
    assert "expectation" in got


def test_emotion_only_korean_slang():
    """감정만 — 'ㅋㅋㅋ' 단독."""
    assert classify_topic("ㅋㅋㅋ") == ["emotion_only"]
    assert classify_topic("ㅠㅠ") == ["emotion_only"]


def test_too_short_returns_empty():
    """10자 미만이면서 감정도 아니면 빈 리스트."""
    assert classify_topic("좋다") == []
    assert classify_topic("") == []
    assert classify_topic(None) == []  # type: ignore[arg-type]


def test_multi_label_korean():
    """멀티라벨 — 좋네요 (positive) + 할인 (price)."""
    got = classify_topic("좋네요 할인 받아 구매했어요")
    assert "positive_general" in got
    assert "price_purchase" in got


def test_no_match_default_empty():
    """매칭 없고 allow_other=False (기본) → []."""
    assert classify_topic("그냥 일반적인 잡담 텍스트입니다") == []


def test_no_match_allow_other():
    """매칭 없고 allow_other=True → ['other']."""
    assert classify_topic("그냥 일반적인 잡담 텍스트입니다", allow_other=True) == [
        "other"
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
