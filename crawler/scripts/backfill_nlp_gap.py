# NLP 파이프라인을 건너뛴 행(language_detected NULL)에 언어·감성·카테고리를 소급 적용
"""
NLP 결측 백필.

2026-06 HN 연도 백필 배치 64,920행이 NLP 파이프라인을 통째로 건너뛰었다.
같은 플랫폼 안에서 비교하면 격차가 분명하다 (실측 2026-09-11) —

    language_detected 있음  66,360행 : sentiment 100.0% · categories 54.1%
    language_detected NULL  64,920행 : sentiment  49.5% · categories  0.6%

**전역 categories 결측 40.5% 는 대부분 정상이다.** 건강한 행도 토픽이 없으면
빈 categories 를 갖는다(최근 수집분 실측 62~88%). 비정상인 것은 이 0.6% 구간뿐이라
대상을 `language_detected IS NULL` 로 좁힌다.

`processed_at` 은 백로그 지표로 쓸 수 없다 — 전체 458,240행 중 9,690행(2.1%)에만
값이 있어 결측이 미처리를 뜻하지 않는다.

- 대상: archived_at IS NULL AND language_detected IS NULL AND content_original NOT NULL
- keyset 커서(id > last). 배치마다 커밋하므로 중단해도 진행분이 남는다.
- 번역은 하지 않는다. 이 구간은 영어 HN 이고, 비영어 행은 language_detected 가
  채워진 뒤 기존 translate_backlog 가 가져간다(역할 분리).

실행: DATABASE_URL=... python3 -m scripts.backfill_nlp_gap [--apply]
환경변수: NLP_GAP_BATCH(기본 2000), NLP_GAP_LIMIT(0=무제한)
"""
import asyncio
import logging
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)

from nlp.detector import detect_language  # noqa: E402
from nlp.sentiment import analyze_sentiment  # noqa: E402
from nlp.categorizer import classify_categories  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("nlp_gap")

# 짧은 라틴문자 텍스트의 언어 감지는 믿을 수 없다. 실측(8,000행) —
# 비영어로 감지된 178건이 **전부** 120자 미만 영어 HN 제목이었다
# ('Samsung Galaxy S24 Plus'→tl, 'Astronomers spot exoplanet'→fr, 'Samsung
# unveils $799 Galaxy S21'→so). 긴 비영어는 0건.
# 그대로 쓰면 translate_backlog 가 영어를 소말리어로 번역하려 든다 —
# 2026-08 에 16일간 큐를 막았던 언어 오탐과 같은 부류다.
#
# 그래서 **비라틴 문자(한글·CJK·키릴·아랍·태국·그리스·히브리·데바나가리)가
# 없는 짧은 텍스트**는 감지 결과와 무관하게 영어로 본다. 비라틴 문자가 있으면
# 감지를 그대로 신뢰한다(한국어 짧은 글은 보호된다).
# 비용: 짧은 불어/독어가 영어로 기록된다. 이 배치는 HN 영어라 실측 오류 0이고,
# 영어를 번역 큐에 넣는 쪽이 훨씬 해롭다.
# 임계는 전수 실측으로 정했다 — 라틴문자 비영어 감지 890건 중 834건(93.7%)이
# 80자 이하이고(p50 35 · p75 52 · p90 70), 80자 초과는 56건뿐이다. 긴 글은
# 감지가 신뢰할 만하므로 그대로 둔다(105자 프랑스어가 영어로 뒤집히던 문제).
_SHORT_LEN = 80
_NON_LATIN_RANGES = (
    (0x0370, 0x03FF),  # 그리스
    (0x0400, 0x04FF),  # 키릴
    (0x0590, 0x05FF),  # 히브리
    (0x0600, 0x06FF),  # 아랍
    (0x0900, 0x097F),  # 데바나가리
    (0x0E00, 0x0E7F),  # 태국
    (0x1100, 0x11FF),  # 한글 자모
    (0x3040, 0x30FF),  # 가나
    (0x3130, 0x318F),  # 한글 호환 자모
    (0x4E00, 0x9FFF),  # CJK 한자
    (0xAC00, 0xD7A3),  # 한글 음절
)


def _has_non_latin_script(t: str) -> bool:
    return any(lo <= ord(c) <= hi for c in t for lo, hi in _NON_LATIN_RANGES)


def _safe_lang(t: str) -> str:
    lang = detect_language(t)
    if lang and lang != "en" and len(t) < _SHORT_LEN and not _has_non_latin_script(t):
        return "en"
    return lang

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("NLP_GAP_BATCH", "2000"))
LIMIT = int(os.getenv("NLP_GAP_LIMIT", "0"))
APPLY = "--apply" in sys.argv

SELECT_SQL = text("""
    SELECT id, content_original
    FROM voc_records
    WHERE archived_at IS NULL AND language_detected IS NULL
      AND content_original IS NOT NULL AND id > :after
    ORDER BY id
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records
       SET language_detected = :lang,
           content_translated = COALESCE(content_translated, :translated),
           sentiment_score = :score,
           sentiment_label = :label,
           categories = :cats,
           processed_at = now()
     WHERE id = :id
""")


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        total = (await db.execute(text(
            "SELECT count(*) FROM voc_records WHERE archived_at IS NULL "
            "AND language_detected IS NULL AND content_original IS NOT NULL"
        ))).scalar_one()
    log.info(f"NLP 결측 백필 대상 {total}건 (apply={APPLY}, BATCH={BATCH})")

    after = seen = updated = 0
    langs: Counter = Counter()
    with_cat = 0
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
                    lang = _safe_lang(r.content_original)
                    langs[lang or "?"] += 1
                    # 비영어는 언어만 채운다 — 번역은 translate_backlog 의 몫이다.
                    translated = r.content_original if lang == "en" else None
                    basis = translated or r.content_original
                    score, label = analyze_sentiment(basis)
                    cats = classify_categories(basis) or []
                    if cats:
                        with_cat += 1
                    if not APPLY:
                        continue
                    await db.execute(UPDATE_SQL, {
                        "id": r.id, "lang": lang, "translated": translated,
                        "score": score, "label": label, "cats": cats,
                    })
                    updated += 1
                if APPLY:
                    await db.commit()
            if seen % 20000 < BATCH:
                log.info(f"  진행 {seen}/{total} — 갱신 {updated}, "
                         f"카테고리 부여 {with_cat}")
            if LIMIT and seen >= LIMIT:
                break
    finally:
        await engine.dispose()

    pct = 100.0 * with_cat / seen if seen else 0.0
    log.info(f"=== 완료: 스캔 {seen}, 갱신 {updated}, "
             f"카테고리 부여 {with_cat} ({pct:.1f}%) (apply={APPLY}) ===")
    log.info(f"    언어 분포: {langs.most_common(8)}")


if __name__ == "__main__":
    asyncio.run(main())
