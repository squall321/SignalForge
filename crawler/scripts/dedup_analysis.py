"""R15 트랙 A — dedup 정책 효과 정량 평가.

R14 의 content_hash 기반 dedup 이 voc_records 를 168,112 → 113,557 (-32%)
로 줄였다. 본 스크립트는 그 *분석 가치 손실* 을 측정한다.

측정 항목:
1. 현재 hash 그룹 분포 (dedup 후 잔존 중복 확인)
2. cross-site 중복 (동일 본문이 여러 platform 에 존재) — 사이트별 인용/재전재
3. 사이트별 no_hash (단문) 비율 — dedup 영향에서 제외된 영역
4. 단문 영역 중복도 — 만약 단문도 dedup 한다면 추가로 사라질 행 추정
5. 사이트별 linked / negative 비율 — 분석 가치 지표

DB 직결, 멱등 read-only. 외부 키 불필요.

산출 (stdout JSON):
    {
        "hash_group_distribution": [...],
        "cross_site_dup": [...],
        "no_hash_by_site": [...],
        "short_content_dup_potential": [...],
        "analytic_quality_by_site": [...],
        "summary": {...}
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dedup_analysis")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)


async def _q(db: AsyncSession, sql: str) -> List[Dict[str, Any]]:
    rows = (await db.execute(text(sql))).mappings().all()
    return [dict(r) for r in rows]


async def analyze() -> Dict[str, Any]:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # 1) hash 그룹 분포 (현재 잔존 중복)
        hash_dist = await _q(db, """
            WITH grouped AS (
              SELECT platform_id, content_hash, COUNT(*) AS c
              FROM voc_records
              WHERE content_hash IS NOT NULL
              GROUP BY 1,2
            )
            SELECT CASE WHEN c=1 THEN '1' WHEN c=2 THEN '2'
                        WHEN c BETWEEN 3 AND 5 THEN '3-5'
                        ELSE '6+' END AS bucket,
                   COUNT(*) AS groups,
                   SUM(c)  AS rows
            FROM grouped GROUP BY 1 ORDER BY 1
        """)

        # 2) cross-site 중복 (같은 hash 가 N 개 platform)
        cross_site = await _q(db, """
            WITH cs AS (
              SELECT content_hash, COUNT(DISTINCT platform_id) AS sites, COUNT(*) AS rows
              FROM voc_records
              WHERE content_hash IS NOT NULL
              GROUP BY content_hash
              HAVING COUNT(DISTINCT platform_id) > 1
            )
            SELECT sites, COUNT(*) AS hash_groups, SUM(rows) AS total_rows
            FROM cs GROUP BY sites ORDER BY sites
        """)

        # 3) 사이트별 no_hash (length<30) 비율
        no_hash = await _q(db, """
            SELECT p.code,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE v.content_hash IS NULL) AS no_hash,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE v.content_hash IS NULL)
                         / NULLIF(COUNT(*),0), 2) AS no_hash_pct,
                   ROUND(AVG(length(content_original))) AS avg_len
            FROM voc_records v JOIN platforms p ON p.id = v.platform_id
            GROUP BY p.code
            HAVING COUNT(*) >= 300
            ORDER BY no_hash_pct DESC
        """)

        # 4) 단문(<30) 영역 중복도 — 정책 완화 시 추가 삭제 추정
        #    distinct(content_original) 와 total 차이 = 본문 중복 row 수
        short_dup = await _q(db, """
            SELECT p.code,
                   COUNT(*) FILTER (WHERE length(content_original) < 30) AS short_rows,
                   COUNT(DISTINCT content_original)
                       FILTER (WHERE length(content_original) < 30) AS distinct_short,
                   COUNT(*) FILTER (WHERE length(content_original) < 30)
                     - COUNT(DISTINCT content_original)
                         FILTER (WHERE length(content_original) < 30) AS would_delete
            FROM voc_records v JOIN platforms p ON p.id = v.platform_id
            WHERE p.code IN ('dcinside','instiz','ppomppu','dogdrip','mlbpark','slrclub')
            GROUP BY p.code
            ORDER BY would_delete DESC
        """)

        # 5) 사이트별 분석 가치 (linked / negative)
        quality = await _q(db, """
            SELECT p.code,
                   COUNT(*) AS voc,
                   COUNT(*) FILTER (WHERE v.product_id IS NOT NULL) AS linked,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE v.product_id IS NOT NULL)
                         / NULLIF(COUNT(*),0), 2) AS linked_pct,
                   COUNT(*) FILTER (WHERE v.sentiment_label = 'negative') AS negative,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE v.sentiment_label = 'negative')
                         / NULLIF(COUNT(*),0), 2) AS neg_pct
            FROM voc_records v JOIN platforms p ON p.id = v.platform_id
            GROUP BY p.code
            HAVING COUNT(*) >= 300
            ORDER BY voc DESC
        """)

        # 6) 전체 요약
        summary_row = (await db.execute(text("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE product_id IS NOT NULL) AS linked,
                   COUNT(*) FILTER (WHERE sentiment_label = 'negative') AS neg,
                   COUNT(*) FILTER (WHERE content_hash IS NOT NULL) AS hashed,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE product_id IS NOT NULL)
                         / NULLIF(COUNT(*),0), 2) AS linked_pct
            FROM voc_records
        """))).mappings().one()

    await engine.dispose()

    # R13 baseline (메모리 / R14 보고 기록) — 비교용 상수
    r13_baseline = {"total": 168112, "linked_pct": 20.6}
    delta_total = int(summary_row["total"]) - r13_baseline["total"]

    summary = {
        "current": dict(summary_row),
        "r13_baseline": r13_baseline,
        "delta_total": delta_total,
        "delta_total_pct": round(100.0 * delta_total / r13_baseline["total"], 2),
        "delta_linked_pp": round(
            float(summary_row["linked_pct"]) - r13_baseline["linked_pct"], 2
        ),
    }

    return {
        "hash_group_distribution": hash_dist,
        "cross_site_dup": cross_site,
        "no_hash_by_site": no_hash,
        "short_content_dup_potential": short_dup,
        "analytic_quality_by_site": quality,
        "summary": summary,
    }


def main() -> int:
    result = asyncio.run(analyze())
    # decimal/datetime safe dump
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
