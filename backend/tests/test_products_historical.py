"""products release boost (0008+0009) — Galaxy 17년 역사 마이그레이션 검증.

Track A R6. 0008 (legacy_products) + 0009 (release_boost) 가 적용된 후의
products 마스터 상태를 검증.

설계:
  - 0008 이 옛 모델 66개 시드 (S1~21, Note 1~20, Z 1~4, Watch 1~5, Buds 1,
    AP11~13, PX5~7, GA50~55) — 모두 released_at 채움.
  - 0009 가 (a) 기존 48행 released_at 보강 (b) 0008 누락 옛 iPhone 6/7/8/X +
    옛 Pixel 1~4 추가 INSERT.

검증:
  1) 전체 행 ≥ 100 (122 기대).
  2) released_at NULL 인 행이 의도된 미정 모델 (GS26 family, GFE25,
     GZF8/GZFL8, GB4 family, GR2) 외에는 없다.
  3) 핵심 신규 코드 (GN7 발화, GS1, GZF1) 존재 — 0008 시드.
  4) 기존 48행 released_at 보강 (GS22, GS24U, AP14, PX8) — 0009 UPDATE.
  5) 0009 신규 옛 모델 (AP6, PX1) 존재.

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_products_historical.py -v
"""
import asyncio
import os
import sys

import pytest
from sqlalchemy import text

# backend/ 를 sys.path 에 추가 (직접 실행 모드용)
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402


# 출시일 미정 (현 시점 의도적 NULL — 출시 전 또는 미공개 라인업)
_UNDATED_EXPECTED = {
    "GS26", "GS26P", "GS26U",   # 차차세대 (출시일 미공개)
    "GFE25",                     # FE25 출시일 미공개
    "GZF8", "GZFL8",            # Z 8세대 미발매
    "GB4", "GB4P",              # Buds4 출시일 미공개
    "GR2",                      # Ring2 출시일 미공개
    # R7 (0010) 신규 라인업 — 출시 전 또는 미공개
    "GA27", "GA37", "GA57",     # A 차차세대
    "GF25",                      # F25 미공개
    "GW9",                       # Watch9 미발표
}


async def _verify():
    async with AsyncSessionLocal() as db:
        # 1) 전체 행 수 ≥ 100
        total = (await db.execute(text("SELECT count(*) FROM products"))).scalar()
        assert total >= 100, f"products 행 수 {total} < 100"

        # 2) released_at NULL 인 행은 _UNDATED_EXPECTED 부분집합
        null_codes = set((await db.execute(
            text("SELECT code FROM products WHERE released_at IS NULL")
        )).scalars().all())
        unexpected = null_codes - _UNDATED_EXPECTED
        assert not unexpected, f"예상 외 released_at NULL 행: {sorted(unexpected)}"

        # 3) 핵심 신규 코드 존재 (0008 시드)
        #    GN7: Note 7 발화 (2016-08-19) — VOC 부정 검색 기반
        #    GS1: 최초 갤럭시 (2010-06-04) — 17년 역사 시작
        #    GZF1: 최초 폴드 (2019-09-06)
        for code, expected_date in [
            ("GN7", "2016-08-19"),
            ("GS1", "2010-06-04"),
            ("GZF1", "2019-09-06"),
        ]:
            row = (await db.execute(
                text("SELECT released_at FROM products WHERE code = :c"),
                {"c": code},
            )).first()
            assert row is not None, f"신규 코드 {code} 미존재"
            assert str(row[0]) == expected_date, (
                f"{code} released_at 불일치: {row[0]} != {expected_date}"
            )

        # 4) 기존 48행 released_at 보강 (0009 UPDATE)
        for code, expected_date in [
            ("GS22",  "2022-02-25"),
            ("GS24U", "2024-01-31"),
            ("AP14",  "2022-09-16"),
            ("PX8",   "2023-10-12"),
        ]:
            row = (await db.execute(
                text("SELECT released_at FROM products WHERE code = :c"),
                {"c": code},
            )).first()
            assert row is not None, f"기존 코드 {code} 미존재"
            assert str(row[0]) == expected_date, (
                f"{code} 보강 실패: {row[0]} != {expected_date}"
            )

        # 5) 0009 신규 옛 모델 (0008 누락 보강분)
        for code in ("AP6", "PX1"):
            row = (await db.execute(
                text("SELECT released_at FROM products WHERE code = :c"),
                {"c": code},
            )).first()
            assert row is not None, f"0009 신규 코드 {code} 미존재"
            assert row[0] is not None, f"{code} released_at 가 NULL"

        print(f"[ok] products release boost — total={total}, undated={sorted(null_codes)}")


def test_products_release_boost_applied():
    """0008 + 0009 마이그레이션 적용 후 products 마스터 상태 검증.

    DB 미가동이면 ConnectionRefusedError 가 발생, 그때만 skip.
    그 외 예외는 그대로 실패 → 마이그레이션 검증 신뢰성 보장.
    """
    try:
        asyncio.run(_verify())
    except (ConnectionRefusedError, OSError) as e:
        pytest.skip(f"postgres 미가동: {e}")


if __name__ == "__main__":
    asyncio.run(_verify())
    print("OK")
