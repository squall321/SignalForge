"""products 완전 커버리지 (0013) — Galaxy 전 세대 마이그레이션 검증.

Track A R8 (2026-06-04). 0013 alembic 적용 후 products 마스터 상태 검증.

설계:
  - 0013 이 신규 207~214 모델 시드 (A 구형, J 전체, M 확장, F 확장, Tab 구형,
    XCover 구형, Watch Classic/FE/Active3, Gear, Fit, Buds+/FE/IconX, Ring,
    옛 폰 Mega/Grand/Core/Ace/On/Star/Win 등).
  - 0010 까지 175 → 0013 적용 후 ≥ 325 (목표 안전 마진).
  - ON CONFLICT (code) DO UPDATE 멱등 — 재실행 안전.

검증:
  1) 전체 행 ≥ 325 (0013 후 약 389 기대).
  2) 핵심 신규 코드 7개 존재:
       GA3_15 (Galaxy A3 2015), GJ7PRO (J7 Pro), GM51 (M51), GF62 (F62),
       GTACT4P (Tab Active 4 Pro), GR1 (Ring), GNEDGE (Note Edge).
  3) released_at 가 의도된 NULL 외에는 채워져 있다.
  4) 시리즈 분포 — 신규 series=GOLD, JUMP, WIDE, TAB, GR 가 모두 존재.

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_products_complete.py -v
"""
import asyncio
import os
import sys

import pytest
from sqlalchemy import text

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402


# 0013 시점 의도적 NULL (출시일 미공개)
_UNDATED_EXPECTED = {
    # R6 / R7 부터 유지
    "GS26", "GS26P", "GS26U",
    "GFE25",
    "GZF8", "GZFL8",
    "GB4", "GB4P",
    "GR2",
    # R7 (0010) 신규 — 출시일 미공개 라인업
    "GW9",          # Watch9 미발표
    "GA27", "GA37", "GA57",
    "GF25",
}


async def _verify():
    async with AsyncSessionLocal() as db:
        # 1) 전체 행 ≥ 325
        total = (await db.execute(text("SELECT count(*) FROM products"))).scalar()
        assert total >= 325, f"products 행 수 {total} < 325"

        # 2) released_at NULL 인 행은 _UNDATED_EXPECTED 부분집합 — 0013 신규 행은
        #    원칙적으로 모두 released_at 채움 (Wikipedia 검증 기준).
        null_codes = set((await db.execute(
            text("SELECT code FROM products WHERE released_at IS NULL")
        )).scalars().all())
        unexpected = null_codes - _UNDATED_EXPECTED
        assert not unexpected, f"예상 외 released_at NULL 행: {sorted(unexpected)}"

        # 3) 핵심 신규 코드 7개 존재 + released_at 정확
        for code, expected_date in [
            ("GA3_15",  "2014-12-19"),
            ("GJ7PRO",  "2017-07-15"),
            ("GM51",    "2020-09-10"),
            ("GF62",    "2021-02-22"),
            ("GTACT4P", "2022-10-12"),
            ("GR1",     "2024-07-24"),
            ("GNEDGE",  "2014-09-26"),
        ]:
            row = (await db.execute(
                text("SELECT released_at FROM products WHERE code = :c"),
                {"c": code},
            )).first()
            assert row is not None, f"0013 신규 코드 {code} 미존재"
            assert str(row[0]) == expected_date, (
                f"{code} released_at 불일치: {row[0]} != {expected_date}"
            )

        # 4) 시리즈 분포 — 신규 series 등장
        series_counts = dict((await db.execute(text(
            "SELECT series_code, count(*) FROM products GROUP BY series_code"
        ))).all())
        # GOLD (옛 폰) / GR (Ring) / TAB (구형 Tab) 신규 등장
        for s, min_count in [("GOLD", 20), ("GR", 1), ("TAB", 9), ("GJ", 18)]:
            assert series_counts.get(s, 0) >= min_count, (
                f"series={s} 분포 {series_counts.get(s, 0)} < {min_count}"
            )

        print(
            f"[ok] products complete (R8) — total={total}, "
            f"undated={sorted(null_codes)}, series_dist={series_counts}"
        )


def test_products_complete_applied():
    """0013 마이그레이션 적용 후 products 마스터 상태 검증.

    DB 미가동이면 ConnectionRefusedError 가 발생, 그때만 skip.
    """
    try:
        asyncio.run(_verify())
    except (ConnectionRefusedError, OSError) as e:
        pytest.skip(f"postgres 미가동: {e}")


if __name__ == "__main__":
    asyncio.run(_verify())
    print("OK")
