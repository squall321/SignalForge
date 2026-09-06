# 기존 VOC 코퍼스에 다대다 제품 링크(voc_product_links) 백필 — 비교글 신호 복원
"""
voc_product_links 백필.

voc_records.product_id 는 1행 1제품이라 "S26 Ultra vs Fold8" 비교글이 먼저 매칭된
쪽으로만 잡혀 있다(실측: Fold8 언급 글이 GS26U 1,280건에 묻힘). 이 스크립트가
기존 행 전체를 다시 훑어 언급된 모든 제품을 링크한다.

- 대상: archived_at IS NULL (분석에 쓰는 활성 행). archived 는 노이즈로 제외된 것이라
  링크를 만들어도 쓰이지 않고 테이블만 불린다.
- keyset 커서(id > last)로 배치 처리 → 대용량에서도 일정한 성능.
- 멱등: ON CONFLICT DO UPDATE 로 재실행 시 역할까지 갱신.

실행: DATABASE_URL=... python3 -m scripts.backfill_product_links
환경변수: LINK_BATCH(기본 2000), LINK_LIMIT(0=무제한)
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

from base.product_match import infer_all_product_codes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_links")

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("LINK_BATCH", "2000"))
LIMIT = int(os.getenv("LINK_LIMIT", "0"))

SELECT_SQL = text("""
    SELECT id, product_id, content_original
    FROM voc_records
    WHERE archived_at IS NULL AND content_original IS NOT NULL AND id > :after
    ORDER BY id
    LIMIT :batch
""")

UPSERT_SQL = text("""
    INSERT INTO voc_product_links (voc_id, product_id, role)
    VALUES (:v, :p, :r)
    ON CONFLICT (voc_id, product_id) DO UPDATE SET role = EXCLUDED.role
""")

# product_id 가 비어 있는데 추론으로 primary 를 찾은 경우 본 컬럼도 채운다.
# (retag 가 놓친 행 보정 — primary 링크 == product_id 불변식 유지)
SYNC_PRODUCT_ID_SQL = text("""
    UPDATE voc_records SET product_id = :p WHERE id = :v AND product_id IS NULL
""")


def build_links(product_id, content, pmap):
    """저장된 product_id 를 primary 로, 추론된 나머지를 compared/mentioned 로."""
    links = {}
    if product_id is not None:
        links[product_id] = "primary"
    for code, role in infer_all_product_codes(content):
        pid = pmap.get(code.upper())
        if pid is None or pid in links:
            continue
        links[pid] = role if product_id is None else (
            "mentioned" if role == "primary" else role
        )
    return links


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        pmap = {r.code.upper(): r.id for r in
                (await db.execute(text("SELECT code, id FROM products"))).all()}
        total = (await db.execute(text(
            "SELECT count(*) FROM voc_records WHERE archived_at IS NULL"
        ))).scalar_one()
    log.info(f"백필 대상 {total}건 (제품 사전 {len(pmap)}종, BATCH={BATCH})")

    after = 0
    seen = links_written = multi = synced = 0
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
                    links = build_links(r.product_id, r.content_original, pmap)
                    if len(links) > 1:
                        multi += 1
                    for pid, role in links.items():
                        await db.execute(UPSERT_SQL, {"v": r.id, "p": pid, "r": role})
                        links_written += 1
                        # 미태깅 행에서 primary 를 찾았으면 product_id 도 채움
                        if role == "primary" and r.product_id is None:
                            await db.execute(SYNC_PRODUCT_ID_SQL, {"v": r.id, "p": pid})
                            synced += 1
                await db.commit()
            if seen % 50000 < BATCH:
                log.info(f"  진행 {seen}/{total} — 링크 {links_written}, 다중제품 행 {multi}, product_id 보정 {synced}")
            if LIMIT and seen >= LIMIT:
                break
    finally:
        await engine.dispose()
    log.info(f"=== 완료: 스캔 {seen}, 링크 {links_written}, 다중제품 행 {multi}, product_id 보정 {synced} ===")


if __name__ == "__main__":
    asyncio.run(main())
