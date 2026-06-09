"""voc_records → voc_keywords 키워드 ingest 파이프라인.

처리 흐름:
- voc_keywords 에 아직 없는 voc_records 중 N건 선택
- 본문(content_original)에서 키워드 추출 (extractor.extract)
- voc_keywords 일괄 INSERT
- 실패행은 skip + 로그 (전체 중단 X)

환경변수:
- DATABASE_URL (필수)
- KW_INGEST_BATCH (기본 1000) — 한 사이클 처리 행수
- KW_INGEST_TOP_N (기본 20) — VOC 1건당 키워드 갯수 cap

사용:
- 단독 실행: python -m keywords.ingest
- Celery task: tasks.ingest_keywords
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

from keywords.extractor import extract


logger = logging.getLogger(__name__)


def _resolve_dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://"):]
    elif url.startswith("postgres+asyncpg://"):
        url = "postgres://" + url[len("postgres+asyncpg://"):]
    if url:
        return url
    return (
        f"postgresql://{os.getenv('POSTGRES_USER','signalforge')}:"
        f"{os.getenv('POSTGRES_PASSWORD','signalforge_pass')}@"
        f"{os.getenv('POSTGRES_HOST','127.0.0.1')}:"
        f"{os.getenv('POSTGRES_PORT','5434')}/"
        f"{os.getenv('POSTGRES_DB','signalforge')}"
    )


SELECT_BATCH = """
    SELECT v.id, v.content_original, v.language_detected
    FROM voc_records v
    LEFT JOIN voc_keywords k ON k.voc_id = v.id
    WHERE v.content_original IS NOT NULL
      AND length(v.content_original) >= 10
      AND k.id IS NULL
    ORDER BY v.id DESC
    LIMIT $1
"""

INSERT_ROWS = """
    INSERT INTO voc_keywords (voc_id, keyword, lang, weight)
    SELECT * FROM unnest($1::bigint[], $2::text[], $3::text[], $4::float[])
"""


async def ingest(batch: int = 1000, top_n: int = 20) -> int:
    """한 번에 batch 행 처리, INSERT한 keyword 행수 반환."""
    dsn = _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SELECT_BATCH, batch)
        if not rows:
            logger.info("ingest: 처리 대상 0건")
            return 0

        voc_ids: List[int] = []
        keywords: List[str] = []
        langs: List[str] = []
        weights: List[float] = []

        for r in rows:
            vid = r["id"]
            text = r["content_original"]
            lang = r["language_detected"] or "auto"
            try:
                pairs = extract(text, lang=lang, top_n=top_n)
            except Exception as e:
                logger.warning("voc %s 추출 실패: %s", vid, e)
                continue
            for kw, w in pairs:
                voc_ids.append(vid)
                keywords.append(kw[:200])  # 200자 cap
                langs.append(lang[:10])
                weights.append(float(w))

        if not voc_ids:
            logger.info("ingest: 추출된 키워드 0건 (대상 %d 건)", len(rows))
            return 0

        await conn.execute(INSERT_ROWS, voc_ids, keywords, langs, weights)
        logger.info(
            "ingest: %d 건 voc 처리 → %d 키워드 행 INSERT",
            len(rows), len(voc_ids),
        )
        return len(voc_ids)
    finally:
        await conn.close()


async def _main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    batch = int(os.getenv("KW_INGEST_BATCH", "1000"))
    top_n = int(os.getenv("KW_INGEST_TOP_N", "20"))
    n = await ingest(batch=batch, top_n=top_n)
    print(f"OK: {n} keyword rows inserted")


if __name__ == "__main__":
    asyncio.run(_main())
