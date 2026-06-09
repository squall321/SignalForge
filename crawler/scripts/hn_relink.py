"""HN/영문 포럼 전용 product 재매칭 — R8 Track D.

배경:
  - relink_products.py 의 일반 매칭으로 HN linked 0.3% → 18.92% 까지 회복.
  - 그러나 영문 HN 본문에는 여전히 "Galaxy phone" / "Samsung Galaxy" 같은
    *제품군 통칭* 만 등장하거나, "S10E" / "Z Fold 4" / "Note 20 Ultra" 같은
    영문 약어가 일반 사전 substring 매칭에 잡히지 않는 경우가 있다.
  - 또한 HN 영문은 노이즈가 많다 — "galaxy" 가 우주/Hitchhiker's Guide/
    GOG Galaxy 런처 등으로 자주 등장 → Samsung/Galaxy 컨텍스트 *엄격* 요구.

설계:
  1. 갤럭시 컨텍스트 *필수* — "samsung galaxy" 또는 "samsung's galaxy" 같이
     samsung 어휘가 같은 문장에 등장해야 매칭 시도. 단순 "galaxy" 만은 거부.
     (HN 한정 — 일반 relink_products.py 보다 보수적.)
  2. ENG_REGEX_PATTERNS — 약어/연차 조합:
        * "S10E" / "S10e" → GS10E
        * "Z Fold 4" / "Z Fold4" → GZF4
        * "Z Flip 5" → GZFL5
        * "Note 20 Ultra" / "Note20 Ultra" → GN20U
        * "Tab S9 FE" → GTABS9F
        * "Watch5 Pro" → GW5P
  3. 차단 패턴 — "milky way", "hitchhiker", "gog galaxy", "galaxy brain",
     "galaxy energy" 같은 우주/은유 표현에 등장하면 매칭 거부.

환경변수:
  DATABASE_URL              필수
  HN_RELINK_LIMIT           기본 200000
  HN_RELINK_BATCH           기본 5000
  HN_RELINK_DRY_RUN         '1' = UPDATE 안 함
  HN_RELINK_PLATFORM_NAME   기본 'Hacker News' (확장 시 'Reddit' 등)

실행:
  DATABASE_URL=... /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.hn_relink
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("hn_relink")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("HN_RELINK_LIMIT", "200000"))
BATCH = int(os.getenv("HN_RELINK_BATCH", "5000"))
DRY_RUN = os.getenv("HN_RELINK_DRY_RUN", "0") == "1"
PLATFORM_NAME = os.getenv("HN_RELINK_PLATFORM_NAME", "Hacker News")


# ─────────────────────────────────────────────────────────────────────────
# 1) HN 컨텍스트 필수 — "samsung" 어휘가 본문에 있어야 매칭 시도.
#    "galaxy" 단독은 우주/은유로 빈번하게 등장하므로 차단.
# ─────────────────────────────────────────────────────────────────────────
_SAMSUNG_CTX_RE = re.compile(
    r"\bsamsung'?s?\b|\bs\s*pen\b|\bone\s*ui\b|\bbixby\b",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────
# 2) HN 차단 패턴 — "galaxy" 가 우주/은유로 등장하는 잘 알려진 표현.
#    이 표현이 본문에 *하나라도* 있으면 그 본문은 매칭 시도하지 않는다.
# ─────────────────────────────────────────────────────────────────────────
HN_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bmilky\s+way\b", re.IGNORECASE),
    re.compile(r"\bhitchhiker'?s?\s+guide\b", re.IGNORECASE),
    re.compile(r"\bgog\s+galaxy\b", re.IGNORECASE),
    re.compile(r"\bgalaxy[-\s]+brain(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\bgalaxy\s+energy\b", re.IGNORECASE),
    re.compile(r"\bgalaxy\s+(?:cluster|formation|merger|core|center)\b", re.IGNORECASE),
    re.compile(r"\bdwarf\s+galaxy\b|\bandromeda\s+galaxy\b", re.IGNORECASE),
    re.compile(r"\bcross(?:ing)?\s+the\s+galaxy\b", re.IGNORECASE),
    re.compile(r"\bfar(?:\s|,)\s*far\s+away\s+galaxy\b", re.IGNORECASE),
]


def _is_blocked(text_lower: str) -> bool:
    """HN_NOISE_PATTERNS 중 하나라도 매칭되면 True."""
    return any(p.search(text_lower) for p in HN_NOISE_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────
# 3) HN/영문 강화 regex — 약어, 변형 모음
#    relink_products.py 의 기본 패턴과 중복되어도 무해. 여기는 *영문 한정* 표현
#    위주로 짧고 정확한 규칙을 추가한다.
# ─────────────────────────────────────────────────────────────────────────
ENG_REGEX_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── Tab S 영문 약어 (existing 175 product 한정) ──────────────────
    # ⚠️ Tab S 패턴은 *반드시* 일반 S 패턴보다 먼저 매칭되어야 한다.
    # "Tab S9 FE" 에서 'S9' 단독 매칭이 우선되면 안 됨.
    (re.compile(r"\btab\s*s\s*11\s*ultra\b", re.IGNORECASE), "GTABS11U"),
    (re.compile(r"\btab\s*s\s*11\b", re.IGNORECASE), "GTABS11"),
    (re.compile(r"\btab\s*s\s*10\s*ultra\b", re.IGNORECASE), "GTABS10U"),
    (re.compile(r"\btab\s*s\s*10\s*(?:plus|\+)", re.IGNORECASE), "GTABS10P"),
    (re.compile(r"\btab\s*s\s*10\s*fe\b", re.IGNORECASE), "GTABS10F"),
    (re.compile(r"\btab\s*s\s*10\b", re.IGNORECASE), "GTABS10"),
    (re.compile(r"\btab\s*s\s*9\s*ultra\b", re.IGNORECASE), "GTABS9U"),
    (re.compile(r"\btab\s*s\s*9\s*(?:plus|\+)", re.IGNORECASE), "GTABS9P"),
    (re.compile(r"\btab\s*s\s*9\s*fe\b", re.IGNORECASE), "GTABS9F"),
    (re.compile(r"\btab\s*s\s*9\b", re.IGNORECASE), "GTABS9"),
    (re.compile(r"\btab\s*s\s*8\s*ultra\b", re.IGNORECASE), "GTABS8U"),
    (re.compile(r"\btab\s*s\s*8\s*(?:plus|\+)", re.IGNORECASE), "GTABS8P"),
    (re.compile(r"\btab\s*s\s*8\b", re.IGNORECASE), "GTABS8"),
    (re.compile(r"\btab\s*s\s*7\s*(?:plus|\+)", re.IGNORECASE), "GTABS7P"),
    (re.compile(r"\btab\s*s\s*7\b", re.IGNORECASE), "GTABS7"),
    (re.compile(r"\btab\s*s\s*6\b", re.IGNORECASE), "GTABS6"),

    # ── Tab A / Tab Active5 ─────────────────────────────────────────
    (re.compile(r"\btab\s*a\s*11\b", re.IGNORECASE), "GTABA11"),
    (re.compile(r"\btab\s*a\s*9\s*(?:plus|\+)", re.IGNORECASE), "GTABA9P"),
    (re.compile(r"\btab\s*a\s*9\b", re.IGNORECASE), "GTABA9"),
    (re.compile(r"\btab\s*a\s*8\b", re.IGNORECASE), "GTABA8"),
    (re.compile(r"\btab\s*active\s*5(?:\s*pro)?\b", re.IGNORECASE), "GTABACT5"),

    # ── S 시리즈 영문 약어 ───────────────────────────────────────────
    (re.compile(r"\bs\s*25\s*ultra\b", re.IGNORECASE), "GS25U"),
    (re.compile(r"\bs\s*25\s*(?:plus|\+)", re.IGNORECASE), "GS25P"),
    (re.compile(r"\bs\s*25\b", re.IGNORECASE), "GS25"),
    (re.compile(r"\bs\s*24\s*ultra\b", re.IGNORECASE), "GS24U"),
    (re.compile(r"\bs\s*24\s*(?:plus|\+)", re.IGNORECASE), "GS24P"),
    (re.compile(r"\bs\s*24\s*fe\b", re.IGNORECASE), "GFE24"),
    (re.compile(r"\bs\s*24\b", re.IGNORECASE), "GS24"),
    (re.compile(r"\bs\s*23\s*ultra\b", re.IGNORECASE), "GS23U"),
    (re.compile(r"\bs\s*23\s*(?:plus|\+)", re.IGNORECASE), "GS23P"),
    (re.compile(r"\bs\s*23\s*fe\b", re.IGNORECASE), "GFE23"),
    (re.compile(r"\bs\s*23\b", re.IGNORECASE), "GS23"),
    (re.compile(r"\bs\s*22\s*ultra\b", re.IGNORECASE), "GS22U"),
    (re.compile(r"\bs\s*22\b", re.IGNORECASE), "GS22"),
    (re.compile(r"\bs\s*21\s*ultra\b", re.IGNORECASE), "GS21U"),
    (re.compile(r"\bs\s*21\s*(?:plus|\+)", re.IGNORECASE), "GS21P"),
    (re.compile(r"\bs\s*21\s*fe\b", re.IGNORECASE), "GFE21"),
    (re.compile(r"\bs\s*21\b", re.IGNORECASE), "GS21"),
    (re.compile(r"\bs\s*20\s*ultra\b", re.IGNORECASE), "GS20U"),
    (re.compile(r"\bs\s*20\s*(?:plus|\+)", re.IGNORECASE), "GS20P"),
    (re.compile(r"\bs\s*20\s*fe\b", re.IGNORECASE), "GFE20"),
    (re.compile(r"\bs\s*20\b", re.IGNORECASE), "GS20"),
    (re.compile(r"\bs\s*10\s*5g\b", re.IGNORECASE), "GS105G"),
    (re.compile(r"\bs\s*10\s*(?:plus|\+)", re.IGNORECASE), "GS10P"),
    (re.compile(r"\bs\s*10\s*e\b", re.IGNORECASE), "GS10E"),
    (re.compile(r"\bs\s*10\b", re.IGNORECASE), "GS10"),
    (re.compile(r"\bs\s*9\s*(?:plus|\+)", re.IGNORECASE), "GS9P"),
    (re.compile(r"\bs\s*9\b", re.IGNORECASE), "GS9"),
    (re.compile(r"\bs\s*8\s*(?:plus|\+)", re.IGNORECASE), "GS8P"),
    (re.compile(r"\bs\s*8\b", re.IGNORECASE), "GS8"),
    (re.compile(r"\bs\s*7\s*edge\b", re.IGNORECASE), "GS7E"),
    (re.compile(r"\bs\s*7\b", re.IGNORECASE), "GS7"),
    (re.compile(r"\bs\s*6\s*edge\b", re.IGNORECASE), "GS6E"),
    (re.compile(r"\bs\s*6\b", re.IGNORECASE), "GS6"),
    (re.compile(r"\bs\s*5\b", re.IGNORECASE), "GS5"),
    (re.compile(r"\bs\s*4\b", re.IGNORECASE), "GS4"),
    (re.compile(r"\bs\s*3\b", re.IGNORECASE), "GS3"),
    (re.compile(r"\bs\s*2\b", re.IGNORECASE), "GS2"),
    (re.compile(r"\bgalaxy\s+s\s*1\b|\bgalaxy\s+s\s+i\b", re.IGNORECASE), "GS1"),

    # ── Note (Arabic + Roman 숫자 변형) ─────────────────────────────
    (re.compile(r"\bnote\s*20\s*ultra\b", re.IGNORECASE), "GN20U"),
    (re.compile(r"\bnote\s*20\b", re.IGNORECASE), "GN20"),
    (re.compile(r"\bnote\s*10\s*(?:plus|\+)", re.IGNORECASE), "GN10P"),
    (re.compile(r"\bnote\s*10\b", re.IGNORECASE), "GN10"),
    (re.compile(r"\bnote\s*9\b", re.IGNORECASE), "GN9"),
    (re.compile(r"\bnote\s*8\b", re.IGNORECASE), "GN8"),
    (re.compile(r"\bnote\s*7\b", re.IGNORECASE), "GN7"),
    (re.compile(r"\bnote\s*5\b", re.IGNORECASE), "GN5"),
    (re.compile(r"\bnote\s*4\b|\bnote\s+iv\b", re.IGNORECASE), "GN4"),
    (re.compile(r"\bnote\s*3\b|\bnote\s+iii\b", re.IGNORECASE), "GN3"),
    (re.compile(r"\bnote\s*2\b|\bnote\s+ii\b", re.IGNORECASE), "GN2"),
    # Galaxy Note 1세대 (2011) — 숫자/로마자 *없이* "galaxy note" 만 등장.
    # 후행에 II/III/IV/단어 숫자가 오지 않도록 엄격히 차단.
    (re.compile(r"\bgalaxy\s+note\b(?!\s*(?:[0-9]|ii|iii|iv|edge|fe|pro))", re.IGNORECASE), "GN1"),

    # ── Z Fold / Z Flip 영문 약어 (공백 유연) ────────────────────────
    (re.compile(r"\bz\s*fold\s*8\b", re.IGNORECASE), "GZF8"),
    (re.compile(r"\bz\s*fold\s*7\b", re.IGNORECASE), "GZF7"),
    (re.compile(r"\bz\s*fold\s*6\b", re.IGNORECASE), "GZF6"),
    (re.compile(r"\bz\s*fold\s*5\b", re.IGNORECASE), "GZF5"),
    (re.compile(r"\bz\s*fold\s*4\b", re.IGNORECASE), "GZF4"),
    (re.compile(r"\bz\s*fold\s*3\b", re.IGNORECASE), "GZF3"),
    (re.compile(r"\bz\s*fold\s*2\b", re.IGNORECASE), "GZF2"),
    (re.compile(r"\bgalaxy\s+fold\b|\bz\s*fold\b(?!\s*[0-9])", re.IGNORECASE), "GZF1"),
    (re.compile(r"\bz\s*flip\s*8\b", re.IGNORECASE), "GZFL8"),
    (re.compile(r"\bz\s*flip\s*7\b", re.IGNORECASE), "GZFL7"),
    (re.compile(r"\bz\s*flip\s*6\b", re.IGNORECASE), "GZFL6"),
    (re.compile(r"\bz\s*flip\s*5\b", re.IGNORECASE), "GZFL5"),
    (re.compile(r"\bz\s*flip\s*4\b", re.IGNORECASE), "GZFL4"),
    (re.compile(r"\bz\s*flip\s*3\b", re.IGNORECASE), "GZFL3"),
    (re.compile(r"\bz\s*flip\b(?!\s*[0-9])", re.IGNORECASE), "GZFL1"),

    # ── Watch — pre-Watch5 매칭 (Active2 / Active) ───────────────────
    (re.compile(r"\bwatch\s*ultra\b", re.IGNORECASE), "GWU"),
    (re.compile(r"\bwatch\s*9\b", re.IGNORECASE), "GW9"),
    (re.compile(r"\bwatch\s*8\b", re.IGNORECASE), "GW8"),
    (re.compile(r"\bwatch\s*7\b", re.IGNORECASE), "GW7"),
    (re.compile(r"\bwatch\s*6\b", re.IGNORECASE), "GW6"),
    (re.compile(r"\bwatch\s*5\s*pro\b", re.IGNORECASE), "GW5P"),
    (re.compile(r"\bwatch\s*5\b", re.IGNORECASE), "GW5"),
    (re.compile(r"\bwatch\s*4\b", re.IGNORECASE), "GW4"),
    (re.compile(r"\bwatch\s*3\b", re.IGNORECASE), "GW3"),
    (re.compile(r"\bwatch\s*active\s*2\b", re.IGNORECASE), "GWA2"),
    (re.compile(r"\bwatch\s*active\b", re.IGNORECASE), "GWA"),
    (re.compile(r"\bgalaxy\s+watch\b(?!\s*[0-9])", re.IGNORECASE), "GW1"),

    # ── Buds 영문 약어 ──────────────────────────────────────────────
    (re.compile(r"\bbuds\s*4\s*pro\b", re.IGNORECASE), "GB4P"),
    (re.compile(r"\bbuds\s*4\b", re.IGNORECASE), "GB4"),
    (re.compile(r"\bbuds\s*3\s*pro\b", re.IGNORECASE), "GB3P"),
    (re.compile(r"\bbuds\s*3\b", re.IGNORECASE), "GB3"),
    (re.compile(r"\bbuds\s*2\s*pro\b", re.IGNORECASE), "GB2P"),
    (re.compile(r"\bbuds\s*2\b", re.IGNORECASE), "GB2"),
    (re.compile(r"\bbuds\s*pro\b", re.IGNORECASE), "GBP"),
    (re.compile(r"\bbuds\s*live\b", re.IGNORECASE), "GBL"),
    (re.compile(r"\bgalaxy\s+buds\b(?!\s*[0-9])", re.IGNORECASE), "GB1"),

    # ── A 시리즈 영문 (Galaxy 컨텍스트 필수 — _has_galaxy_ctx 에서 확인) ─
    (re.compile(r"\ba\s*57\b", re.IGNORECASE), "GA57"),
    (re.compile(r"\ba\s*56\b", re.IGNORECASE), "GA56"),
    (re.compile(r"\ba\s*55\b", re.IGNORECASE), "GA55"),
    (re.compile(r"\ba\s*54\b", re.IGNORECASE), "GA54"),
    (re.compile(r"\ba\s*53\b", re.IGNORECASE), "GA53"),
    (re.compile(r"\ba\s*52\b", re.IGNORECASE), "GA52"),
    (re.compile(r"\ba\s*51\b", re.IGNORECASE), "GA51"),
    (re.compile(r"\ba\s*50\b", re.IGNORECASE), "GA50"),
    (re.compile(r"\ba\s*36\b", re.IGNORECASE), "GA36"),
    (re.compile(r"\ba\s*26\b", re.IGNORECASE), "GA26"),
    (re.compile(r"\ba\s*16\b", re.IGNORECASE), "GA16"),
    (re.compile(r"\ba\s*07\b", re.IGNORECASE), "GA07"),
]


# 공백 정규화
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower()).strip()


def match_hn_product_code(content: str) -> Optional[str]:
    """HN/영문 본문에서 product code 매칭.

    매칭 우선순위:
      1. 본문에 *samsung* 어휘 없음 → None (HN은 보수적).
      2. HN_NOISE_PATTERNS 매칭되면 None.
      3. ENG_REGEX_PATTERNS 선언 순서로 매칭.
    """
    if not content:
        return None
    norm = _normalize(content)

    # 1) samsung 컨텍스트 필수 — A 시리즈 같은 짧은 변형 매칭에 안전 장치.
    #    "galaxy s10" 같이 명시적인 모델은 _galaxy_strong 검사로 별도 허용.
    has_samsung = bool(_SAMSUNG_CTX_RE.search(norm))
    has_galaxy_model = bool(
        re.search(
            r"\bgalaxy\s+(?:s\d|note|tab|watch|buds|fold|flip|a\d|z\s)",
            norm,
            re.IGNORECASE,
        )
    )
    if not has_samsung and not has_galaxy_model:
        return None

    # 2) Noise 차단
    if _is_blocked(norm):
        return None

    # 3) Regex 매칭 (선언 순서, more specific first)
    for pat, code in ENG_REGEX_PATTERNS:
        if pat.search(norm):
            return code

    return None


# ─────────────────────────────────────────────────────────────────────────
# 4) DB 처리 — Hacker News 만 한정 (PLATFORM_NAME 기본값)
# ─────────────────────────────────────────────────────────────────────────
SELECT_SQL = text("""
    SELECT v.id, v.content_translated, v.content_original
    FROM voc_records v
    JOIN platforms p ON v.platform_id = p.id
    WHERE p.name = :platform
      AND v.product_id IS NULL
      AND v.content_original IS NOT NULL
      AND v.id < :cursor
    ORDER BY v.id DESC
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records
    SET product_id = :pid
    WHERE id = :id
