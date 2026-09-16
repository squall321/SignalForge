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

- **커서 영속**: LINK_LIMIT 으로 하루치를 끊어도 다음 실행이 이어받는다.
  이게 없을 때는 매 실행 after=0 으로 되돌아가 앞 6만 건만 반복해서 훑고
  나머지 46만 건에 영영 닿지 않았다(2026-09-16 발견). 창을 고정하면 꼬리만 긁는다.
  끝까지 가면 커서를 0 으로 되감아 그동안 쌓인 새 글을 다시 훑는다.

실행: DATABASE_URL=... python3 -m scripts.backfill_product_links
환경변수: LINK_BATCH(기본 2000), LINK_LIMIT(0=무제한),
          LINK_STATE(커서 파일 경로, 기본 logs/product-links-state.json)
"""
import asyncio
import json
import logging
import os
import pathlib
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
STATE_PATH = pathlib.Path(os.getenv(
    "LINK_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "product-links-state.json")))


def read_cursor() -> int:
    """마지막으로 훑은 id. 파일이 깨졌거나 없으면 0 부터 — 멱등이라 손해가 없다."""
    try:
        return int(json.loads(STATE_PATH.read_text()).get("after", 0))
    except Exception:
        return 0


def write_cursor(after: int, wrapped: bool) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(
            {"after": after, "wrapped": wrapped}, ensure_ascii=False))
    except Exception as e:      # 커서를 못 써도 이번 회차 결과는 이미 DB 에 있다
        log.warning("커서 저장 실패 — 다음 실행이 처음부터 간다: %s", e)

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

    after = read_cursor()
    start_after = after
    log.info(f"커서 {after} 에서 이어받는다 ({STATE_PATH})")

    wrapped = False
    seen = links_written = multi = synced = 0
    try:
        while True:
            async with Session() as db:
                rows = (await db.execute(
                    SELECT_SQL, {"after": after, "batch": BATCH})).all()
                if not rows:
                    # 코퍼스 끝. 상한이 걸린 회차라면 되감아 남은 예산으로 앞쪽
                    # (그동안 쌓인 새 글)을 훑는다. 되감기는 **회차당 한 번만** —
                    # 상한이 없으면 무한히 돌기 때문이다.
                    if wrapped or not LIMIT or after == 0:
                        break
                    log.info(f"  id {after} 에서 코퍼스 끝 — 커서를 0 으로 되감는다")
                    after, wrapped = 0, True
                    continue
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
        write_cursor(after, wrapped)
        await engine.dispose()
    log.info(f"=== 완료: 스캔 {seen}, 링크 {links_written}, 다중제품 행 {multi}, "
             f"product_id 보정 {synced} ===")
    log.info(f"    커서 {start_after} → {after}"
             f"{' (한 바퀴 돌아 되감음)' if wrapped else ''}")


if __name__ == "__main__":
    asyncio.run(main())
