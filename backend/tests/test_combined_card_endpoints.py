"""
P3.7 트랙 B — 결합 카드 2 endpoint 단위 테스트.

실행 (격리):
    cd backend && .venv/bin/python tests/test_combined_card_endpoints.py
    cd backend && .venv/bin/pytest tests/test_combined_card_endpoints.py -v
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
    AnomalyWithDriversResponse,
    SentimentDriverResponse,
)


async def _run_all():
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # 1) sentiment-driver
        r = await svc.sentiment_driver(period_days=30, top_n=10)
        assert isinstance(r, SentimentDriverResponse)
        assert isinstance(r.items, list)
        for it in r.items:
            assert 0.0 <= it.before_neg_rate <= 1.0
            assert 0.0 <= it.after_neg_rate <= 1.0
            assert it.n_before >= 0
            assert it.n_after >= 1
            assert isinstance(it.related_categories, list)
            assert len(it.related_categories) <= 3
        assert "before_window" in r.meta and "after_window" in r.meta
        print(f"[ok] sentiment-driver: items={len(r.items)}")

        # 2) anomaly-with-drivers
        r2 = await svc.anomaly_with_drivers(period_days=14, z_threshold=2.0)
        assert isinstance(r2, AnomalyWithDriversResponse)
        assert isinstance(r2.anomalies, list)
        for a in r2.anomalies:
            assert a.z >= 2.0
            assert a.metric == "category_daily_count"
            assert a.value >= 0
            assert a.baseline >= 0
            assert isinstance(a.top_drivers, list)
            assert len(a.top_drivers) <= 5
            for d in a.top_drivers:
                assert isinstance(d.delta_pct, float)
                assert -1.0 <= d.sentiment <= 1.0
        print(f"[ok] anomaly-with-drivers: anomalies={len(r2.anomalies)}")


def test_combined_card_endpoints():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll combined card endpoints passed.")
