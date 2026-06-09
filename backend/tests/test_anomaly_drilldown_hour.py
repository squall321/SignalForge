"""
P4.2 트랙 E3 — anomaly-drilldown-hour 단위 테스트.

실행:
    cd backend && .venv/bin/python tests/test_anomaly_drilldown_hour.py
    cd backend && .venv/bin/pytest tests/test_anomaly_drilldown_hour.py -v
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
from app.schemas.deep import AnomalyDrilldownHourResponse  # noqa: E402


async def _pick_busy_hour(svc: DeepService) -> tuple[date, int]:
    """anchor 인근에서 hourly count 가 가장 큰 (date, hour) 선정."""
    tgt = await svc._anchor_date()
    # 최근 7일 중 hourly 가장 분포가 큰 날 찾기.
    for offset in range(0, 7):
        d = tgt - timedelta(days=offset)
        r = await svc.anomaly_drilldown(target_date=d, z_threshold=2.0, top_k=5)
        best = max(r.hourly, key=lambda h: h.count) if r.hourly else None
        if best and best.count > 0:
            return d, best.hour
    return tgt, 0


async def _run_all() -> None:
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)
        tgt, hour = await _pick_busy_hour(svc)

        # 1) 200 + schema + 정렬 검증.
        r = await svc.anomaly_drilldown_hour(
            target_date=tgt, hour=hour, limit=5, offset=0
        )
        assert isinstance(r, AnomalyDrilldownHourResponse)
        assert r.date == str(tgt)
        assert r.hour == hour
        assert r.total >= 0
        assert isinstance(r.items, list)
        assert len(r.items) <= 5

        for it in r.items:
            assert isinstance(it.id, int)
            assert it.content_preview is not None
            assert len(it.content_preview) <= 200
            if it.sentiment_label is not None:
                assert it.sentiment_label in ("positive", "negative", "neutral")
            if it.product is not None:
                assert it.product.code

        # 정렬 규칙: negative 가 positive 보다 앞.
        labels = [it.sentiment_label for it in r.items]
        neg_idx = [i for i, l in enumerate(labels) if l == "negative"]
        pos_idx = [i for i, l in enumerate(labels) if l == "positive"]
        if neg_idx and pos_idx:
            assert max(neg_idx) < min(pos_idx), (
                f"negative 가 positive 뒤에 옴: labels={labels}"
            )

        assert r.meta["limit"] == 5
        assert r.meta["offset"] == 0
        assert r.meta["returned"] == len(r.items)
        print(
            f"[ok] anomaly-drilldown-hour date={r.date} h={r.hour} "
            f"total={r.total} items={len(r.items)}"
        )

        # 2) 빈 시간대 (미래 일자 hour=3) → 200 + items=[] + total=0.
        future = (await svc._anchor_date()) + timedelta(days=90)
        r2 = await svc.anomaly_drilldown_hour(
            target_date=future, hour=3, limit=10, offset=0
        )
        assert r2.total == 0
        assert r2.items == []
        assert r2.hour == 3
        print(f"[ok] empty future date={r2.date} h=3 → 빈 결과")


def test_anomaly_drilldown_hour() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nanomaly-drilldown-hour endpoint passed.")
