"""nlp/categorizer.py R6 — 옛 모델 정규식 회귀.

R6 (2026-06-04) 트랙 B:
- GALAXY_MODEL_RE 가 옛 모델까지 매칭하는지 검증.
- Note 7 / Note Edge / S10 5G / S8 / S9 / Galaxy Fold 1세대 등.
- 한국어 약어 영향 없음 (별도 패턴) 회귀.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.categorizer import classify_categories, GALAXY_MODEL_RE  # noqa: E402


def test_galaxy_note7_classified():
    """Note 7 발화 사건 — 'Galaxy Note 7' → model_mention + (battery/performance)."""
    text = "My Galaxy Note 7 explosion ruined my pocket"
    got = classify_categories(text)
    assert "model_mention" in got, f"Note 7 model_mention 누락: {got}"
    # 'explosion' 키워드는 직접 매핑 없지만 'battery' 키워드 'Galaxy battery'
    # 와는 별도 — 회귀의 핵심은 model_mention 부여 여부.


def test_galaxy_s10_5g_classified():
    """S10 5G — 'Galaxy S10 5G' → model_mention. 5G 토큰이 모델 매칭을 깨면 안 됨."""
    text = "Galaxy S10 5G camera review"
    got = classify_categories(text)
    assert "model_mention" in got, f"S10 5G model_mention 누락: {got}"
    # camera 도 함께 매칭되어야 함
    assert "camera" in got, f"camera 누락: {got}"


def test_historical_model_regex_direct():
    """GALAXY_MODEL_RE 정규식 직접 검증 — 옛 모델 다수 패턴."""
    must_match = [
        "Galaxy Note 7",
        "Note 7",
        "Note Edge",
        "Galaxy S10",
        "Galaxy S10 5G",
        "Galaxy S9",
        "Galaxy S8",
        "Galaxy Fold",
        "Galaxy Watch Active",
        "Buds Live",
    ]
    for text in must_match:
        assert GALAXY_MODEL_RE.search(text), (
            f"옛 모델 정규식 매칭 실패: {text!r}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
