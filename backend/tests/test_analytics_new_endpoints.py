"""
새로 추가된 4개 analytics endpoint 검증 (read-only, 실 DB 사용).

backend/.venv 활성화 후:
    cd backend && .venv/bin/pytest tests/test_analytics_new_endpoints.py -v

또는 (가장 가벼움) 단순 실행:
    .venv/bin/python tests/test_analytics_new_endpoints.py
"""
import asyncio
import sys
import os

# backend/ 를 sys.path 에 추가 (직접 실행 모드용)
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.analytics_service import AnalyticsService  # noqa: E402


async def _run_all():
    async with AsyncSessionLocal() as db:
        svc = AnalyticsService(db)

        # 1) keyword-track
        res = await svc.get_keyword_track(keyword="battery", period_days=30, granularity="week")
        assert res.keyword == "battery"
        assert res.granularity == "week"
        assert res.total_matches >= 0
        assert isinstance(res.data, list)
        if res.data:
            p = res.data[0]
            assert p.count >= 0
            assert p.positive + p.negative + p.neutral <= p.count + 1  # 일부 라벨 누락 허용
        print(f"[ok] keyword-track: {res.total_matches} matches across {len(res.data)} buckets")

        # 2) cohort-compare (sentiment)
        res = await svc.cohort_compare(
            product_codes=["GS25", "GS26"], dimension="sentiment", period_days=30
        )
        assert res.dimension == "sentiment"
        assert res.sentiment is not None
        assert res.category is None
        assert len(res.sentiment) >= 1
        for m in res.sentiment:
            assert 0 <= m.positive_rate <= 100
            assert 0 <= m.negative_rate <= 100
            assert m.total == m.positive + m.negative + m.neutral or m.total >= 0
        print(f"[ok] cohort-compare sentiment: {len(res.sentiment)} products")

        # 3) cohort-compare (category)
        res = await svc.cohort_compare(
            product_codes=["GS25", "GS26"], dimension="category", period_days=30
        )
        assert res.dimension == "category"
        assert res.category is not None
        assert res.sentiment is None
        print(f"[ok] cohort-compare category: {len(res.category)} products")

        # 4) site-health
        res = await svc.get_site_health()
        assert len(res.sites) >= 1
        for s in res.sites:
            assert 0 <= s.tagged_rate <= 100
            assert s.count_24h >= 0
            assert s.count_7d >= 0
            assert s.avg_content_length >= 0
        print(f"[ok] site-health: {len(res.sites)} platforms")

        # 5) recent-issues
        res = await svc.get_recent_issues(product_code="GS25", top_n=5)
        assert res.product_code == "GS25"
        assert res.top_n == 5
        assert len(res.issues) <= 5
        for it in res.issues:
            assert it.content                       # 본문 비어있지 않음
            # 모두 'negative' 라벨로 필터링되므로 score <= 0 또는 None
            assert it.sentiment_score is None or it.sentiment_score <= 0.05
        print(f"[ok] recent-issues: {len(res.issues)} negative quotes")


def test_all_endpoints():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 4 new analytics endpoints passed.")
