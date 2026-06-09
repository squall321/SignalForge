"""
Compare Insight 단위 테스트 (트랙 D).

실행:
    cd /home/koopark/claude/SignalForge/crawler
    ../.venv/bin/python -m pytest tests/test_compare_insight.py -v
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import compare_insight as ci
from insight.llm_provider import LLMProvider


_PAYLOAD = {
    "period_days": 30,
    "products": [
        {
            "code": "GS25",
            "name_ko": "갤럭시 S25",
            "count": 12345,
            "sent_avg": -0.02,
            "neg_count": 1234,
            "pos_count": 4567,
            "top_categories": [
                {"code": "price", "name_ko": "가격", "n": 800},
                {"code": "battery", "name_ko": "배터리", "n": 450},
            ],
            "neg_keywords": [
                {"keyword": "발열", "n": 42},
                {"keyword": "비싸다", "n": 31},
            ],
        },
        {
            "code": "GS24",
            "name_ko": "갤럭시 S24",
            "count": 9876,
            "sent_avg": 0.04,
            "neg_count": 654,
            "pos_count": 5432,
            "top_categories": [
                {"code": "price", "name_ko": "가격", "n": 320},
                {"code": "camera", "name_ko": "카메라", "n": 280},
            ],
            "neg_keywords": [
                {"keyword": "발열", "n": 18},
                {"keyword": "배터리", "n": 22},
            ],
        },
    ],
}


# ────────────────────────────────────────────────────────────────────────────
# 1) build_compare_prompt_payload — 표 변환 입력이 grounding 이 검증할 수 있는 shape
# ────────────────────────────────────────────────────────────────────────────
def test_build_compare_prompt_payload_shape():
    p = ci.build_compare_prompt_payload(_PAYLOAD)

    # by_product 두 행, 콤마 포함 숫자 추출이 가능해야 함
    assert isinstance(p["by_product"], list) and len(p["by_product"]) == 2
    codes = [row["code"] for row in p["by_product"]]
    assert codes == ["GS25", "GS24"], codes
    assert p["by_product"][0]["n"] == 12345
    assert p["by_product"][0]["neg"] == 1234
    assert p["by_product"][1]["pos"] == 5432

    # total = 합산
    assert p["total"] == 12345 + 9876

    # by_category_neg 는 두 제품 합산이 우선
    cat_codes = [r["code"] for r in p["by_category_neg"]]
    assert "price" in cat_codes
    price_row = next(r for r in p["by_category_neg"] if r["code"] == "price")
    assert price_row["n"] == 800 + 320

    # top_negative 키워드 12개 이하 + 첫 행 텍스트에 키워드 포함
    assert len(p["top_negative"]) <= 12
    assert any("발열" in row["text"] for row in p["top_negative"])

    # 표 marldown 변환이 깨지지 않는지 — grounding.metrics_to_markdown 호출
    from insight.grounding import metrics_to_markdown
    md = metrics_to_markdown(p, schema_desc=ci.COMPARE_SCHEMA_DESC)
    assert "12,345" in md and "GS25" in md
    assert "9,876" in md and "GS24" in md
    print("  [PASS] build_compare_prompt_payload: shape + markdown 포함")


# ────────────────────────────────────────────────────────────────────────────
# 2) generate_compare_narrative — provider mock 으로 grounding 검증
# ────────────────────────────────────────────────────────────────────────────
def test_generate_compare_narrative_with_mocked_provider():
    """provider mock 이 표 수치를 인용한 텍스트를 반환하면 grounding > 0.4 가 되어야 한다."""

    class _MockProv(LLMProvider):
        name = "mock"
        tier_label = "mock-high"

        def summarize(self, prompt: str):  # pragma: no cover (호출 안 됨)
            return None

        def summarize_json(self, payload, schema_desc, instructions, **kwargs):
            # 표의 핵심 수치/코드/이름을 다수 인용 — grounding 검증을 통과해야 함
            return (
                "**갤럭시 S25(GS25)** 는 **12,345건**으로 **갤럭시 S24(GS24) 9,876건** 대비 "
                "수집량이 많습니다. 부정은 각각 **1,234건**, **654건** 으로 GS25 의 부정 "
                "비율이 높으며, 긍정은 각각 **4,567건**, **5,432건** 으로 집계되었습니다. "
                "부정 카테고리 1위는 가격(price) 으로 합산 **1,120건**, "
                "그 다음은 배터리(battery) **450건**, 카메라(camera) **280건** 입니다. "
                "부정 키워드 중 발열 은 두 제품에서 공통적으로 등장합니다."
            )

    text, score, tier_label = ci.generate_compare_narrative(
        _PAYLOAD,
        provider=_MockProv(),
    )
    assert text is not None and "12,345" in text and "GS24" in text
    assert score >= 0.4, f"mock provider 가 수치 다수 인용 → 점수 >= 0.4 기대, 실측 {score}"
    assert tier_label == "mock-high"
    print(f"  [PASS] generate_compare_narrative: score={score:.2f} tier={tier_label}")


# ────────────────────────────────────────────────────────────────────────────
# 보조) products < 2 → skip
# ────────────────────────────────────────────────────────────────────────────
def test_generate_compare_narrative_too_few_products_returns_none():
    text, score, tier = ci.generate_compare_narrative({"products": [{"code": "GS25"}]})
    assert text is None and score == 0.0 and tier == "skipped"
    print("  [PASS] generate_compare_narrative: products<2 → skip")


if __name__ == "__main__":  # pragma: no cover
    test_build_compare_prompt_payload_shape()
    test_generate_compare_narrative_with_mocked_provider()
    test_generate_compare_narrative_too_few_products_returns_none()
    print("\nAll 3 compare_insight tests passed.")
