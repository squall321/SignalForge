"""
Compare LLM endpoint 단위 테스트 (트랙 D).

실행:
    cd backend && ../.venv/bin/python -m pytest tests/test_compare_llm.py -v

실제 LLM 호출은 mock — generate_compare_narrative 를 패치해 결정적 응답 사용.
schema/payload 빌더만 검증.
"""
import asyncio
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.schemas.insights import CompareLLMResponse  # noqa: E402
from app.services.insights_service import InsightsService  # noqa: E402


async def _run():
    async with AsyncSessionLocal() as db:
        svc = InsightsService(db)

        # 1) payload 빌더가 DB 쿼리 4종을 결합해 (count, sent_avg, neg/pos,
        #    top_categories[3], neg_keywords[5]) 를 모은다.
        payload = await svc._build_compare_payload(["GS25", "GS24"], 30)
        assert "products" in payload and len(payload["products"]) == 2
        for p in payload["products"]:
            assert "code" in p and "count" in p and "sent_avg" in p
            assert isinstance(p.get("top_categories"), list)
            assert isinstance(p.get("neg_keywords"), list)
            assert len(p["top_categories"]) <= 3
            assert len(p["neg_keywords"]) <= 5
        print(
            f"[ok] _build_compare_payload: products={len(payload['products'])} "
            f"total={sum(p['count'] for p in payload['products'])}"
        )

        # 2) compare_llm — generate_compare_narrative 를 mock 으로 갈음하고
        #    응답 schema 와 redis_cache 통과 여부만 검증.
        fake_narrative = (
            "**GS25** 와 **GS24** 비교 결과 ... (mock narrative)"
        )
        with mock.patch(
            "insight.compare_insight.generate_compare_narrative",
            return_value=(fake_narrative, 0.66, "mock-high"),
        ):
            resp = await svc.compare_llm(products=["GS25", "GS24"], period_days=30)

        assert isinstance(resp, CompareLLMResponse), type(resp)
        assert resp.products == ["GS25", "GS24"]
        assert resp.period_days == 30
        assert resp.tier_label == "mock-high"
        assert resp.narrative == fake_narrative
        assert 0.0 <= resp.grounding_score <= 1.0
        assert resp.generated_at  # ISO8601
        print(
            f"[ok] compare_llm: tier={resp.tier_label} "
            f"grounding={resp.grounding_score:.2f}"
        )


def test_compare_llm_endpoint():
    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_run())
    print("\ncompare_llm test passed.")
