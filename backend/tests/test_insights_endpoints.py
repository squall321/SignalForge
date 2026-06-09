"""
T4 딥 인사이트 7 endpoint 단위 테스트 (P4 트랙 C).

실행:
    cd backend && .venv/bin/python tests/test_insights_endpoints.py
    cd backend && .venv/bin/pytest tests/test_insights_endpoints.py -v
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.insights_service import InsightsService, _normalize_scores  # noqa: E402


def test_normalize_scores_unit():
    """순수 함수 단위 — 정규화 동작."""
    assert _normalize_scores([]) == []
    assert _normalize_scores([1.0, 1.0, 1.0]) == [50.0, 50.0, 50.0]
    out = _normalize_scores([0.0, 5.0, 10.0])
    assert out[0] == 0.0 and out[2] == 100.0 and 49.0 <= out[1] <= 51.0
    print(f"[ok] normalize_scores unit: {out}")


async def _run_all():
    test_normalize_scores_unit()

    async with AsyncSessionLocal() as db:
        svc = InsightsService(db)

        # ── 1) hourly-pattern ─────────────────────────────────
        r = await svc.hourly_pattern(product=None, period_days=30)
        assert len(r.points) == 24, f"24행이어야 하는데 {len(r.points)}"
        for p in r.points:
            assert 0 <= p.hour <= 23
            assert p.count >= 0
            assert -1.0 <= p.sent_avg <= 1.0
        print(f"[ok] hourly-pattern: total={r.meta.get('total')} peak={r.meta.get('peak_hour')}")

        # product 필터
        r2 = await svc.hourly_pattern(product="GS25", period_days=30)
        assert len(r2.points) == 24
        print(f"[ok] hourly-pattern GS25: total={r2.meta.get('total')}")

        # ── 2) weekday-pattern ────────────────────────────────
        r = await svc.weekday_pattern(product=None, period_days=30)
        assert len(r.points) == 7
        labels = [p.label for p in r.points]
        assert labels == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for p in r.points:
            assert 0.0 <= p.neg_rate <= 100.0
            assert -1.0 <= p.sent_avg <= 1.0
        print(f"[ok] weekday-pattern: total={r.meta.get('total')}")

        # ── 3) emerging-keywords ──────────────────────────────
        r = await svc.emerging_keywords(period_days=7, top_n=20)
        # voc_keywords 가 있으므로 0 보다 많아야 함 (실제 시드 데이터에 emerging/declining 존재)
        total_returned = len(r.emerging) + len(r.declining)
        assert total_returned >= 1, f"emerging+declining 최소 1건은 있어야 함: {total_returned}"
        for t in r.emerging[:5]:
            assert t.this_week_count >= t.prev_week_count
        for t in r.declining[:5]:
            assert t.prev_week_count >= t.this_week_count
        print(f"[ok] emerging-keywords: emerging={len(r.emerging)} declining={len(r.declining)}")

        # ── 4) new-terms ──────────────────────────────────────
        r = await svc.new_terms(period_days=30)
        # 데이터 조건에 따라 0 일 수 있으므로 형식만 검증
        for item in r.items[:5]:
            assert item.count_recent >= 2
            assert item.first_seen
        print(f"[ok] new-terms: total={len(r.items)}")

        # ── 5) sentiment-swing ────────────────────────────────
        r = await svc.sentiment_swing(period_days=14, min_volume=10)
        assert isinstance(r.items, list)
        for it in r.items[:5]:
            assert it.n_before >= 10 and it.n_after >= 10
            assert -2.0 <= it.delta_pp <= 2.0
        print(f"[ok] sentiment-swing: total={len(r.items)}")

        # ── 6) product-lifecycle ──────────────────────────────
        r = await svc.product_lifecycle(product="GS25")
        # release_date 가 있으면 points 는 5개 (D+0/7/30/90/180)
        if r.release_date:
            assert len(r.points) == 5
            offsets = [p.d_offset for p in r.points]
            assert offsets == [0, 7, 30, 90, 180]
            for p in r.points:
                assert p.count >= 0
                assert -1.0 <= p.sent_avg <= 1.0
        print(f"[ok] product-lifecycle GS25: release={r.release_date} pts={len(r.points)}")

        # ── 7) platform-influence ─────────────────────────────
        r = await svc.platform_influence(period_days=30)
        assert isinstance(r.items, list)
        if r.items:
            # 점수는 0~100 범위 + 정렬됨
            assert r.items[0].score >= r.items[-1].score
            for it in r.items:
                assert 0.0 <= it.score <= 100.0
                assert it.drivers.engagement >= 0.0
                assert 0.0 <= it.drivers.neg_rate <= 100.0
        print(f"[ok] platform-influence: total={len(r.items)}")


def test_insights_endpoints():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 7 insights endpoints + normalize unit passed.")
