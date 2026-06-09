"""
데이터 정리(reprocess) — 번역 실패로 품질이 망가진 기존 행 복구.

대상: 비영어인데 영어 번역이 안 된 행
      (content_translated IS NULL 또는 content_translated = content_original).
처리: 재번역 → 번역 성공 시 sentiment/categories 재계산 → UPDATE.
      (미번역 한국어에 영어 전용 VADER를 돌려 neutral 로 잘못 채워진 값 교정)

번역 모듈이 자체적으로 동시성/레이트리밋/백오프를 처리하므로
대량 행도 안전하게 순차 배치 복구한다.

환경변수:
  DATABASE_URL          (필수)
  REPROCESS_LIMIT       총 처리 상한 (기본 0 = 무제한)
  REPROCESS_BATCH       배치 크기 (기본 50)
실행: DATABASE_URL=... ../.venv/bin/python -m nlp.reprocess
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from nlp.detector import detect_language
from nlp.translator import translate_to_english, SKIP_LANGS
from nlp.sentiment import analyze_sentiment, analyze
from nlp.categorizer import classify_categories

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("reprocess")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("REPROCESS_LIMIT", "0"))
BATCH = int(os.getenv("REPROCESS_BATCH", "50"))

SELECT_SQL = text("""
    SELECT id, content_original, language_detected
    FROM voc_records
    WHERE language_detected IS NOT NULL
      AND language_detected NOT IN ('en', 'und')
      AND (content_translated IS NULL OR content_translated = content_original)
    ORDER BY id
    LIMIT :batch OFFSET :offset
""")

UPDATE_SQL = text("""
    UPDATE voc_records
    SET content_translated = :tr,
        sentiment_score    = :ss,
        sentiment_label    = :sl,
        categories         = :cats,
        processed_at       = NOW()
    WHERE id = :id
""")


# 한국어 감성 일괄 교정 (오프라인·레이트리밋 무관) — 원문에서 직접 산출
KO_SENT_SELECT = text("""
    SELECT id, content_original
    FROM voc_records
    WHERE language_detected = 'ko' AND content_original IS NOT NULL
    ORDER BY id LIMIT :batch OFFSET :offset
""")
KO_SENT_UPDATE = text("""
    UPDATE voc_records SET sentiment_score = :ss, sentiment_label = :sl
    WHERE id = :id
