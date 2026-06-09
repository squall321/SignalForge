"""
GET /api/v1/dashboard/overview unit test (P1-2 MVP).

직접 실행:
    cd backend && .venv/bin/python tests/test_dashboard_overview.py

pytest:
    cd backend && .venv/bin/pytest tests/test_dashboard_overview.py -v
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.dashboard_service import DashboardService  # noqa: E402


async def _run_all():
    async with AsyncSessionLocal() as db:
        svc = DashboardService(db)

        # 1) 전체 (필터 없음, 30d)
        r = await svc.get_overview(period="30d")
        assert r.period == "30d"
        assert r.kpis.total_voc >= 0
        assert 0 <= r.kpis.neg_rate <= 100
        assert r.kpis.alert_count >= 0
        assert isinstance(r.trend14d, list)
        assert len(r.top_sites) <= 5
        print(f"[ok] all: total={r.kpis.total_voc} neg={r.kpis.neg_rate}% "
              f"top={r.kpis.top_product} alerts={r.kpis.alert_count} "
              f"trend_days={len(r.trend14d)} sites={len(r.top_sites)}")

        # 2) product 단일 필터
        r = await svc.get_overview(period="30d", product="GS26U")
        assert r.filters["product"] == "GS26U"
        assert r.kpis.total_voc >= 0
        # 단일 제품 필터 시 top_product는 본인 또는 None
        assert r.kpis.top_product in ("GS26U", None)
        print(f"[ok] product=GS26U: total={r.kpis.total_voc} top={r.kpis.top_product}")

        # 3) country 단일 필터
        r = await svc.get_overview(period="30d", country="KR")
        assert r.filters["country"] == "KR"
        assert r.kpis.total_voc >= 0
        print(f"[ok] country=KR: total={r.kpis.total_voc} sites={len(r.top_sites)}")

        # 4) platform 단일 필터
        r = await svc.get_overview(period="7d", platform="reddit")
        assert r.filters["platform"] == "reddit"
        assert r.period == "7d"
        # platform 필터 시 top_sites 는 reddit 단일 또는 비어있음
        codes = [s.code for s in r.top_sites]
        assert all(c == "reddit" for c in codes)
        print(f"[ok] platform=reddit 7d: total={r.kpis.total_voc} sites={codes}")

        # 5) 조합 (product + country + platform, 90d)
        r = await svc.get_overview(
            period="90d", product="GS26U", country="KR", platform="reddit"
        )
        assert r.period == "90d"
        assert r.filters["product"] == "GS26U"
        assert r.filters["country"] == "KR"
        assert r.filters["platform"] == "reddit"
        assert r.kpis.total_voc >= 0
        # 모든 trend 포인트는 sent_avg 가 -1~1
        for tp in r.trend14d:
            assert -1.0 <= tp.sent_avg <= 1.0
        print(f"[ok] combo: total={r.kpis.total_voc} "
              f"trend_days={len(r.trend14d)} sites={len(r.top_sites)}")


def test_dashboard_overview_all_filters():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 5 dashboard.overview cases passed.")