""")


async def load_code_to_id(db: AsyncSession) -> dict:
    rows = (await db.execute(text("SELECT id, code FROM products"))).all()
    return {r.code: r.id for r in rows}


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        code_to_id = await load_code_to_id(db)
        total = (await db.execute(text("""
            SELECT count(*)
            FROM voc_records v
            JOIN platforms p ON v.platform_id = p.id
            WHERE p.name = :platform
              AND v.product_id IS NULL
              AND v.content_original IS NOT NULL
        """), {"platform": PLATFORM_NAME})).scalar_one()

    log.info(
        f"HN relink 대상: {total:,}건 (platform={PLATFORM_NAME}, "
        f"LIMIT={LIMIT or '무제한'}, BATCH={BATCH}, DRY_RUN={DRY_RUN}, "
        f"등록 code={len(code_to_id)})"
    )

    seen = matched = unknown_code = 0
    code_hits: Counter = Counter()
    cursor = 1 << 62

    while True:
        async with Session() as db:
            rows = (await db.execute(
                SELECT_SQL,
                {"batch": BATCH, "cursor": cursor, "platform": PLATFORM_NAME},
            )).all()
            if not rows:
                log.info("  더 이상 처리할 NULL 행 없음 — 종료")
                break

            ups = []
            for r in rows:
                seen += 1
                txt = r.content_translated or r.content_original or ""
                code = match_hn_product_code(txt)
                if not code:
                    continue
                pid = code_to_id.get(code)
                if not pid:
                    unknown_code += 1
                    continue
                ups.append({"id": r.id, "pid": pid})
                code_hits[code] += 1
                matched += 1

            if ups and not DRY_RUN:
                await db.execute(UPDATE_SQL, ups)
                await db.commit()

            cursor = rows[-1].id

            log.info(
                f"  진행 누적 {seen:,} / 매치 {matched:,} / unknown_code "
                f"{unknown_code} (이번 배치 UPDATE={len(ups)}, cursor={cursor})"
            )

        if LIMIT and seen >= LIMIT:
            log.info(f"LIMIT {LIMIT:,} 도달 — 종료")
            break

    await engine.dispose()
    hit_pct = matched * 100.0 / max(seen, 1)
    log.info(f"=== HN relink 완료: 시도 {seen:,} / 매치 {matched:,} / hit {hit_pct:.2f}% ===")
    log.info("  상위 매칭 code:")
    for code, n in code_hits.most_common(25):
        log.info(f"    {code:10s} {n:6,}")


if __name__ == "__main__":
    asyncio.run(main())
