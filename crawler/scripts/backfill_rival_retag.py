# 타사 기기가 삼성 코드로 오태깅된 기존 행을 경쟁사 카탈로그 기준으로 교정
"""
오귀속 교정 백필.

브랜드 한정자 없는 삼성 패턴(`watch\\s*ultra`, `\\bfold\\s*6`, `\\bwatch\\s*6`,
`\\bflip\\s*7`, `\\bbuds\\s*N`)이 타사 기기를 삼성 제품으로 흡수해 왔다.
실측(코퍼스 1/7 표본) — GWU 의 16.9% 가 Apple Watch Ultra, GW6 의 9.5% 가 Redmi
Watch, GZF6 의 5.2% 가 vivo X Fold6 였다. 브랜드 인접 가드와 경쟁사 카탈로그 380종이
들어왔으니 기존 행에 반영한다.

**교정 조건이 이 스크립트의 핵심이다.** D26 의 교훈대로 전 코퍼스 재추론은 금지다 —
저장 product_id 가 현행 추론과 다른 행에는 크롤러 명시 매핑(raw.meta["product_code"])
산물이 섞여 있고, 재추론으로 덮으면 조용히 파괴된다. 따라서 다음을 **모두** 만족할
때만 고친다.
  1) 저장된 코드에 **매칭 패턴이 존재한다** — 패턴 없는 제품(구형 GS2/GS6/GR1 등
     products 769종 중 다수)은 애초에 후보 집합에 들어갈 수 없어, 조건 2가 항상 참이
     된다. 이 게이트가 없으면 "패턴이 없다"를 "본문이 부정한다"로 오판해 구형 태그를
     전부 갈아엎는다(드라이런 실측: GS2→GS22 64건, GS6→AP6 17건 등 234건 오검출).
  2) 저장된 코드가 현행 추론 후보에 **없다** (본문이 그 태그를 더는 뒷받침하지 않음)
  3) 현행 추론이 **다른 제품을 실제로 찾았다** (빈손이면 근거 없이 지우는 셈이라 보류)
갱신 대상: voc_records.product_id 와 그 행의 voc_product_links 전체(재구성).

실행: DATABASE_URL=... python3 -m scripts.backfill_rival_retag [--apply]
      (기본은 dry-run — 무엇이 바뀌는지만 집계)
환경변수: RETAG_BATCH(기본 2000), RETAG_LIMIT(0=무제한)
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

from base.product_match import (  # noqa: E402
    PRODUCT_PATTERNS, infer_all_product_codes, _brand_of,
)

# 패턴이 있는 코드만 추론으로 판정할 수 있다 (게이트 1)
PATTERNED = {c.upper() for c, _ in PRODUCT_PATTERNS}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("rival_retag")

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("RETAG_BATCH", "2000"))
LIMIT = int(os.getenv("RETAG_LIMIT", "0"))
APPLY = "--apply" in sys.argv

SELECT_SQL = text("""
    SELECT v.id, v.content_original, p.code AS stored_code
    FROM voc_records v JOIN products p ON p.id = v.product_id
    WHERE v.archived_at IS NULL AND v.content_original IS NOT NULL AND v.id > :after
    ORDER BY v.id
    LIMIT :batch
""")


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        pmap = {r.code.upper(): r.id for r in
                (await db.execute(text("SELECT code, id FROM products"))).all()}
    log.info(f"교정 백필 시작 (apply={APPLY}, 제품 {len(pmap)}종)")

    after = seen = 0
    cross = same = held = nopat = 0     # 타브랜드 / 동브랜드 / 근거없음 / 패턴없음
    flow: Counter = Counter()
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
                    stored = (r.stored_code or "").upper()
                    if stored not in PATTERNED:
                        nopat += 1             # 판정 불가 — 손대지 않는다
                        continue
                    out = infer_all_product_codes(r.content_original)
                    codes = {c for c, _ in out}
                    if stored in codes:
                        continue               # 본문이 여전히 뒷받침 → 손대지 않음
                    if not out:
                        held += 1              # 근거 없이 지우지 않는다(명시매핑 보호)
                        continue
                    new_code = out[0][0]
                    if _brand_of(new_code) != _brand_of(stored):
                        cross += 1
                    else:
                        same += 1
                    flow[f"{stored}->{new_code}"] += 1
                    if not APPLY:
                        continue
                    new_pid = pmap.get(new_code.upper())
                    if new_pid is None:
                        continue
                    await db.execute(text(
                        "UPDATE voc_records SET product_id = :p WHERE id = :v"),
                        {"p": new_pid, "v": r.id})
                    # 링크 재구성 — 낡은 태그가 남으면 집계가 계속 오염된다
                    await db.execute(text(
                        "DELETE FROM voc_product_links WHERE voc_id = :v"),
                        {"v": r.id})
                    for code, role in out:
                        pid = pmap.get(code.upper())
                        if pid is None:
                            continue
                        await db.execute(text(
                            "INSERT INTO voc_product_links (voc_id, product_id, role) "
                            "VALUES (:v, :p, :r) ON CONFLICT (voc_id, product_id) "
                            "DO UPDATE SET role = EXCLUDED.role"),
                            {"v": r.id, "p": pid, "r": role})
                if APPLY:
                    await db.commit()
            if seen % 50000 < BATCH:
                log.info(f"  진행 {seen} — 타브랜드 {cross}, 동브랜드 {same}, "
                         f"보류 {held}, 패턴없음 {nopat}")
            if LIMIT and seen >= LIMIT:
                break
    finally:
        await engine.dispose()

    log.info(f"=== 완료: 스캔 {seen}, 타브랜드 교정 {cross}, 동브랜드 세분화 {same}, "
             f"근거없어 보류 {held}, 패턴없어 판정불가 {nopat} (apply={APPLY}) ===")
    for k, v in flow.most_common(25):
        log.info(f"    {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
