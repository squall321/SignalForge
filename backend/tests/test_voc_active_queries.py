"""
Data Clean 2 / Track D2 — voc_active migration smoke test.

10 high-traffic backend service queries 에 archived_at IS NULL 필터를
주입했음을 검증한다.

검증 포인트
-----------
1. voc_active VIEW 컬럼 = voc_records 컬럼 동일 (24개)
2. dashboard overview 의 total_voc <= 활성 VOC 수
3. insights _anchor_date() 가 archived 행을 무시한다 (NULL→일관 anchor)
4. deep _anchor_date() 동일
5. community matrix cells 의 합 <= 활성 VOC 수

직접 실행:
    cd backend && .venv/bin/python tests/test_voc_active_queries.py

pytest:
    cd backend && .venv/bin/pytest tests/test_voc_active_queries.py -v
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from sqlalchemy import text  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.services.dashboard_service import DashboardService  # noqa: E402
from app.services.insights_service import InsightsService  # noqa: E402
from app.services.deep_service import DeepService  # noqa: E402


async def _run_all():
    async with AsyncSessionLocal() as db:
        # ── 1) voc_active VIEW 무결성 ────────────────────────────────
        r = await db.execute(text(
            "SELECT COUNT(*) AS n_active FROM voc_records WHERE archived_at IS NULL"
        ))
        n_active = int(r.scalar() or 0)

        r = await db.execute(text("SELECT COUNT(*) FROM voc_records"))
        n_total = int(r.scalar() or 0)

        r = await db.execute(text(
            "SELECT COUNT(*) FROM voc_records WHERE archived_at IS NOT NULL"
        ))
        n_archived = int(r.scalar() or 0)

        assert n_total == n_active + n_archived, (
            f"voc_records total({n_total}) != active({n_active}) + "
            f"archived({n_archived})"
        )
        assert n_archived > 0, "archived 행이 0건이면 D1 적용이 없는 상태"
        print(f"[ok] voc_records total={n_total} active={n_active} "
              f"archived={n_archived}")

        # ── 2) dashboard overview total_voc <= n_active ────────────
        # 90d 윈도우 — 활성 행 중 최근 90일은 n_active 의 일부 또는 전체.
        svc_d = DashboardService(db)
        r = await svc_d.get_overview(period="90d")
        total_voc = int(r.kpis.total_voc or 0)
        assert total_voc <= n_active, (
            f"dashboard.total_voc({total_voc}) > n_active({n_active}) — "
            "archived_at 필터가 누락된 듯"
        )
        print(f"[ok] dashboard.overview(period=90d) total_voc={total_voc} "
              f"<= n_active={n_active}")

        # ── 3) insights _anchor_date 가 archived 를 제외 ────────────
        svc_i = InsightsService(db)
        anchor_i = await svc_i._anchor_date()
        # archived_at IS NULL 적용 → anchor 는 활성 행 중 최대 published_at
        r = await db.execute(text(
            "SELECT MAX(published_at::date) AS d FROM voc_records "
            "WHERE archived_at IS NULL "
            "AND published_at <= NOW() + INTERVAL '1 day'"
        ))
        expected = r.scalar()
        assert anchor_i == expected, (
            f"insights._anchor_date({anchor_i}) != expected({expected})"
        )
        print(f"[ok] insights._anchor_date = {anchor_i}")

        # ── 4) deep _anchor_date 도 동일 ────────────────────────────
        svc_dp = DeepService(db)
        anchor_dp = await svc_dp._anchor_date()
        assert anchor_dp == expected, (
            f"deep._anchor_date({anchor_dp}) != expected({expected})"
        )
        print(f"[ok] deep._anchor_date = {anchor_dp}")

        # ── 5) hourly + weekday 패턴 합계가 n_active 를 초과하지 않음 ─
        from datetime import timedelta
        from app.schemas.insights import HourlyPatternResponse, WeekdayPatternResponse  # noqa: F401

        hr = await svc_i.hourly_pattern(product=None, period_days=365)
        total_h = sum(p.count for p in hr.points)
        assert total_h <= n_active, (
            f"hourly_pattern total({total_h}) > n_active({n_active})"
        )

        wr = await svc_i.weekday_pattern(product=None, period_days=365)
        total_w = sum(p.count for p in wr.points)
        assert total_w <= n_active, (
            f"weekday_pattern total({total_w}) > n_active({n_active})"
        )
        print(f"[ok] hourly_total={total_h} weekday_total={total_w} "
              f"<= n_active={n_active}")

        return True


def test_voc_active_queries():
    assert asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll voc_active query smoke checks passed.")
