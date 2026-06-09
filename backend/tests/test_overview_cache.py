"""
Dashboard overview 캐시 단위 테스트 (P3.6 트랙 B).

dashboard.overview 가 @redis_cache(ttl=120, prefix='dashboard:') 로 캐시되어
2회차 호출은 SQL 실행 없이 Redis HIT 로 응답하는지 검증.

실행:
    cd backend && .venv/bin/python tests/test_overview_cache.py
    cd backend && .venv/bin/pytest tests/test_overview_cache.py -v
"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.core import cache as cache_mod  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.services.dashboard_service import DashboardService  # noqa: E402


async def _run_all():
    # Redis 없으면 캐시 검증 자체가 무의미 → skip
    cache_mod._reset_for_test()
    client = cache_mod._get_redis()
    if client is None:
        print("[skip] Redis 비활성 — 캐시 검증 skip")
        return

    async with AsyncSessionLocal() as db:
        svc = DashboardService(db)

        # 안정적 비교를 위해 사전 캐시 키 제거.
        try:
            for k in client.scan_iter("dashboard:DashboardService.get_overview:*"):
                client.delete(k)
        except Exception:
            pass

        # 1차 호출 (cold, SQL 실행)
        t0 = time.perf_counter()
        r1 = await svc.get_overview(period="30d")
        cold_ms = (time.perf_counter() - t0) * 1000
        assert r1.period == "30d"
        assert isinstance(r1.trend14d, list)

        # 2차 호출 (warm, Redis HIT)
        t1 = time.perf_counter()
        r2 = await svc.get_overview(period="30d")
        warm_ms = (time.perf_counter() - t1) * 1000

        # 결과 동등성 — model_dump 비교 (cache HIT 응답은 model_cls 로 재구성).
        assert r2.model_dump() == r1.model_dump(), "캐시 hit 결과가 cold 와 달라짐"

        # 핵심 단언: warm < 5ms (Redis 로컬 GET 은 보통 1ms 이하)
        assert warm_ms < 5.0, (
            f"warm 호출이 {warm_ms:.2f}ms — Redis HIT 보장 실패 "
            f"(cold={cold_ms:.2f}ms)"
        )
        print(
            f"[ok] dashboard.overview cache: cold={cold_ms:.2f}ms "
            f"warm={warm_ms:.2f}ms speedup={cold_ms/max(warm_ms,1e-3):.1f}x"
        )


def test_dashboard_overview_cache_hit_under_5ms():
    """pytest entry — 2회차 호출 < 5ms."""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\ndashboard.overview cache 테스트 통과.")