""")


async def fix_korean_sentiment(Session) -> int:
    """전체 한국어 행의 sentiment 를 원문 기반 한국어 사전으로 재계산.

    번역 의존이 없어 즉시·전량 처리 가능. 미번역으로 false-neutral 이던
    수천 행의 감성을 의미 있는 값으로 교정한다.
    """
    async with Session() as db:
        total = (await db.execute(text(
            "SELECT count(*) FROM voc_records WHERE language_detected='ko'"
        ))).scalar_one()
    log.info(f"[Phase A] 한국어 감성 재계산 대상: {total}건 (오프라인)")

    fixed = 0
    offset = 0
    while True:
        async with Session() as db:
            rows = (await db.execute(KO_SENT_SELECT, {"batch": 500, "offset": offset})).all()
            if not rows:
                break
            for r in rows:
                score, label = analyze(r.content_original, lang="ko")
                await db.execute(KO_SENT_UPDATE, {"id": r.id, "ss": score, "sl": label})
                fixed += 1
            await db.commit()
        offset += 500
        if offset % 5000 == 0:
            log.info(f"  [Phase A] {fixed}건 교정")
    log.info(f"=== [Phase A] 한국어 감성 교정 완료: {fixed}건 ===")
    return fixed


async def retag_products(Session) -> int:
    """Phase C — 신규 제품 패턴(구세대 + 경쟁사)을 기존 행에 재적용.

    product_id 가 NULL 인 행만 대상으로 infer_product_code 재실행 → 매치되면
    products.code → id 매핑으로 product_id 채움. 이미 태깅된 행은 건드리지 않음
    (BaseCrawler 가 처음 태깅한 결과를 존중).
    """
    from base.product_match import infer_product_code

    async with Session() as db:
        prows = (await db.execute(text("SELECT code, id FROM products"))).all()
        pmap = {r.code.upper(): r.id for r in prows}
        total = (await db.execute(text(
            "SELECT count(*) FROM voc_records WHERE product_id IS NULL"
        ))).scalar_one()
    log.info(f"[Phase C] 제품 재태깅 대상(미태깅): {total}건 (사전 {len(pmap)}종)")

    fixed = 0
    offset = 0
    BATCH = 2000
    while True:
        async with Session() as db:
            rows = (await db.execute(text("""
                SELECT id, content_original FROM voc_records
                WHERE product_id IS NULL AND content_original IS NOT NULL
                ORDER BY id LIMIT :b OFFSET :o
            """), {"b": BATCH, "o": offset})).all()
            if not rows:
                break
            ups = []
            for r in rows:
                code = infer_product_code(r.content_original)
                if not code:
                    continue
                pid = pmap.get(code.upper())
                if pid:
                    ups.append({"id": r.id, "pid": pid})
            if ups:
                await db.execute(text(
                    "UPDATE voc_records SET product_id = :pid WHERE id = :id"
                ), ups)
                fixed += len(ups)
                await db.commit()
        offset += BATCH
        if offset % 10000 == 0:
            log.info(f"  [Phase C] 진행 offset={offset} 누적 태깅 {fixed}")
    log.info(f"=== [Phase C] 제품 재태깅 완료: {fixed}건 ===")
    return fixed


async def _reprocess_row(db: AsyncSession, row) -> bool:
    rid, original, lang = row.id, row.content_original, row.language_detected
    if not original or lang in SKIP_LANGS:
        return False

    translated = await translate_to_english(original, source_lang=lang)
    # 번역이 여전히 실패(원문 그대로)면 이번엔 건너뜀 — 다음 실행에서 재시도
    if not translated or translated == original:
        return False

    # 감성: 한국어는 원문 기반(번역 품질 무관), 그 외는 번역본
    if lang == "ko":
        score, label = analyze(original, lang="ko")
    else:
        score, label = analyze_sentiment(translated)
    cats = classify_categories(translated)
    await db.execute(UPDATE_SQL, {
        "id": rid, "tr": translated, "ss": score, "sl": label, "cats": cats,
    })
    return True


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Phase A: 한국어 감성 전량 교정 (오프라인·즉시, 번역 무관)
    await fix_korean_sentiment(Session)

    # Phase C: product 재태깅 — 신규 추가 제품(구세대/경쟁사)을 기존 행에 적용
    await retag_products(Session)

    # Phase B: 미번역 비영어 행 번역 백필 (느림·레이트리밋, content_translated/categories 보강)
    async with Session() as db:
        total = (await db.execute(text("""
            SELECT count(*) FROM voc_records
            WHERE language_detected IS NOT NULL
              AND language_detected NOT IN ('en','und')
              AND (content_translated IS NULL OR content_translated = content_original)
        """))).scalar_one()
    log.info(f"재처리 대상: {total}건 (LIMIT={LIMIT or '무제한'}, BATCH={BATCH})")

    fixed = skipped = seen = 0
    offset = 0
    while True:
        async with Session() as db:
            rows = (await db.execute(SELECT_SQL, {"batch": BATCH, "offset": offset})).all()
            if not rows:
                break
            for r in rows:
                seen += 1
                try:
                    if await _reprocess_row(db, r):
                        fixed += 1
                    else:
                        skipped += 1
                except Exception as e:
                    skipped += 1
                    log.warning(f"행 {r.id} 재처리 실패: {e}")
            await db.commit()

        log.info(f"  진행 {seen}건 (복구 {fixed}, 건너뜀 {skipped})")
        # 번역 실패로 건너뛴 행은 그대로 남으므로 offset 을 전진시켜 무한루프 방지
        offset += BATCH
        if LIMIT and seen >= LIMIT:
            log.info(f"LIMIT {LIMIT} 도달 — 종료")
            break

    await engine.dispose()
    log.info(f"=== 재처리 완료: 복구 {fixed} / 시도 {seen} ===")


if __name__ == "__main__":
    asyncio.run(main())
