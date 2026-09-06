# 기존 VOC 코퍼스에서 (부품·증상·심각도) 결함 레코드를 추출해 voc_defects 에 적재
"""
voc_defects 백필.

부정/결함 신호가 있는 글에서 구조화 결함을 뽑는다. 단어 카운트("hinge 248")를
"(hinge, dust_ingress, degraded)" 레코드로 바꾸는 것이 목적.

- 대상: archived_at IS NULL. 번역본 우선(COALESCE(content_translated, content_original))
  — 렉시콘이 영어 중심이고 한국어 패턴도 함께 두어 번역 실패 행도 잡는다.
- keyset 커서(id > last)로 배치 처리. 멱등(ON CONFLICT DO NOTHING).

실행: DATABASE_URL=... python3 -m scripts.backfill_defects
환경변수: DEFECT_BATCH(기본 2000), DEFECT_LIMIT(0=무제한)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)

from nlp.defect_extract import extract_defects  # noqa: E402
from nlp.modality import classify as classify_modality  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_defects")

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("DEFECT_BATCH", "2000"))
LIMIT = int(os.getenv("DEFECT_LIMIT", "0"))

SELECT_SQL = text("""
    SELECT v.id, COALESCE(v.content_translated, v.content_original) AS body,
           pl.kind AS platform_kind
    FROM voc_records v LEFT JOIN platforms pl ON pl.id = v.platform_id
    WHERE v.archived_at IS NULL AND v.content_original IS NOT NULL AND v.id > :after
    ORDER BY v.id
    LIMIT :batch
""")

INSERT_SQL = text("""
    INSERT INTO voc_defects (voc_id, component, symptom, severity, modality)
    VALUES (:v, :c, :s, :sev, :mod)
    ON CONFLICT (voc_id, component, symptom) DO UPDATE SET modality = EXCLUDED.modality
""")


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        total = (await db.execute(text(
            "SELECT count(*) FROM voc_records WHERE archived_at IS NULL"
        ))).scalar_one()
    log.info(f"결함 추출 대상 {total}건 (BATCH={BATCH})")

    after = seen = written = rows_with_defect = 0
    try:
        while True:
            async with Session() as db:
                rows = (await db.execute(
                    SELECT_SQL, {"after": after, "batch": BATCH})).all()
                if not rows:
                    break
                for r in rows:
                    seen += 1
                    after = r.id
                    defects = extract_defects(r.body)
                    if defects:
                        rows_with_defect += 1
                    # 양상은 문서 단위 — 결함이 있을 때만 1회 계산해 각 행에 붙인다
                    mod = (classify_modality(r.body, r.platform_kind)["label"]
                           if defects else None)
                    for comp, symp, sev in defects:
                        await db.execute(INSERT_SQL,
                                         {"v": r.id, "c": comp, "s": symp,
                                          "sev": sev, "mod": mod})
                        written += 1
                await db.commit()
            if seen % 50000 < BATCH:
                log.info(f"  진행 {seen}/{total} — 결함 {written}, 결함보유 행 {rows_with_defect}")
            if LIMIT and seen >= LIMIT:
                break
    finally:
        await engine.dispose()
    log.info(f"=== 완료: 스캔 {seen}, 결함 {written}, 결함보유 행 {rows_with_defect} ===")


if __name__ == "__main__":
    asyncio.run(main())
