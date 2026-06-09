"""positive_general v2 사전 정제 단위 테스트 — R18 Track A (2026-06-05).

R17 spot-check 결과 positive_general F1 0.353 (R10 0.533 대비 -0.18).
주요 회귀 원인:
  1) 약신호 ("좋네"/"좋아요"/"좋다") 가 비교/구매/경험 글에 우연 매칭
  2) 가정/희망 ("좋겠다"/"would be nice") 가 positive 로 오분류 → expectation 누락
  3) 부정 ("만족하지 않"/"not satisfied") 가 positive 로 오분류 → negative 누락

R18 정제:
  1) 약신호 단독 어휘 제거, 명확한 평가 phrase 만 유지
  2) POSITIVE_NEGATION_PATTERNS 가드 — 부정/가정 매칭 시 positive 무효화

목표: positive_general F1 ≥ 0.6 회복 (R10 baseline 근접).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 1) 강신호 — 명확한 긍정 평가는 여전히 잡혀야 한다
# ──────────────────────────────────────────────────────────────────────────
def test_positive_strong_korean_satisfied():
    """'만족합니다' — 강신호 (단어 변형 포함)."""
    out = classify_topic("폴드 5 정말 만족합니다 추천드려요")
    assert "positive_general" in out
    assert out[0] == "positive_general", out


def test_positive_strong_english_love_it():
    """'love it' / 'highly recommend' — 강신호."""
    out = classify_topic("Galaxy S24 is fantastic, I love it and highly recommend it")
    assert "positive_general" in out


# ──────────────────────────────────────────────────────────────────────────
# 2) 약신호 제거 — 단순 '좋아요' / '좋네' 단독은 더 이상 positive 가 아니어야 한다
# ──────────────────────────────────────────────────────────────────────────
def test_positive_weak_signal_removed_with_comparison():
    """'좋네' + 비교 글 — 약신호 제거로 positive 안 잡힘.

    R17 confusion pattern: positive ↔ comparison.
    '갈아탔는데 좋네요' 같은 글은 comparison 이어야 함.
    """
    out = classify_topic("S22 에서 S24 로 갈아탔는데 카메라가 더 좋네요")
    # 약신호 '좋네요' 가 제거되어 positive 매칭 사라짐
    assert "positive_general" not in out, out
    assert "comparison" in out, out


def test_positive_weak_signal_removed_with_price():
    """'좋아요' + 가격 글 — 약신호 제거로 positive 안 잡힘.

    R17 confusion pattern: positive ↔ price.
    '1.45M 에 샀어요 좋아요' 는 price_purchase 가 강신호.
    """
    out = classify_topic("폴드7 1,200,000원에 샀어요 가성비 좋아요")
    # '좋아요' 가 제거되어 positive 매칭 없음, price 만 남음
    assert "positive_general" not in out, out
    assert "price_purchase" in out, out


# ──────────────────────────────────────────────────────────────────────────
# 3) 부정 가드 — '만족하지 않'/'not satisfied' 시 positive 안 잡힘
# ──────────────────────────────────────────────────────────────────────────
def test_positive_negation_korean():
    """'만족하지 않' — 강신호 어휘가 있어도 부정이면 무효."""
    # "만족합니다" 와 "만족하지 않" 둘 다 매칭되지만 negation 가드로 positive 무효
    out = classify_topic("처음엔 만족했는데 지금은 만족하지 않습니다 별로네요")
    assert "positive_general" not in out, out
    # negative 강신호도 있음
    assert "negative_general" in out, out


def test_positive_negation_english_dont_recommend():
    """'don't recommend' — positive 무효, negative 우선."""
    out = classify_topic(
        "I really wanted to love it but don't recommend this phone, terrible camera"
    )
    assert "positive_general" not in out, out
    assert "negative_general" in out, out


# ──────────────────────────────────────────────────────────────────────────
# 4) 가정 가드 — '좋겠다' / 'would be nice' 시 positive 안 잡힘
# ──────────────────────────────────────────────────────────────────────────
def test_positive_subjunctive_korean():
    """'좋겠다' — 미래 가정. expectation 영역.

    R17 confusion pattern: positive ↔ expectation.
    "다음 모델 좋겠다" 류는 expectation.
    """
    out = classify_topic("다음 출시 모델 카메라 더 좋아졌으면 좋겠다 기대중")
    assert "positive_general" not in out, out
    assert "expectation" in out, out


def test_positive_subjunctive_english_would_be_nice():
    """'would be nice' / 'looking forward' — expectation, not positive."""
    # 'love' (강신호) 가 있어도 'would be nice' 가 가드 발동
    out = classify_topic(
        "Looking forward to the Galaxy S26, would be nice if Samsung adds love"
    )
    assert "positive_general" not in out, out
    assert "expectation" in out, out


if __name__ == "__main__":  # pragma: no cover
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
