"""
P4.0 트랙 B — anomaly-drilldown 단위 테스트.

실행:
    cd backend && .venv/bin/python tests/test_anomaly_drilldown.py
    cd backend && .venv/bin/pytest tests/test_anomaly_drilldown.py -v
"""
import asyncio
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.deep_service import DeepService  # noqa: E402
from app.schemas.deep import AnomalyDrilldownResponse  # noqa: E402


async def _run_all() -> None:
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # 1) 실제 anomaly day 1건을 선정 (anomaly_with_drivers 첫 항목).
        aw = await svc.anomaly_with_drivers(period_days=14, z_threshold=2.0)
        if aw.anomalies:
            tgt_str = aw.anomalies[0].date
            tgt = date.fromisoformat(tgt_str)
        else:
            # anomaly 0건 환경 fallback — anchor_date 사용.
            tgt = await svc._anchor_date()

        r = await svc.anomaly_drilldown(target_date=tgt, z_threshold=2.0, top_k=10)
        assert isinstance(r, AnomalyDrilldownResponse)
        assert r.date == str(tgt)

        # hourly 는 24개 고정.
        assert len(r.hourly) == 24
        for h in r.hourly:
            assert 0 <= h.hour <= 23
            assert h.count >= 0
            assert -1.0 <= h.sent_avg <= 1.0
            assert 0.0 <= h.neg_rate <= 1.0

        # products / keywords / platforms 는 list (빈 배열 허용).
        assert isinstance(r.products, list)
        assert len(r.products) <= 5
        for p in r.products:
            assert p.code
            assert p.count >= 0
            assert 0.0 <= p.neg_rate <= 1.0

        assert isinstance(r.keywords, list)
        assert len(r.keywords) <= 10
        for k in r.keywords:
            assert k.keyword
            assert k.count >= 0
            assert isinstance(k.delta_pct, float)
            assert isinstance(k.related_products, list)
            assert len(k.related_products) <= 3

        assert isinstance(r.platforms, list)
        assert len(r.platforms) <= 5
        for pl in r.platforms:
            assert pl.code
            assert pl.count >= 0

        # anomaly_summary 필드 — 빈 케이스 z=0 허용.
        assert isinstance(r.anomaly_summary.z, float)
        assert r.anomaly_summary.value >= 0.0
        assert r.anomaly_summary.baseline >= 0.0

        # meta 윈도우 필드 확인.
        assert "baseline_window" in r.meta
        assert r.meta["top_k"] == 10
        print(
            f"[ok] anomaly-drilldown date={r.date} z={r.anomaly_summary.z} "
            f"products={len(r.products)} keywords={len(r.keywords)} "
            f"platforms={len(r.platforms)} hourly_nonzero={r.meta.get('n_hourly')}"
        )

        # 2) 빈 anomaly date — 미래 일자 호출시 200 + 빈 hourly[24] (count=0).
        future = (await svc._anchor_date()) + timedelta(days=60)
        r2 = await svc.anomaly_drilldown(target_date=future, z_threshold=2.0, top_k=10)
        assert isinstance(r2, AnomalyDrilldownResponse)
        assert len(r2.hourly) == 24
        assert all(h.count == 0 for h in r2.hourly)
        assert r2.products == []
        assert r2.keywords == []
        assert r2.platforms == []
        print(f"[ok] empty future date={r2.date} → 빈 결과 + hourly[24] 유지")


def test_anomaly_drilldown() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nanomaly-drilldown endpoint passed.")
