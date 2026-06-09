"""
T2 시계열+LLM 3 endpoint 단위 테스트 (P2-3).

실행:
    cd backend && .venv/bin/python tests/test_temporal_endpoints.py
    cd backend && .venv/bin/pytest tests/test_temporal_endpoints.py -v
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
from app.services.temporal_service import (  # noqa: E402
    TemporalService,
    detect_changepoints,
)


# 데이터셋 범위: 2026-05-16 ~ 2026-06-01 (mv_voc_daily 기준)
FROM = "2026-05-16"
TO = "2026-06-01"


def test_changepoint_unit():
    """순수 함수 단위 테스트 — change-point 검출."""
    # 평평한 시리즈 → 변화점 없음
    flat = [{"date": f"2026-05-{i:02d}", "count": 10, "sent_avg": 0.0}
            for i in range(1, 11)]
    cps = detect_changepoints(flat)
    assert cps == [], f"flat 시리즈는 change-point 없어야 함: {cps}"

    # 명확한 점프 시리즈 → count change-point 검출
    # 약간의 잡음을 섞어 local_std 가 의미있는 값 갖게 함
    jump_values = [10, 12, 9, 11, 10, 100, 105, 98, 102, 100]
    jump = [{"date": f"2026-05-{i:02d}", "count": v, "sent_avg": 0.0}
            for i, v in enumerate(jump_values, start=1)]
    cps = detect_changepoints(jump)
    assert any(cp.metric == "count" and cp.direction == "up" for cp in cps), \
        f"점프 시리즈는 'count up' change-point 있어야 함: {cps}"
    print(f"[ok] changepoint_unit: flat={len(detect_changepoints(flat))} "
          f"jump={len(cps)}")


async def _run_all():
    test_changepoint_unit()

    async with AsyncSessionLocal() as db:
        svc = TemporalService(db)

        # ── 1) temporal-series (기본) ────────────────────────
        res = await svc.get_series(
            product="GS25",
            categories=None,
            from_date=FROM,
            to_date=TO,
            bucket="day",
            metric="both",
            include_events=True,
            include_changepoints=True,
        )
        assert len(res.series) >= 1
        for p in res.series:
            assert -1.0 <= p.sent_avg <= 1.0
            assert 0.0 <= p.neg_rate <= 100.0
            assert 0.0 <= p.pos_rate <= 100.0
            assert p.count >= 0
        print(f"[ok] series GS25: pts={len(res.series)} events={len(res.events)} "
              f"cps={len(res.changepoints)}")

        # categories 필터
        res2 = await svc.get_series(
            product=None,
            categories=["battery"],
            from_date=FROM,
            to_date=TO,
            bucket="week",
            metric="both",
            include_events=False,
            include_changepoints=False,
        )
        assert res2.meta["source"] == "category_daily"
        assert res2.meta["bucket"] == "week"
        assert res2.events == []
        assert res2.changepoints == []
        print(f"[ok] series category=battery week: pts={len(res2.series)}")

        # ── 2) temporal-compare (products) ───────────────────
        cmp_res = await svc.compare(
            mode="products",
            keys=["GS25", "GS26"],
            from_date=FROM,
            to_date=TO,
            bucket="day",
        )
        assert cmp_res.a.key == "GS25"
        assert cmp_res.b.key == "GS26"
        assert isinstance(cmp_res.diff, list)
        for d in cmp_res.diff:
            assert isinstance(d.delta_count, int)
            assert isinstance(d.delta_sent, float)
        print(f"[ok] compare products: a={len(cmp_res.a.points)} "
              f"b={len(cmp_res.b.points)} diff={len(cmp_res.diff)}")

        # ── 3) llm-narrative (실제 ollama 호출) ──────────────
        # 작은 payload — qwen2.5:7b 호출
        payload = res.model_dump(mode="json")
        narr = await svc.llm_narrative(
            series_payload=payload,
            lang="ko",
        )
        assert isinstance(narr.summary, str)
        assert len(narr.summary) > 10
        # 한자 0 검증 (간단 휴리스틱 — CJK Unified Ideographs U+4E00-U+9FFF)
        han_chars = [c for c in narr.summary if "一" <= c <= "鿿"]
        assert not han_chars, f"summary 에 한자 발견: {han_chars[:10]}"
        print(f"[ok] llm-narrative: provider={narr.provider} "
              f"len={len(narr.summary)} 한자={len(han_chars)}")
        print(f"      첫 80자: {narr.summary[:80]}")


def test_temporal_endpoints():
    """pytest entry"""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 3 temporal endpoints + changepoint unit passed.")
