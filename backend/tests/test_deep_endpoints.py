"""
P3.6 트랙 A — 심층 분석 8 endpoint 단위 테스트.

실행:
    cd backend && .venv/bin/python tests/test_deep_endpoints.py
    cd backend && .venv/bin/pytest tests/test_deep_endpoints.py -v
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
    AnomalyContextResponse,
    CategoryProductMatrixResponse,
    CountrySentimentGapResponse,
    EngagementSentimentResponse,
    IssueLifecycleResponse,
    KeywordCooccurrenceResponse,
    NewTermSurvivalResponse,
    SiteDiffusionResponse,
)


async def _run_all():
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # 1) issue-lifecycle ------------------------------------------------
        r = await svc.issue_lifecycle(category=None, period_days=180, top_n=20)
        assert isinstance(r, IssueLifecycleResponse)
        for it in r.items:
            assert it.lifespan >= 1
            assert it.days_to_peak >= 0
            assert it.first_seen <= it.peak_day <= it.last_seen
        for cv in r.category_avg:
            assert cv.n_issues >= 1
        print(f"[ok] issue-lifecycle: items={len(r.items)} categories={len(r.category_avg)}")

        # 2) category-product-matrix ---------------------------------------
        r = await svc.category_product_matrix(period_days=90, top_products=10)
        assert isinstance(r, CategoryProductMatrixResponse)
        for c in r.cells:
            assert -1.0 <= c.score <= 1.0
            assert c.n >= 5
            assert c.flag in ("outlier_neg", "outlier_pos", "normal")
        print(f"[ok] category-product-matrix: products={len(r.products)} cats={len(r.categories)} cells={len(r.cells)}")

        # 3) site-diffusion -------------------------------------------------
        r = await svc.site_diffusion(period_days=180, min_sites=2, top_keywords=30)
        assert isinstance(r, SiteDiffusionResponse)
        for kw in r.keywords:
            assert len(kw.path) >= 2
            assert kw.total_span_days >= 0
            assert kw.origin_site == kw.path[0].site
            assert kw.terminal_site == kw.path[-1].site
        print(f"[ok] site-diffusion: keywords={len(r.keywords)} edges={len(r.edges)}")

        # 4) country-sentiment-gap -----------------------------------------
        r = await svc.country_sentiment_gap(period_days=90, top_products=10, min_n=10)
        assert isinstance(r, CountrySentimentGapResponse)
        for it in r.items:
            assert -1.0 <= it.score <= 1.0
            assert it.n >= 10
        for tg in r.top_gaps:
            assert tg.gap >= 0
        print(f"[ok] country-sentiment-gap: items={len(r.items)} top_gaps={len(r.top_gaps)}")

        # 5) engagement-sentiment ------------------------------------------
        r = await svc.engagement_sentiment(period_days=90)
        assert isinstance(r, EngagementSentimentResponse)
        # 빈 데이터 핸들링: 0 ≤ buckets ≤ 5
        assert len(r.buckets) <= 5
        for b in r.buckets:
            assert 1 <= b.bucket <= 5
            assert 0.0 <= b.neg_ratio <= 1.0
            assert -1.0 <= b.score <= 1.0
        for c in r.by_category:
            assert -1.0 <= c.corr_eng_neg <= 1.0
            assert 1 <= c.top_bucket <= 5
        print(f"[ok] engagement-sentiment: buckets={len(r.buckets)} cats={len(r.by_category)}")

        # 6) new-term-survival ---------------------------------------------
        r = await svc.new_term_survival(period_days=90, lookback_window=14, min_mentions=2)
        assert isinstance(r, NewTermSurvivalResponse)
        for it in r.items:
            assert it.cls in ("sustained", "mid", "flash")
            assert it.survival_days >= 0
            assert it.active_days >= 1
            assert it.total >= 2
        assert r.summary.sustained + r.summary.mid + r.summary.flash == len(r.items)
        print(f"[ok] new-term-survival: total={len(r.items)} sustained={r.summary.sustained} flash={r.summary.flash}")

        # 7) keyword-cooccurrence ------------------------------------------
        r = await svc.keyword_cooccurrence(period_days=90, min_edge_weight=2, top_nodes=80)
        assert isinstance(r, KeywordCooccurrenceResponse)
        for n in r.nodes:
            assert n.degree >= 1
            assert -1.0 <= n.sentiment_bias <= 1.0
        for e in r.edges:
            assert e.weight >= 2
            assert e.lift >= 0
        for p in r.top_pairs:
            assert p.k1 < p.k2 or p.k1 != p.k2
        print(f"[ok] keyword-cooccurrence: nodes={len(r.nodes)} edges={len(r.edges)} pairs={len(r.top_pairs)}")

        # 8) anomaly-context -----------------------------------------------
        r = await svc.anomaly_context(period_days=90, z_threshold=2.0)
        assert isinstance(r, AnomalyContextResponse)
        for sp in r.spikes:
            assert sp.z >= 2.0
            assert sp.count >= 1
            for kd in sp.top_keywords_delta:
                assert kd.delta > 0
            for ev in sp.matched_events:
                assert isinstance(ev.lag_days, int)
        print(f"[ok] anomaly-context: spikes={len(r.spikes)}")


def test_deep_endpoints():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 8 deep endpoints passed.")
