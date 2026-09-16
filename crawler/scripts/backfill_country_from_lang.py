# 언어가 국가를 사실상 특정하는 경우에만 country_code 를 소급해 채운다.
"""backfill_country_from_lang — 국가 미상 행의 보수적 추론.

배경:
  country_code 가 비어 있는 행이 155,214건으로 **최대 항목**이다(90일 기준
  US 13.5만·KR 9만보다 많다). 지역별 결함 비교가 절반만 보이는 상태다.
  대부분 youtube(124,709건 전부 NULL) 에서 온다 — 전 세계 대상이라 플랫폼으로
  국가를 정할 수 없기 때문이다.

왜 언어만 쓰고, 왜 일부만 쓰는가:
  언어→국가가 1:1 인 경우에만 쓴다. 영어 72,346건은 어느 나라인지 알 수 없고,
  스페인어·포르투갈어·아랍어도 여러 나라에 걸친다. 억지로 정하면 지역 분석이
  오히려 틀어진다.

  **라틴 문자 언어는 제외한다.** 짧은 텍스트 언어 오탐이 심하기 때문이다 —
  실측(2026-09-16) da 로 잡힌 것이 "Still using fold 1"(영어),
  no 가 "Awesome video, killer intro."(영어), nl 이 "pixel is gem."(영어)였다.
  비라틴 문자(한글·가나·태국·키릴 등)는 문자 자체가 증거라 오탐이 거의 없다.

  그래서 한국어 22,552 · 일본어 1,903 · 태국어 1,287 · 베트남어 804 등
  **약 27,000건**만 대상이다. 37,351건 중 라틴 문자 1만여 건은 버린다 —
  절반을 안전하게 채우는 것이 전부를 의심스럽게 채우는 것보다 낫다.

env:
  COUNTRY_BATCH   한 번에 처리할 행 수 (기본 5000)
  COUNTRY_LIMIT   총 상한 (0=무제한)
  DRY_RUN=1       갱신하지 않고 건수만 센다
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("country_backfill")

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("COUNTRY_BATCH", "5000"))
LIMIT = int(os.getenv("COUNTRY_LIMIT", "0"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# **비라틴 문자 언어만.** 문자 자체가 증거라 짧은 글에서도 오탐이 거의 없다.
# 라틴 문자 언어(no/da/nl/sv/ro…)는 영어를 오탐하는 일이 잦아 제외했다.
LANG_TO_COUNTRY = {
    "ko": "KR",   # 한글
    "ja": "JP",   # 가나
    "th": "TH",   # 타이 문자
    "vi": "VN",   # 성조 부호가 붙은 라틴 — 사실상 베트남 전용
    "el": "GR",   # 그리스 문자
    "he": "IL",   # 히브리 문자
    "fa": "IR",   # 아랍 문자(페르시아)
    "uk": "UA",   # 키릴(우크라이나 고유 문자 ї/є/ґ)
    "bg": "BG",   # 키릴(불가리아)
    "hi": "IN",   # 데바나가리
    "ta": "IN",   # 타밀
    "bn": "BD",   # 벵골
}

# 언어가 여러 나라에 걸쳐 **추론하면 안 되는** 것들. 문서화 목적으로 남긴다.
AMBIGUOUS = {
    "en": "영어권 다수 — 추론 불가",
    "es": "스페인·중남미 다수",
    "pt": "브라질·포르투갈",
    "ar": "아랍권 다수",
    "de": "독일·오스트리아·스위스",
    "fr": "프랑스·캐나다·아프리카 다수",
    "ru": "러시아·CIS 다수",
    "zh": "중국·대만·싱가포르",
}

SELECT_SQL = text("""
    SELECT id, language_detected
    FROM voc_records
    WHERE archived_at IS NULL
      AND country_code IS NULL
      AND language_detected = ANY(:langs)
      AND id > :after
    ORDER BY id
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records SET country_code = :c
    WHERE id = ANY(:ids) AND country_code IS NULL
""")


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return 2

    langs = list(LANG_TO_COUNTRY)
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    after, seen, fixed = 0, 0, 0
    by_country: dict = {}

    log.info("국가 추론 백필 — 대상 언어 %d종%s (모호한 언어 %d종은 제외)",
             len(langs), " [DRY_RUN]" if DRY_RUN else "", len(AMBIGUOUS))
    try:
        while True:
            async with engine.begin() as conn:
                rows = (await conn.execute(
                    SELECT_SQL,
                    {"langs": langs, "after": after, "batch": BATCH})).all()
                if not rows:
                    break
                after = rows[-1].id
                seen += len(rows)

                # 국가별로 묶어 한 번에 갱신 — 행 단위 UPDATE 는 느리다
                groups: dict = {}
                for r in rows:
                    c = LANG_TO_COUNTRY.get(r.language_detected)
                    if c:
                        groups.setdefault(c, []).append(r.id)
                if not DRY_RUN:
                    for c, ids in groups.items():
                        res = await conn.execute(UPDATE_SQL, {"c": c, "ids": ids})
                        n = res.rowcount or 0
                        fixed += n
                        by_country[c] = by_country.get(c, 0) + n
                else:
                    for c, ids in groups.items():
                        by_country[c] = by_country.get(c, 0) + len(ids)
                        fixed += len(ids)

            if LIMIT and seen >= LIMIT:
                break
    finally:
        await engine.dispose()

    top = sorted(by_country.items(), key=lambda x: -x[1])[:8]
    log.info("완료 — 스캔 %d / 채움 %d%s", seen, fixed,
             " (DRY_RUN)" if DRY_RUN else "")
    log.info("  국가별: %s", ", ".join(f"{c} {n}" for c, n in top))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
