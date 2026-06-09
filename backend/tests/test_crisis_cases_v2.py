"""
R10 트랙 C — crisis-cases 5건 확장 단위 테스트.

검증 5 케이스:
  1) endpoint 가 정확히 5건 반환
  2) GN7 / GZF1 / GS22U / GZFL3 / GS20 모두 catalog 에 포함
  3) 각 사건의 timeline 항목은 day, count(>=0) 구조
  4) period_start <= 모든 timeline.day <= period_end (catalog 와 일치)
  5) total_voc 와 timeline.count 합이 일치, neg_rate 는 [0,1]

실행:
    cd backend && .venv/bin/python tests/test_crisis_cases_v2.py
    cd backend && .venv/bin/pytest tests/test_crisis_cases_v2.py -v
"""
import asyncio
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.deep_service import (  # noqa: E402
    DeepService,
    CRISIS_CATALOG,
)
from app.schemas.deep import CrisisCasesResponse  # noqa: E402


EXPECTED_CODES = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}


async def _run_all() -> None:
    # case 1 — catalog 자체 검증 (런타임과 무관)
    catalog_codes = {spec["code"] for spec in CRISIS_CATALOG}
    assert len(CRISIS_CATALOG) == 5, f"catalog={len(CRISIS_CATALOG)}"
    assert catalog_codes == EXPECTED_CODES, f"codes={catalog_codes}"
    print(f"[ok] case1 catalog 5건: {sorted(catalog_codes)}")

    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # crisis_cases 는 redis_cache 데코레이터 적용 — 결과 모델 동일.
        r = await svc.crisis_cases()
        assert isinstance(r, CrisisCasesResponse)

        # case 2 — endpoint 5건 반환 및 코드 일치
        assert len(r.cases) == 5, f"got {len(r.cases)}"
        ret_codes = {c.code for c in r.cases}
        assert ret_codes == EXPECTED_CODES, f"return codes={ret_codes}"
        print(f"[ok] case2 endpoint 5건 반환: {sorted(ret_codes)}")

        # case 3 — timeline 구조
        for c in r.cases:
            for p in c.timeline:
                assert isinstance(p.day, str)
                assert p.count >= 0
        print("[ok] case3 timeline schema 정상")

        # case 4 — timeline day 범위가 catalog period 안에 있어야 함
        for c in r.cases:
            p_lo = date.fromisoformat(c.period_start)
            p_hi = date.fromisoformat(c.period_end)
            for p in c.timeline:
                d = date.fromisoformat(p.day)
                assert p_lo <= d <= p_hi, (
                    f"{c.code} day={d} not in [{p_lo},{p_hi}]"
                )
        print("[ok] case4 timeline day in period")

        # case 5 — total_voc 합 검증 + neg_rate 범위 + 사이트/키워드 schema
        rows = []
        for c in r.cases:
            tl_sum = sum(p.count for p in c.timeline)
            assert tl_sum == c.total_voc, (
                f"{c.code} timeline_sum={tl_sum} total={c.total_voc}"
            )
            assert 0.0 <= c.neg_rate <= 1.0, f"{c.code} neg_rate={c.neg_rate}"
            for kw in c.top_keywords:
                assert kw.count > 0
            for site in c.top_sites:
                assert site.count > 0
            rows.append(
                f"  {c.code:6s} period={c.period_start}..{c.period_end}  "
                f"voc={c.total_voc:5d}  neg={c.neg_rate:.3f}  "
                f"tl_len={len(c.timeline):3d}"
            )
        print("[ok] case5 voc 합·neg_rate·schema 정상")

        # 사양 요청: 5 사건별 voc + neg_rate + timeline 길이 표
        print("\n=== crisis-cases summary ===")
        print("\n".join(rows))


def test_crisis_cases_v2() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 5 crisis-cases v2 checks passed.")
