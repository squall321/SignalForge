"""nlp/topic_classifier.py R20 Track A 단위 테스트 (2026-06-05).

R20 변경점 검증:
  1) negative_general 짧은 phrase 환원 (R19 recall 0.250 → 0.50+ 회복).
     - 영문: 'very bad', 'really bad', 'is bad' 등.
     - 한국어: '엉망', '구질', '별로다'.
  2) comparison Discovery 권고 환원 (R19 F1 0.250 → 0.50+ 회복).
     - 'better than', 'is better than', 'rather than', '에 비해'.
     - 모델 2개 부스트(+2) 가 false positive 정밀도 보강.
  3) positive_general (R19 0.571) 회귀 없음 확인.

목표:
  - negative_general F1 0.50+ (R19 0.400 → +0.10)
  - comparison F1 0.50+ (R19 0.250 → +0.25)
  - positive_general 유지 (R19 0.571)
  - 전체 정확도 0.60+
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ---------------------------------------------------------------------------
# 1) negative_general 짧은 phrase 환원
# ---------------------------------------------------------------------------
def test_negative_display_very_bad_match():
    """'Is the s23 display very bad?' — R19 no_match 였던 케이스."""
    text = (
        "It's definitely easier on the eyes to see Quantum 6 after looking at S23. "
        "Is the s23 display very bad?"
    )
    out = classify_topic(text)
    # comparison 도 같이 매칭될 수 있음 — negative 가 포함되면 OK.
    assert "negative_general" in out, f"negative 미포함: {out}"


def test_negative_really_bad_phrase_match():
    """'camera is really bad on this phone' — 강신호 환원."""
    text = "The camera is really bad on this phone, can't take good photos at night"
    out = classify_topic(text)
    assert "negative_general" in out


def test_negative_korean_engmang_match():
    """'엉망' 단독 매칭 (한국어 강신호 환원)."""
    text = "S25 배터리 진짜 엉망이네요. 하루도 못가요"
    out = classify_topic(text)
    assert "negative_general" in out


# ---------------------------------------------------------------------------
# 2) comparison Discovery 권고 환원
# ---------------------------------------------------------------------------
def test_comparison_better_than_phrase():
    """'X is better than Y' — Discovery 권고 환원."""
    text = "iPhone 17 camera is better than the older models in low light"
    out = classify_topic(text)
    assert "comparison" in out


def test_comparison_rather_than_phrase():
    """'rather than' — implicit comparison 강신호."""
    text = "I would buy the Galaxy S25 rather than the iPhone 17 for the price"
    out = classify_topic(text)
    assert "comparison" in out


def test_comparison_korean_e_biehae_phrase():
    """'에 비해' 한국어 강신호."""
    text = "S25 에 비해 S24 가 가성비는 훨씬 좋네요. 둘다 좋은 폰이긴 합니다"
    out = classify_topic(text)
    assert "comparison" in out


# ---------------------------------------------------------------------------
# 3) positive_general 회귀 없음 (R19 0.571 유지)
# ---------------------------------------------------------------------------
def test_positive_loveit_still_matches():
    """'love this phone' — R19 phrase 그대로 작동."""
    text = "I absolutely love this phone, best purchase I've made in years"
    out = classify_topic(text)
    assert "positive_general" in out


def test_positive_negation_guard_still_works():
    """'would be excellent' — POSITIVE_NEGATION_PATTERNS 가드 작동."""
    out = classify_topic("For spare phone would be excellent if it lasted longer")
    assert "positive_general" not in out


# ---------------------------------------------------------------------------
# 4) false positive 방지 — 'is bad' generic context
# ---------------------------------------------------------------------------
def test_negative_is_bad_in_question_doesnt_dominate():
    """'Is it bad to wait for next gen?' — question 이 우선 (is bad 단독은 약신호)."""
    # R20: 'is bad' 단독 매칭은 짧은 평가에선 OK 지만 question pattern 우선.
    text = "Is it bad to wait for the next generation? Should I buy now or later?"
    out = classify_topic(text)
    # question 키워드 ('should i', '?') 가 강해서 question 이 primary 여야 함.
    assert "question" in out
    if "negative_general" in out:
        # primary 는 question
        assert out[0] == "question", f"primary 가 question 이어야 함 — {out}"


def test_comparison_better_than_with_2_models_boost():
    """Galaxy + iPhone 2개 모델 + 'better than' → comparison primary."""
    text = (
        "Galaxy S25 camera is better than iPhone 17 in low light. "
        "Tested both side by side for a week."
    )
    out = classify_topic(text)
    assert out, f"분류 결과가 비면 안 됨"
    assert out[0] == "comparison", f"primary 가 comparison 이어야 함 — {out}"


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
