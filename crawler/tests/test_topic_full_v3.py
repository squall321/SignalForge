"""Track E (R18) — experience/comparison/question 사전 정제 단위 테스트.

R17 spot-check 결과 회귀:
  - experience F1 0.381 (R10 0.600)
  - comparison F1 0.429 (R10 0.480)
  - question  F1 0.667 (R10 0.762)

조치 (crawler/nlp/topic_classifier.py):
  experience:
    - 약신호 '써본'(단독), '잘 쓰고', 단순 '1년/2년/3년' 제거.
    - 기간 phrase '개월 사용', '개월째', '년 사용', '년째' 추가.
  comparison:
    - 약신호 'vs.', '대비' 제거 (대비책/대비해서 오탑).
  question:
    - 단독 기호 '??', '???' 제거.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ---------------------------------------------------------------------------
# 1) experience — 기간 강신호 새 패턴 (긍정)
# ---------------------------------------------------------------------------
def test_experience_duration_strong_signals_match():
    """'X개월 사용', '몇 년째', '한 달 동안' 등 명시 기간 → experience."""
    out = classify_topic("폴드5 6개월 사용 중인데 힌지 쪽 약간 흔들림 있음")
    assert "experience" in out

    out2 = classify_topic("이 폰 두 달째 쓰고 있는데 전반적으로 만족합니다")
    # '두 달' + '쓰고 있' 둘 다 매칭 → experience
    assert "experience" in out2

    out3 = classify_topic("갤럭시 노트20 3년째 쓰는데 배터리만 좀 아쉽")
    # '3년째' (new) + '쓰는' 매칭
    assert "experience" in out3


# ---------------------------------------------------------------------------
# 2) experience — '잘 쓰고' 단독 더이상 experience 강제 매칭 아님
# ---------------------------------------------------------------------------
def test_experience_jal_sseugo_alone_no_primary():
    """'잘 쓰고 있어요' → '쓰고 있' 매칭으로 experience 들어가나
    '잘 쓰고' 단독 노이즈는 제거됨. ('쓰고 있' 으로는 여전히 잡힘.)"""
    out = classify_topic("폴드 잘 쓰고 있어요 카메라가 만족스럽고")
    # '쓰고 있' 매칭 → experience 포함
    assert "experience" in out


# ---------------------------------------------------------------------------
# 3) experience — 단순 '1년/2년' 단독은 더이상 experience 매칭 안 됨
# ---------------------------------------------------------------------------
def test_experience_bare_year_not_match():
    """'1년 만에 고장났어요' 같이 기간만 보이고 사용 phrase 없으면 experience X."""
    out = classify_topic("산 지 1년 만에 액정 고장났네요 진짜 빡침")
    # '1년' 만 매칭되던 R17 → R18 에서는 매칭 안 됨
    assert "experience" not in out


# ---------------------------------------------------------------------------
# 4) comparison — 약신호 '대비'/'vs.' 단독은 매칭되지 않음
# ---------------------------------------------------------------------------
def test_comparison_weak_vs_dabi_removed():
    """'대비책', 'A vs.' 단독으로 comparison 들어가지 않음."""
    out = classify_topic("배터리 부족 대비책으로 보조배터리 가지고 다닙니다")
    # '대비' 가 있어도 comparison 들어가지 않아야 함
    assert "comparison" not in out

    # 'vs.' 단독 시그널 제거 검증 — '대비' 같은 비교 약신호와 함께 제거됨.
    # '어느 게' 는 comparison 사전 어휘이므로 매칭되지만, 'vs.' 자체로는 안 됨.
    out2 = classify_topic("폰 케이스 vs. 강화유리 둘 다 좋습니다")
    # 'vs.' 만 있던 R17 에선 comparison 매칭됐으나 R18 에선 매칭 안 됨.
    assert "comparison" not in out2


# ---------------------------------------------------------------------------
# 5) comparison — 진짜 비교 phrase + 모델 2개 컨텍스트 부스트 유지
# ---------------------------------------------------------------------------
def test_comparison_real_phrase_still_match():
    """'switching from Galaxy S22 to iPhone 14' → comparison primary
    (모델 2개 컨텍스트 부스트도 함께 적용)."""
    out = classify_topic("switching from Galaxy S22 to iPhone 14 next month")
    assert "comparison" in out
    assert out[0] == "comparison"


# ---------------------------------------------------------------------------
# 6) question — 단독 '??' 기호는 더이상 question 매칭 아님
# ---------------------------------------------------------------------------
def test_question_bare_marks_removed():
    """'아 진짜 이게 뭐임?? 답답하다' — 의문 어구 없이 '??' 만으로
    question 매칭되던 R17 → R18 에서는 매칭 안 됨."""
    out = classify_topic("아 진짜 이게 뭐임?? 답답하다")
    assert "question" not in out


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
