"""
P3.7 트랙 D — 추가 deep cut 5 endpoint 단위 테스트.

실행:
    cd backend && .venv/bin/python tests/test_deep_v2_endpoints.py
    cd backend && .venv/bin/pytest tests/test_deep_v2_endpoints.py -v
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.deep_service import DeepService  # noqa: E402
from app.schemas.deep import (  # noqa: E402
    CategoryMomentumResponse,
    InfluenceRankResponse,
    KeywordNetworkResponse,
    LifecycleFunnelResponse,
    ProductFunnelResponse,
)


async def _run_all() -> None:
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # D1) category-momentum --------------------------------------
        r = await svc.category_momentum(period_days=60, bucket="week")
        assert isinstance(r, CategoryMomentumResponse)
        for c in r.categories:
            assert c.code
            for p in c.series:
                assert 0.0 <= p.share_pct <= 100.0
                assert p.n >= 0
        print(f"[ok] D1 category-momentum: cats={len(r.categories)}")

        # D2) keyword-network ----------------------------------------
        r = await svc.keyword_network(period_days=30, min_cooccur=10, max_nodes=80)
        assert isinstance(r, KeywordNetworkResponse)
        node_ids = {n.id for n in r.nodes}
        for n in r.nodes:
            assert n.freq >= 0
            assert n.community_id >= 0
        for e in r.edges:
            assert e.weight >= 10
            assert e.source in node_ids and e.target in node_ids
        print(
            f"[ok] D2 keyword-network: nodes={len(r.nodes)} "
            f"edges={len(r.edges)} comm={r.meta.get('total_communities')}"
        )

        # D3) lifecycle-funnel ---------------------------------------
        r = await svc.lifecycle_funnel(period_days=90)
        assert isinstance(r, LifecycleFunnelResponse)
        stage_names = [s.stage for s in r.stages]
        assert stage_names == ["신규", "성장", "정체", "감소"]
        for s in r.stages:
            assert s.n_keywords >= 0
            for ex in s.examples:
                assert ex.days_alive >= 0
                assert ex.peak_count >= 1
        print(
            "[ok] D3 lifecycle-funnel: "
            + " ".join(f"{s.stage}={s.n_keywords}" for s in r.stages)
        )

        # D4) influence-rank -----------------------------------------
        r = await svc.influence_rank(period_days=30, top_n=30)
        assert isinstance(r, InfluenceRankResponse)
        scores = [it.score for it in r.items]
        assert scores == sorted(scores, reverse=True)
        for it in r.items:
            assert 0.0 <= it.score <= 1.0
            assert 0.0 <= it.drivers.engagement <= 1.0
            assert 0.0 <= it.drivers.neg_rate <= 1.0
            assert 0.0 <= it.drivers.reach <= 1.0
        print(f"[ok] D4 influence-rank: items={len(r.items)}")

        # D5) product-funnel (GS25) ----------------------------------
        r = await svc.product_funnel(product="GS25", period_days=180)
        assert isinstance(r, ProductFunnelResponse)
        # release_date 가 있는 케이스만 stage 채워짐
        if r.meta.get("release_date"):
            assert len(r.stages) >= 1
            expected = ["출시", "인지", "관심", "구매고려", "실사용", "이탈"]
            for s in r.stages:
                assert s.stage in expected
                assert -1.0 <= s.sent_avg <= 1.0
                assert s.count >= 0
                assert len(s.top_keywords) <= 5
        print(
            f"[ok] D5 product-funnel: product={r.product} "
            f"stages={len(r.stages)} release={r.meta.get('release_date')}"
        )


def test_deep_v2_endpoints() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 5 deep v2 (track D) endpoints passed.")
