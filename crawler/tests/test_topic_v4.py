"""nlp/topic_classifier.py R19 Track A 단위 테스트 (2026-06-05).

R19 변경점 검증:
  1) experience 약신호 'been using it'/'after using it'/'have been using'
     제거 — sunscreen 등 비 VOC 텍스트 false positive 차단.
  2) comparison 키워드 'switched from' 추가 — R18 누락분.
  3) Experience 컨텍스트 부스트 — '3달 사용', '6개월 사용', '1년 사용',
     'for 3 months', 'for 1 year' 등 명시 기간 phrase 가 있으면 +2.
     → comparison/positive_general 와 동시 매칭 시 experience 우선.

목표:
  - R18 v3 의 experience F1 0.211 회귀를 다시 0.50+ 로 회복
  - positive_general 0.556 / comparison 0.516 유지 또는 개선
  - 전체 정확도 0.65+
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ---------------------------------------------------------------------------
# 1) experience 약신호 제거 — generic 'been using' false positive 차단
# ---------------------------------------------------------------------------
def test_experience_generic_been_using_no_longer_matches():
    """sunscreen 등 비 VOC 문맥에서 'been using' 만으로는 experience 매칭 안 함."""
    text = (
        "People who have been using sunscreen since they were young "
        "and people who have not are very different in skin condition."
    )
    out = classify_topic(text)
    assert "experience" not in out, f"experience false positive: {out}"


def test_experience_after_using_generic_removed():
    """'after using it' 약신호도 단독으로는 experience 안 됨."""
    text = "After using it once you can tell the difference in workflow"
    out = classify_topic(text)
    # 매칭 없으면 빈 리스트가 정답 (allow_other=False 기본)
    assert "experience" not in out


def test_experience_strong_phrase_still_matches():
    """'in my experience' / 'owned for' 등 강신호는 여전히 매칭."""
    assert "experience" in classify_topic(
        "In my experience the camera holds up under low light"
    )
    assert "experience" in classify_topic(
        "I have owned for over a year and the battery is solid"
    )


def test_experience_korean_period_still_matches():
    """한국어 기간 + 사용기/사용 phrase 유지."""
    assert "experience" in classify_topic("일년 써본 결과 만족스럽고 좋습니다")
    assert "experience" in classify_topic("장기 사용 후 리뷰 남깁니다 잘 쓰고 있어요")


# ---------------------------------------------------------------------------
# 2) comparison 'switched from' 보류
# ---------------------------------------------------------------------------
def test_comparison_switched_to_still_matches():
    """'switched to' 는 여전히 comparison 으로 분류 (R10 어휘)."""
    text = (
        "I switched to the Galaxy S25 last month and the camera is much better."
    )
    out = classify_topic(text)
    assert "comparison" in out


# ---------------------------------------------------------------------------
# 3) Experience 명시 기간 부스트
# ---------------------------------------------------------------------------
def test_experience_boost_korean_period_primary():
    """'3달 사용기 ... 다른 폰보다 낫고' — experience 가 primary 가 되어야 한다."""
    text = "3달 사용기 입니다. 너무 좋아서 다른 폰보다 낫고 만족합니다."
    out = classify_topic(text)
    assert out, "분류 결과가 비면 안 됨"
    assert out[0] == "experience", (
        f"experience 가 primary 여야 하는데 {out[0]} 가 됨 — 전체 {out}"
    )


def test_experience_boost_english_for_n_months():
    """'for 6 months' 명시 기간 phrase 매칭 + 부스트."""
    text = (
        "I have been holding this phone for 6 months and switched from the previous model. "
        "Camera is solid."
    )
    out = classify_topic(text)
    # 'switched from' (comparison) + 'for 6 months' (experience boost +2)
    # experience 가 primary 여야 함.
    assert "experience" in out
    assert out[0] == "experience", f"primary={out[0]}, 전체={out}"


def test_experience_boost_short_text_no_period_no_boost():
    """기간 phrase 없으면 부스트 적용 안 됨 — comparison primary 유지."""
    text = "Switched to the new model, camera comparison is interesting"
    out = classify_topic(text)
    # 'switched to' (comparison) 만 매칭. experience 키워드 없음.
    assert "experience" not in out
    assert "comparison" in out


# ---------------------------------------------------------------------------
# 4) 회귀 — R18 v1 성공 케이스 유지
# ---------------------------------------------------------------------------
def test_regression_positive_negation_guard():
    """'would be excellent' — POSITIVE_NEGATION_PATTERNS 가드가 여전히 작동."""
    out = classify_topic("For spare phone would be excellent")
    assert "positive_general" not in out


def test_regression_service_repair_primary():
    """'서비스센터에서 처리받았고 ... 추천합니다' — service_repair primary."""
    out = classify_topic("서비스센터에서 처리받았고 진짜 추천합니다")
    assert out[0] == "service_repair"


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
