"""
T4 국가 지도 4 endpoint 단위 테스트 (P3-2).

실행:
    cd backend && .venv/bin/python tests/test_geo_endpoints.py
    cd backend && .venv/bin/pytest tests/test_geo_endpoints.py -v

데이터셋: country_daily MV 2026-05-16 ~ 2026-06-02 (1016 행, 0004 migration).
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.geo_service import (  # noqa: E402
    GeoService,
    _ci_wald,
    _z_scores,
)


FROM = "2026-05-16"
TO = "2026-06-02"


def test_pure_helpers():
    """순수 함수: _z_scores / _ci_wald."""
    # flat → 모두 0
    assert _z_scores([1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0]
    # n<2 → [0]
    assert _z_scores([0.5]) == [0.0]
    # 대조군 — 중심값(평균)의 z 는 ~0, 양 끝은 부호 다름
    zs = _z_scores([-1.0, 0.0, 1.0])
    assert abs(zs[1]) < 1e-6
    assert zs[0] < 0 < zs[2]
    # _ci_wald: n=0 → 폭 0
    lo, hi = _ci_wald(0.5, 0)
    assert lo == hi == 0.5
    # n>0 → hi>lo
    lo, hi = _ci_wald(0.0, 100)
    assert lo < hi
    print(f"[ok] helpers: z_scores 중앙=0, CI(0,100)=[{lo},{hi}]")


async def _run_all():
    test_pure_helpers()

    async with AsyncSessionLocal() as db:
        svc = GeoService(db)

        # ── 1) choropleth ──────────────────────────────────
        res = await svc.choropleth(
            product_id=None,
            date_from=FROM,
            date_to=TO,
            metric="n",
        )
        assert len(res.items) >= 1, "최소 1 국가 데이터 필요"
        # KR 이 가장 많음 (103634건이 voc_records 기준 → MV 도 KR 최다)
        iso2s = [it.iso2 for it in res.items]
        assert "KR" in iso2s, f"KR not in items: {iso2s[:10]}"
        # totals 일관성
        assert res.totals.n == sum(it.n for it in res.items)
        assert res.totals.countries == sum(1 for it in res.items if it.covered)
        # sent_z covered 분포 — 평균 근방 z 가 적어도 1개
        zs = [it.sent_z for it in res.items if it.covered]
        if len(zs) >= 3:
            assert any(abs(z) < 1.5 for z in zs), "z 가 정상 분포되지 않음"
        # 모든 sent_avg 는 [-1,1] 범위
        for it in res.items:
            assert -1.0 <= it.sent_avg <= 1.0
        print(
            f"[ok] choropleth: items={len(res.items)} totals.n={res.totals.n} "
            f"totals.countries={res.totals.countries}"
        )

        # product_id 지정 — 결과는 작아지거나 같아야
        # country_daily 의 product_key 분포 확인 후 가장 많은 pid 추출
        from sqlalchemy import text  # local import to avoid top-level dependency
        top_pid_row = (await db.execute(text(
            "SELECT product_key, SUM(n)::int AS s "
            "FROM country_daily WHERE product_key >= 0 "
            "GROUP BY product_key ORDER BY s DESC LIMIT 1"
        ))).first()
        if top_pid_row is not None:
            top_pid = int(top_pid_row.product_key)
            res2 = await svc.choropleth(
                product_id=top_pid,
                date_from=FROM,
                date_to=TO,
                metric="n",
            )
            assert res2.totals.n <= res.totals.n
            print(
                f"[ok] choropleth product_id={top_pid}: "
                f"n={res2.totals.n} (<= 전체 {res.totals.n})"
            )

        # ── 2) drilldown (KR) ──────────────────────────────
        dr = await svc.drilldown(
            code="KR", date_from=FROM, date_to=TO, limit=5
        )
        assert dr.iso2 == "KR"
        assert dr.n > 0, "KR drilldown n=0 (데이터 없음)"
        assert -1.0 <= dr.sent_avg <= 1.0
        assert isinstance(dr.top_sites, list)
        assert isinstance(dr.top_products, list)
        assert isinstance(dr.top_categories, list)
        # top_sites 개수 <= limit
        assert len(dr.top_sites) <= 5
        # top_products 개수 <= limit
        assert len(dr.top_products) <= 5
        # 사이트 카운트 내림차순 검증
        if len(dr.top_sites) >= 2:
            for a, b in zip(dr.top_sites, dr.top_sites[1:]):
                assert a.n >= b.n
        print(
            f"[ok] drilldown KR: n={dr.n} sites={len(dr.top_sites)} "
            f"prods={len(dr.top_products)} cats={len(dr.top_categories)}"
        )

        # ── 3) diffusion ───────────────────────────────────
        df = await svc.diffusion(
            product_id=None,
            date_from=FROM,
            date_to=TO,
            granularity="day",
        )
        assert len(df.frames) >= 1
        # 날짜 정렬 검증
        days = [f.day for f in df.frames]
        assert days == sorted(days), f"frames not sorted: {days}"
        # 각 frame items 비어있지 않음
        for fr in df.frames:
            assert len(fr.items) >= 1
        # week granularity
        df_w = await svc.diffusion(
            product_id=None,
            date_from=FROM,
            date_to=TO,
            granularity="week",
        )
        # week frames 개수 < day frames 개수
        assert len(df_w.frames) <= len(df.frames)
        print(
            f"[ok] diffusion: day_frames={len(df.frames)} "
            f"week_frames={len(df_w.frames)}"
        )

        # ── 4) product-compare ─────────────────────────────
        # top_pid 가 없으면 1 로 fallback (DB 에 GS25 = id 1)
        pid = int(top_pid_row.product_key) if top_pid_row is not None else 1
        pc = await svc.product_compare(
            product_id=pid,
            countries=["KR", "US", "JP", "ZZ"],  # ZZ 는 없는 국가 (n=0 행 보장)
            date_from=FROM,
            date_to=TO,
        )
        assert len(pc.rows) == 4
        # ZZ 는 n=0
        zz = next(r for r in pc.rows if r.country == "ZZ")
        assert zz.n == 0
        assert zz.ci_lo == zz.ci_hi  # CI 폭 0
        # KR 은 n>=0 (대개 >0). CI 일관성
        for r in pc.rows:
            assert r.ci_lo <= r.ci_hi
            assert -2.0 <= r.ci_lo <= r.sent_avg <= r.ci_hi <= 2.0
        print(
            f"[ok] product-compare pid={pid}: rows={len(pc.rows)} "
            f"KR.n={next(r for r in pc.rows if r.country == 'KR').n}"
        )


def test_geo_endpoints():
    """pytest entry."""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 4 geo endpoints + helpers passed.")
