"""Harvest 7 Track X2 — non_target 태그 라이브 검증.

기존 backend 테스트 패턴 (httpx + 라이브 DB 의존성) 과 달리,
본 테스트는 백엔드 endpoint 가 아닌 *데이터 정합성* 을 검증한다:

1) AP/PX 시리즈 (Apple iPhone, Google Pixel) 의 mapped voc 중
   'non_target' 태그가 부여된 비율이 ≥99% 여야 한다 (스크립트 실행 후).
2) Galaxy (GS/GA/GZ/GN/...) 시리즈는 'non_target' 태그가 0건이어야 한다
   (보호 invariant — 절대 변경 금지).

동기 psycopg2 로 접속해 conftest 의 asyncpg dispose 패턴과 충돌을 회피한다.
DB 미가동 시 skip.

실행: pytest /home/koopark/claude/SignalForge/backend/tests/test_gsmarena_filter.py -v
"""
from __future__ import annotations

import asyncio
import os

import pytest


_DSN = os.getenv(
    "SF_TEST_DSN",
    "postgres://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)

NON_TARGET_SERIES = ("AP", "PX")
GALAXY_SAMPLE_SERIES = ("GS", "GA", "GZ", "GN", "GW", "GB", "TAB", "TABS")


async def _afetch(sql: str, *args):
    """asyncpg 직접 연결 — backend engine 의존성/이벤트루프 충돌 회피.

    asyncpg 는 $1/$2 의 positional placeholder 를 사용한다.
    """
    import asyncpg  # type: ignore
    conn = await asyncpg.connect(_DSN)
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


def _fetchone(sql: str, *args) -> tuple:
    return asyncio.run(_afetch(sql, *args))


def _db_alive() -> bool:
    try:
        _fetchone("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_alive(), reason="DB 미가동")
def test_non_target_invariants():
    """3 가지 invariant 동시 검증.

    A) AP/PX mapped voc total > 0 (실측 858 ± 자연 증가)
    B) AP/PX 의 non_target 태그율 ≥99% (스크립트 적용 후)
    C) Galaxy 샘플 시리즈의 non_target 태그 0 건 (PRESERVE 보장)

    SF_NON_TARGET_APPLIED=1 이면 B 강제.
    """
    # A & B
    row = _fetchone(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE 'non_target' = ANY(v.categories)) AS tagged
        FROM voc_records v JOIN products pr ON pr.id = v.product_id
        WHERE pr.series_code = ANY($1::text[])
        """,
        list(NON_TARGET_SERIES),
    )
    total, tagged = row["total"], row["tagged"]
    assert total > 0, "AP/PX mapped voc 가 0건 (스키마/데이터 이상)"

    ratio = tagged / total if total else 0.0
    if os.getenv("SF_NON_TARGET_APPLIED", "0") == "1":
        assert ratio >= 0.99, (
            f"non_target 태그율 {ratio:.3f} < 0.99 (tagged={tagged}/total={total})"
        )
    else:
        assert ratio >= 0.0

    # C) Galaxy invariant
    leak_row = _fetchone(
        """
        SELECT COUNT(*) AS leak
        FROM voc_records v JOIN products pr ON pr.id = v.product_id
        WHERE pr.series_code = ANY($1::text[])
          AND 'non_target' = ANY(v.categories)
        """,
        list(GALAXY_SAMPLE_SERIES),
    )
    leak = leak_row["leak"]
    assert leak == 0, f"Galaxy 시리즈에 non_target 태그가 누설됨: {leak} 건"


@pytest.mark.skipif(not _db_alive(), reason="DB 미가동")
def test_non_target_idempotent_categories_array_shape():
    """태그가 여러 번 추가되지 않았는지 (idempotent) 확인.

    어떤 voc 의 categories 에도 'non_target' 이 최대 1번 (중복 0).
    """
    row = _fetchone(
        """
        WITH t AS (
          SELECT v.id,
                 (SELECT COUNT(*) FROM unnest(v.categories) c WHERE c='non_target') AS dup
          FROM voc_records v JOIN products pr ON pr.id = v.product_id
          WHERE pr.series_code = ANY($1::text[])
        )
        SELECT COUNT(*) AS bad FROM t WHERE dup > 1
        """,
        list(NON_TARGET_SERIES),
    )
    bad = row["bad"]
    assert bad == 0, f"non_target 중복 태그 발견: {bad} 건 (idempotent 위반)"
