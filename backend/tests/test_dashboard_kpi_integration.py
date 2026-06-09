"""R18 트랙 B — KPI MV → /dashboard/overview 부분 통합 단위 테스트.

직접 실행:
    cd backend && .venv/bin/python tests/test_dashboard_kpi_integration.py

pytest:
    cd backend && .venv/bin/pytest tests/test_dashboard_kpi_integration.py -v

검증:
  1) period='24h' + 무필터 case → MV 경로 hit (kpi_overview 단일 쿼리).
     - kpi_overview MV 의 voc_24h / neg_rate_24h * 100 / top_product_24h 와 일치.
  2) period='30d' case → 기존 raw SQL 경로 유지 (MV 미사용).
     - DashboardKPIs 스키마 호환, 응답 정상.
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from sqlalchemy import text  # noqa: E402

from app.core import cache as _cache_mod  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.services.dashboard_service import DashboardService  # noqa: E402


async def _run_all():
    # 캐시 우회 — 본 테스트는 SQL 경로 / MV 경로 차이를 직접 측정한다.
    _cache_mod._reset_for_test()
    os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"  # 의도적 fail → cache bypass

    async with AsyncSessionLocal() as db:
        # MV 의 ground truth
        mv_row = (
            await db.execute(text("""
                SELECT voc_24h, neg_rate_24h, top_product_24h
                FROM kpi_overview WHERE id=1
            """))
        ).one_or_none()

        svc = DashboardService(db)

        # 1) period=24h + 무필터 → MV 경로 ----------------------
        assert DashboardService._is_mv_eligible("24h", None, None, None) is True
        r = await svc.get_overview(period="24h")
        assert r.period == "24h"
        assert r.filters == {"product": None, "country": None, "platform": None}
        assert r.kpis.total_voc >= 0
        assert 0 <= r.kpis.neg_rate <= 100
        assert isinstance(r.trend14d, list)
        assert len(r.top_sites) <= 5

        # MV 값과 정확 일치 (KPI 부분만)
        if mv_row is not None:
            assert r.kpis.total_voc == int(mv_row.voc_24h or 0)
            expected_neg = round(float(mv_row.neg_rate_24h or 0) * 100, 1)
            assert abs(r.kpis.neg_rate - expected_neg) < 0.11, (
                r.kpis.neg_rate, expected_neg,
            )
            assert r.kpis.top_product == mv_row.top_product_24h
        print(
            f"[ok] 24h MV hit: total={r.kpis.total_voc} neg={r.kpis.neg_rate}% "
            f"top={r.kpis.top_product} alerts={r.kpis.alert_count}"
        )

        # 2) period=30d → MV 미사용 (기존 raw SQL 유지) -------
        assert DashboardService._is_mv_eligible("30d", None, None, None) is False
        r30 = await svc.get_overview(period="30d")
        assert r30.period == "30d"
        assert r30.kpis.total_voc >= 0
        assert 0 <= r30.kpis.neg_rate <= 100
        assert r30.kpis.alert_count >= 0
        print(
            f"[ok] 30d raw SQL: total={r30.kpis.total_voc} neg={r30.kpis.neg_rate}% "
            f"top={r30.kpis.top_product} alerts={r30.kpis.alert_count}"
        )

        # 3) 24h + product 필터 → MV 미사용 (필터 있으므로 raw 경로) ----
        assert DashboardService._is_mv_eligible("24h", "GS26U", None, None) is False
        r24p = await svc.get_overview(period="24h", product="GS26U")
        assert r24p.period == "24h"
        assert r24p.filters["product"] == "GS26U"
        assert r24p.kpis.top_product in ("GS26U", None)
        print(
            f"[ok] 24h+product=GS26U raw SQL: total={r24p.kpis.total_voc} "
            f"top={r24p.kpis.top_product}"
        )


def test_dashboard_kpi_mv_integration():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll dashboard KPI MV integration cases passed.")
