"""R9 트랙 A — galaxy-history 3 endpoint 단위 테스트.

실행:
    cd backend && .venv/bin/pytest tests/test_galaxy_history.py -v
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
    CrisisCasesResponse,
    GalaxyTimelineResponse,
    SeriesComparisonResponse,
)


async def _galaxy_timeline_series_s():
    """1) galaxy-timeline series=S — 모든 Galaxy S 모델이 출시순으로 반환."""
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)
        r = await svc.galaxy_timeline(series="GS")
        assert isinstance(r, GalaxyTimelineResponse)
        assert r.series == "GS"
        # GS 시리즈는 GS1 (2010) 부터 최소 30개 이상
        assert len(r.models) >= 20, f"GS models too few: {len(r.models)}"
        # 첫 모델은 GS1, 출시 가장 빠름
        first = r.models[0]
        assert first.code == "GS1"
        assert first.released_at == "2010-06-04"
        # 출시 정렬 검증
        dates = [m.released_at for m in r.models if m.released_at]
        assert dates == sorted(dates), "models must be ordered by released_at ASC"
        # 모든 모델 sent_avg / neg_rate 범위
        for m in r.models:
            assert -1.0 <= m.sent_avg <= 1.0
            assert 0.0 <= m.neg_rate <= 1.0
            assert m.peak_count >= 0
            assert m.total_count >= 0
            assert m.voc_7d_count >= 0
        print(f"[ok] galaxy-timeline GS: {len(r.models)} models, first={first.code}")


async def _crisis_cases_three():
    """2) crisis-cases — R10 트랙 C 로 catalog 가 5건으로 확장됨.
    GN7, GZF1, GS22U + GZFL3, GS20 가 모두 반환되어야 한다."""
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)
        r = await svc.crisis_cases()
        assert isinstance(r, CrisisCasesResponse)
        codes = [c.code for c in r.cases]
        assert codes == [
            "GN7",
            "GZF1",
            "GS22U",
            "GZFL3",
            "GS20",
        ], f"unexpected codes: {codes}"
        for c in r.cases:
            assert 0.0 <= c.neg_rate <= 1.0
            assert c.total_voc >= 0
            # timeline / top_keywords / top_sites 모두 list
            assert isinstance(c.timeline, list)
            assert isinstance(c.top_keywords, list)
            assert isinstance(c.top_sites, list)
            # period_start <= period_end
            assert c.period_start <= c.period_end
            # 키워드 / 사이트 카운트 양수
            for k in c.top_keywords:
                assert k.count > 0
            for s in c.top_sites:
                assert s.count > 0
        print(
            f"[ok] crisis-cases: {len(r.cases)} cases, "
            f"GN7 voc={r.cases[0].total_voc} sites={len(r.cases[0].top_sites)}"
        )


async def _series_comparison_s_n_z():
    """3) series-comparison series=GS,GN,GZ — 3 시리즈 모두 points 보유."""
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)
        r = await svc.series_comparison(series=["GS", "GN", "GZ"])
        assert isinstance(r, SeriesComparisonResponse)
        assert len(r.series_list) == 3
        for s in r.series_list:
            assert s.series in ("GS", "GN", "GZ")
            assert len(s.points) >= 1
            # gen 1..N 증가
            gens = [p.gen for p in s.points]
            assert gens == list(range(1, len(gens) + 1))
            # 출시일 정렬 (None 제외하고 비교)
            dates = [p.released_at for p in s.points if p.released_at]
            assert dates == sorted(dates), f"{s.series} not sorted"
            for p in s.points:
                assert -1.0 <= p.sent_avg <= 1.0
                assert 0.0 <= p.neg_rate <= 1.0
                assert p.count >= 0
        gs = next(s for s in r.series_list if s.series == "GS")
        # GS 첫 모델 == GS1
        assert gs.points[0].code == "GS1"
        print(
            f"[ok] series-comparison: GS={len(gs.points)} models, "
            f"total={r.meta['n_models_total']}"
        )


def test_galaxy_timeline_series_s():
    asyncio.run(_galaxy_timeline_series_s())


def test_crisis_cases_three():
    asyncio.run(_crisis_cases_three())


def test_series_comparison_s_n_z():
    asyncio.run(_series_comparison_s_n_z())


async def _run_all():
    await _galaxy_timeline_series_s()
    await _crisis_cases_three()
    await _series_comparison_s_n_z()


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 3 galaxy-history endpoints passed.")
