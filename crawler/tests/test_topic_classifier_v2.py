"""nlp/topic_classifier.py v2 단위 테스트 — Track A (R10, 2026-06-04).

R10 변경점 검증:
  - 사전 확장 (각 topic 별 새 어휘)
  - long-form head scan (LONG_THRESHOLD)
  - primary topic 우선순위 (signal density desc)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import (  # noqa: E402
    LONG_SCAN_HEAD_CHARS,
    LONG_THRESHOLD,
    classify_topic,
)


# ---------------------------------------------------------------------------
# 1) 사전 확장 — 새로 추가된 어휘 매칭
# ---------------------------------------------------------------------------
def test_positive_korean_extended_endings():
    """좋아유/좋았/좋더 등 어미 변형도 잡힘."""
    assert "positive_general" in classify_topic("이거 좋았어요 추천드림")
    assert "positive_general" in classify_topic("정말 좋더라구요 만족합니다")


def test_negative_korean_extended_endings():
    """별로네/별로임/별로예요 등 어미 변형."""
    assert "negative_general" in classify_topic("이번 모델 별로네요 후회중입니다")
    assert "negative_general" in classify_topic("진짜 별로임 하자가 많아서")


def test_question_korean_extended():
    """뭐예요/사도 되나/추천 좀 등."""
    assert "question" in classify_topic("이거 사도 되나요 정말 궁금하네")
    assert "question" in classify_topic("폰 추천 좀 해주세요 어떻게 할까")


def test_comparison_english_extended():
    """comparing/moved from 등 영문 확장."""
    out = classify_topic("Just moved from Pixel comparing camera quality now")
    assert "comparison" in out


def test_price_purchase_korean_extended():
    """샀어/샀음/구입했/예약구매 등."""
    assert "price_purchase" in classify_topic("어제 예약구매 했어요 할인받아서")
    assert "price_purchase" in classify_topic("이번달 구입했고 결제완료 했습니다")


def test_service_repair_korean_extended():
    """리퍼받/환불받/CS 접수."""
    assert "service_repair" in classify_topic("리퍼받았는데 보증기간 안에 처리됐어요")
    assert "service_repair" in classify_topic("환불받으려고 cs 접수 했어요 답이 없음")


def test_experience_korean_extended():
    """써본 결과/리뷰 남깁/장기사용."""
    assert "experience" in classify_topic("일년 써본 결과 만족스럽고 좋습니다")
    assert "experience" in classify_topic("장기 사용 후 리뷰 남깁니다 잘 쓰고있어요")


def test_expectation_korean_extended():
    """출시일/예상 스펙/언제쯤."""
    assert "expectation" in classify_topic("다음 출시일 언제쯤 일까요 예상 스펙 궁금")


# ---------------------------------------------------------------------------
# 2) Long-form head scan — 본문이 LONG_THRESHOLD 초과 시 앞부분만 본다
# ---------------------------------------------------------------------------
def test_long_form_tail_ignored():
    """긴 본문의 *꼬리* 에만 negative 키워드가 있으면 무시되어야 한다."""
    head = "오늘 날씨가 맑네요 " * 30  # ~330자, 의미는 잡담
    tail = " 진짜 별로네요 비추합니다"  # 부정 키워드
    text = head + tail
    assert len(text) > LONG_THRESHOLD
    out = classify_topic(text)
    # tail 의 negative 가 head 스캔 윈도우(<=LONG_SCAN_HEAD_CHARS)에 안 들어왔다면 비어야 함
    assert "negative_general" not in out


def test_long_form_head_kept():
    """긴 본문의 *앞부분* 에 키워드가 있으면 잡힌다."""
    head = "정말 좋네요 추천합니다 " * 5  # 짧은 head 안에 positive 키워드
    body = " 그리고 잡담이 계속 됩니다 " * 20
    text = head + body
    assert len(text) > LONG_THRESHOLD
    out = classify_topic(text)
    assert "positive_general" in out


def test_short_text_no_head_truncation():
    """LONG_THRESHOLD 이하 짧은 글은 전체 스캔."""
    short = "잡담 잡담 잡담 잡담 잡담 그래도 결국엔 별로네요"
    assert len(short) <= LONG_THRESHOLD
    out = classify_topic(short)
    assert "negative_general" in out


# ---------------------------------------------------------------------------
# 3) Primary topic 우선순위 — topics[0] = signal 강도 최대
# ---------------------------------------------------------------------------
def test_primary_topic_by_signal_strength():
    """price_purchase 키워드 2개 vs comparison 키워드 1개 → primary=price_purchase."""
    out = classify_topic("어제 할인받아 구매했어요 vs 다른 모델보다는 좀 더 좋")
    assert out, "결과가 비어 있으면 안 됨"
    assert "price_purchase" in out
    # primary 가 price_purchase 가 되어야 함 (매칭 어휘 수 더 많음)
    assert out[0] == "price_purchase"


def test_primary_priority_tie_break():
    """signal 동률이면 PRIMARY_PRIORITY 순서대로 — service_repair > positive.

    R18: 약신호 '좋네요' 제거됨 → 강신호 phrase '추천합니다' 로 갱신.
    """
    # 각 topic 키워드 1개씩
    text = "서비스센터에서 처리받았고 진짜 추천합니다"
    out = classify_topic(text)
    assert "service_repair" in out
    assert "positive_general" in out
    # tie-break: service_repair 가 더 앞
    assert out[0] == "service_repair"


# ---------------------------------------------------------------------------
# 4) 회귀 — 기존 동작 유지
# ---------------------------------------------------------------------------
def test_emotion_only_still_isolated():
    """순수 감정 표현은 여전히 단독 emotion_only."""
    assert classify_topic("ㅋㅋㅋ") == ["emotion_only"]
    assert classify_topic("ㅠㅠ") == ["emotion_only"]


def test_too_short_returns_empty():
    """10자 미만이면서 감정도 아니면 빈 리스트."""
    assert classify_topic("좋다") == []
    assert classify_topic("") == []


def test_no_match_default_empty():
    """매칭 없고 allow_other=False (기본) → []."""
    assert classify_topic("그냥 일반적인 잡담 텍스트입니다") == []


def test_no_match_allow_other():
    """매칭 없고 allow_other=True → ['other']."""
    assert classify_topic("그냥 일반적인 잡담 텍스트입니다", allow_other=True) == [
        "other"
    ]


def test_long_form_constants_sane():
    """가드 상수가 합리적 범위."""
    assert LONG_THRESHOLD >= 200
    assert LONG_SCAN_HEAD_CHARS <= LONG_THRESHOLD


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
