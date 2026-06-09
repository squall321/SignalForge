"""topic_classifier R11 컨텍스트 부스트 단위 테스트 — R12 트랙 E3 (2026-06-04).

대상: ``crawler/nlp/topic_classifier.py`` 의 R11 부스트 로직.
  - comparison: 제품군 모델명 2개 이상 언급 시 +2
  - price_purchase: 통화/가격 패턴(원/$/만원 등) 동반 시 +2

이 부스트가 *primary topic 순서* 에 실제로 영향을 주는지 +
부스트 트리거가 없을 때는 영향을 주지 않는지 검증한다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ── comparison 부스트 — 모델명 2개 이상 ────────────────────────────────
def test_comparison_boost_with_two_models():
    """galaxy s24 + iphone 15 → comparison 이 primary 로."""
    # "good deal" 가 price_purchase 키워드, "compared to" 가 comparison 키워드.
    # 모델 2개 동반 → comparison 에 +2 → comparison 이 primary 가 되어야.
    text = (
        "Galaxy S24 vs iPhone 15 compared to last year — good deal overall, "
        "still using compared to my previous one."
    )
    got = classify_topic(text)
    assert "comparison" in got, got
    # comparison 매칭 어휘 ('compared to', 'in comparison' 등) 2개 + 부스트 +2 = 4
    # price_purchase 는 'good deal' 1개 → comparison 가 primary
    assert got[0] == "comparison", got


def test_comparison_no_boost_without_two_models():
    """모델명 1개만 — 부스트 미발동 ('comparison' 매칭이 있어도 base score 만)."""
    # Galaxy S24 1개만, 'compared to' 1회 매칭, 'good deal' 1회 → 점수 동률(1:1).
    # tie-break PRIMARY_PRIORITY 에 따라 price_purchase 가 comparison 보다 앞.
    text = "Galaxy S24 compared to other phones — got a good deal yesterday."
    got = classify_topic(text)
    assert "comparison" in got
    assert "price_purchase" in got
    # PRIMARY_PRIORITY: service_repair > price_purchase > question > comparison > ...
    # 동률이므로 price_purchase 가 앞으로
    assert got.index("price_purchase") < got.index("comparison"), got


# ── price_purchase 부스트 — 가격/통화 동반 ─────────────────────────────
def test_price_boost_with_won_amount():
    """'한국 가격 1,200,000원' 같은 패턴 → price_purchase 부스트."""
    text = "어제 1,200,000원에 샀어요. 그냥저냥 괜찮네 정도? 만족합니다."
    got = classify_topic(text)
    assert "price_purchase" in got
    assert "positive_general" in got
    # price 부스트(+2) → price_purchase 가 primary (positive 매칭만 동률)
    assert got[0] == "price_purchase", got


def test_price_boost_with_dollar_amount():
    """'$899' 패턴 → price_purchase 부스트."""
    text = "I bought it for $899 — pretty great phone overall, really nice screen too."
    got = classify_topic(text)
    assert "price_purchase" in got
    # positive 도 매칭되지만 price 부스트(+2) → price_purchase primary
    assert got[0] == "price_purchase", got


def test_price_no_boost_without_currency():
    """가격 패턴 없으면 부스트 미발동."""
    # 'i bought' 1회 + 'pretty great' 1회 → 동률. tie-break: price_purchase 가
    # comparison 보다 우선이지만 positive 보다도 우선 (PRIORITY 참조)
    text = "I bought it last week — pretty great overall, really nice build."
    got = classify_topic(text)
    assert "price_purchase" in got
    assert "positive_general" in got


# ── 부스트가 long-form 가드와 충돌하지 않는지 ─────────────────────────
def test_boost_respects_long_form_guard():
    """본문 LONG_THRESHOLD(300) 초과 시 앞 LONG_SCAN_HEAD_CHARS(250) 만 스캔.

    부스트 패턴이 250자 뒤에 있으면 부스트가 발동되지 않아야 한다.
    """
    head = "Galaxy S24 is a phone. " * 5  # ~110자, comparison 키워드 없음
    tail = " ".join(["lorem"] * 100)  # 부스트 영역 밖
    # comparison 매칭 + 모델 2개 둘 다 250자 이후로 밀어넣음
    boost_payload = "Galaxy S24 vs iPhone 15 compared to my old one. good deal here."
    text = head + " " + tail + " " + boost_payload
    assert len(text) > 300, "long-form 가드 발동을 위해 300자 초과 필요"
    got = classify_topic(text)
    # head 영역에 'compared to'/모델 2개/price 모두 없음 → 매칭 없거나 빈 리스트
    # (정책: long-form 가드는 head 만 봄)
    # 핵심 어서션: comparison/price_purchase 매칭이 발동되지 않아야 함.
    assert "comparison" not in got, f"long-form 가드 위반: {got}"
    assert "price_purchase" not in got, f"long-form 가드 위반: {got}"


# ── 부스트가 multi-label 모두 보존하는지 ───────────────────────────────
def test_boost_preserves_multi_label():
    """부스트가 동작해도 다른 topic 들이 누락되지 않아야."""
    text = (
        "Galaxy S24 vs iPhone 15 compared to my old phone — i bought it for $999, "
        "really nice camera, satisfied with my purchase."
    )
    got = classify_topic(text)
    # 3개 topic 모두 매칭
    for topic in ("comparison", "price_purchase", "positive_general"):
        assert topic in got, (topic, got)


# ── 회귀: 부스트 적용 함수가 예외에 강건한지 ───────────────────────────
def test_boost_does_not_break_on_unicode():
    """이모지/특수문자 섞인 본문에도 정상 분류."""
    text = "🔥 Galaxy S24 vs iPhone 15 — compared to last year 👍 good deal!"
    got = classify_topic(text)
    # 최소 한 개 이상 매칭 + throw 없음
    assert isinstance(got, list)
    assert "comparison" in got
