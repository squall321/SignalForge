"""voc_records.categories 백필 — categorizer v2 (한국어 인포멀 + Galaxy 정규식 + others).

대상: categories IS NULL OR array_length=NULL 인 행 (collected_at 최근 우선).
입력: content_translated COALESCE content_original.
옵션: allow_others=True 로 미분류 + 충분한 길이 → ['others'].

환경변수:
  DATABASE_URL          (필수, postgresql+asyncpg://… )
  BACKFILL_LIMIT        총 처리 상한 (기본 50000, 0=무제한)
  BACKFILL_BATCH        배치 크기 (기본 1000)
  BACKFILL_ALLOW_OTHERS '1'/'0' (기본 '1')

실행:
  DATABASE_URL=postgresql+asyncpg://... \
    /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.backfill_categories
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402

from nlp.categorizer import classify_categories  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_categories")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("BACKFILL_LIMIT", "50000"))
BATCH = int(os.getenv("BACKFILL_BATCH", "1000"))
ALLOW_OTHERS = os.getenv("BACKFILL_ALLOW_OTHERS", "1") == "1"

SELECT_SQL = text("""
    SELECT id, content_translated, content_original
    FROM voc_records
    WHERE (categories IS NULL OR array_length(categories, 1) IS NULL)
      AND content_original IS NOT NULL
      AND id < :cursor
    ORDER BY id DESC
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records
    SET categories = :cats,
        processed_at = NOW()
    WHERE id = :id
""")


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        total = (await db.execute(text("""
            SELECT count(*) FROM voc_records
            WHERE (categories IS NULL OR array_length(categories,1) IS NULL)
              AND content_original IS NOT NULL
        """))).scalar_one()
    log.info(
        f"백필 대상: {total:,}건 (LIMIT={LIMIT or '무제한'}, BATCH={BATCH}, "
        f"allow_others={ALLOW_OTHERS})"
    )

    seen = matched = others = 0
    cursor = 1 << 62  # id 내림차순 키셋 페이지네이션
    while True:
        async with Session() as db:
            rows = (await db.execute(SELECT_SQL, {"batch": BATCH, "cursor": cursor})).all()
            if not rows:
                log.info("  더 이상 처리할 NULL 행 없음 — 종료")
                break

            ups = []
            for r in rows:
                seen += 1
                txt = r.content_translated or r.content_original or ""
                cats = classify_categories(txt, allow_others=ALLOW_OTHERS)
                if not cats:
                    continue
                ups.append({"id": r.id, "cats": cats})
                if cats == ["others"]:
                    others += 1
                else:
                    matched += 1

            if ups:
                await db.execute(UPDATE_SQL, ups)
                await db.commit()

            # 키셋 전진 — 이번 배치의 가장 작은 id 다음부터
            cursor = rows[-1].id

            log.info(
                f"  진행 누적 {seen:,} / 매치 {matched:,} / others {others:,} "
                f"(이번 배치 UPDATE={len(ups)}, cursor={cursor})"
            )

        if LIMIT and seen >= LIMIT:
            log.info(f"LIMIT {LIMIT:,} 도달 — 종료")
            break

    await engine.dispose()
    hit_pct = (matched + others) * 100.0 / max(seen, 1)
    log.info(
        f"=== 백필 완료: 시도 {seen:,} / 매치 {matched:,} / others {others:,} "
        f"/ hit {hit_pct:.2f}% ==="
    )


if __name__ == "__main__":
    asyncio.run(main())
