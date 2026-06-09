"""nlp/topic_classifier.py service_repair v3 단위 테스트 — Track B (R15, 2026-06-05).

배경: R13 spot-check 에서 service_repair F1 0.889 → 0.588 회귀.
원인 추정: AS/수리/리퍼/교환/환불 의 *명시적 단어 boundary* 가 부족해
컨텍스트 부스트로 다른 topic 에 흡수됨.

조치: 사전에 한/영 강신호 추가 (R15 계획서 1항):
  - 한국어: "AS 처리", "AS 받고 옴", "수리비용", "수리 견적",
            "리퍼 받고 온", "리퍼받음", "교환 신청", "교환 절차",
            "환불 처리", "환불 받은", "센터 방문", "센터 다녀옴",
            "삼성전자서비스"
  - 영문: "had to repair", "took it for repair", "got it replaced",
         "needed replacement", "samsung service center",
         "warranty extension"
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlp.topic_classifier import classify_topic  # noqa: E402


# ---------------------------------------------------------------------------
# 1) 한국어 — AS/수리비용 강신호
# ---------------------------------------------------------------------------
def test_kr_as_processing_and_cost():
    """AS 처리, AS 비용, AS 받고 옴 → service_repair primary."""
    out = classify_topic("AS 처리 어떻게 해야 하는지 모르겠네 진짜 답답")
    assert "service_repair" in out
    assert out[0] == "service_repair"

    out2 = classify_topic("AS 비용이 너무 많이 나와서 그냥 새로 살까 고민중")
    assert "service_repair" in out2

    out3 = classify_topic("AS 받고 옴 근데 다시 또 똑같은 증상 나옴")
    assert "service_repair" in out3


# ---------------------------------------------------------------------------
# 2) 한국어 — 수리비용/수리 견적
# ---------------------------------------------------------------------------
def test_kr_repair_cost_and_quote():
    """수리비용, 수리 견적 — 가격 토큰이 함께 와도 service_repair 우선."""
    out = classify_topic("화면 수리비용 50만원 나왔는데 그냥 새로 살까")
    assert "service_repair" in out
    # service_repair 가 price_purchase 보다 우선 (PRIMARY_PRIORITY)
    assert out[0] == "service_repair"

    out2 = classify_topic("폴드 수리 견적이 너무 비싸서 충격임")
    assert "service_repair" in out2


# ---------------------------------------------------------------------------
# 3) 한국어 — 리퍼/교환/환불
# ---------------------------------------------------------------------------
def test_kr_refurb_exchange_refund():
    """리퍼 받고 온, 리퍼받음, 교환 신청, 교환 절차, 환불 처리, 환불 받은."""
    out = classify_topic("리퍼 받고 온 지 일주일인데 또 화면 깜빡임 발생")
    assert "service_repair" in out
    assert out[0] == "service_repair"

    out2 = classify_topic("리퍼받음 다행히 보증기간 안에 처리됨")
    assert "service_repair" in out2

    out3 = classify_topic("교환 신청 했는데 부품 없다고 한참 기다리라네")
    assert "service_repair" in out3

    out4 = classify_topic("교환 절차가 너무 복잡해서 짜증남 진짜")
    assert "service_repair" in out4

    out5 = classify_topic("환불 처리 시간이 너무 오래 걸려서 불편함")
    assert "service_repair" in out5

    out6 = classify_topic("환불 받은 지 한참 됐는데 아직 입금 안됨")
    assert "service_repair" in out6


# ---------------------------------------------------------------------------
# 4) 한국어 — 센터 방문/다녀옴/삼성전자서비스
# ---------------------------------------------------------------------------
def test_kr_center_visit_and_samsung_service():
    """센터 방문, 센터 다녀옴, 삼성전자서비스."""
    out = classify_topic("센터 방문해서 점검 받고 왔는데 별 이상 없다고 함")
    assert "service_repair" in out
    assert out[0] == "service_repair"

    out2 = classify_topic("센터 다녀옴 진짜 사람 많아서 두 시간 기다림")
    assert "service_repair" in out2

    out3 = classify_topic("삼성전자서비스 예약 시스템 너무 불편한 거 아닌가요")
    assert "service_repair" in out3


# ---------------------------------------------------------------------------
# 5) 영문 — repair/replacement/service center/warranty extension
# ---------------------------------------------------------------------------
def test_en_repair_replacement_warranty():
    """had to repair, took it for repair, got it replaced, needed replacement,
    samsung service center, warranty extension."""
    out = classify_topic("had to repair the screen after only one month of use")
    assert "service_repair" in out
    assert out[0] == "service_repair"

    out2 = classify_topic("took it for repair last week and still no update")
    assert "service_repair" in out2

    out3 = classify_topic("got it replaced under warranty thankfully it was free")
    assert "service_repair" in out3

    out4 = classify_topic("needed replacement battery after just one year of use")
    assert "service_repair" in out4

    out5 = classify_topic("went to the samsung service center yesterday for a check")
    assert "service_repair" in out5

    out6 = classify_topic("considering the warranty extension worth it or not")
    assert "service_repair" in out6


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
