# primary 재선정(제목 게이팅)을 기존 코퍼스에 반영 — 추론 산물만 안전하게 갱신
"""
primary 재랭킹 백필.

product_match 의 primary 선정이 '패턴 목록 순서'에서 '문서 주제(제목 등장+빈도)'로
바뀌었다. 기존 행에 이를 반영한다.

**안전 범위 한정이 이 스크립트의 핵심이다.** 전 코퍼스 재추론은 금지다 —
활성 행 중 저장된 product_id 가 현행 추론과 다른 행이 1만여 건 있고(hackernews 다수),
이들은 크롤러 명시 매핑(raw.meta["product_code"] / self.product_code)이나 과거 로직의
산물이라 재추론으로 덮으면 조용히 파괴된다. 따라서
  **저장 product_id == 옛 규칙(kept[0]) 결과** 인 행만 갱신한다.
그 조건을 만족하는 행만이 '추론으로 정해진 primary' 이므로 재선정 대상이 된다.

갱신 대상: voc_records.product_id + voc_product_links.role (옛 primary 강등, 새 primary 승격).
후보 집합과 compared/mentioned **판정 규칙**은 건드리지 않는다.

실행: DATABASE_URL=... python3 -m scripts.backfill_primary_rerank [--apply]
      (기본은 dry-run — 무엇이 바뀌는지만 집계)
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

from base.product_match import _COMPILED, _COMPARE_RE, _pick_primary  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_primary")

DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH = int(os.getenv("PRIMARY_BATCH", "2000"))
APPLY = "--apply" in sys.argv

SELECT_SQL = text("""
    SELECT v.id, v.product_id, v.content_original, p.code AS stored_code
    FROM voc_records v
    JOIN products p ON p.id = v.product_id
    WHERE v.archived_at IS NULL AND v.content_original IS NOT NULL AND v.id > :after
    ORDER BY v.id
    LIMIT :batch
""")


def _kept_codes(text_: str):
    """옛 규칙과 동일한 후보 추출(코드당 첫 매칭 + span 겹침 억제). 순서 = 옛 우선순위."""
    kept = []
    for code, pats in _COMPILED:
        span = None
        for pat in pats:
            m = pat.search(text_)
            if m:
                span = m.span()
                break
        if span is None:
            continue
        if any(span[0] < e and s < span[1] for _, (s, e) in kept):
            continue
        kept.append((code, span))
    return [c for c, _ in kept]


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        pmap = {r.code.upper(): r.id for r in
                (await db.execute(text("SELECT code, id FROM products"))).all()}
    log.info(f"백필 시작 (apply={APPLY}, 제품 {len(pmap)}종)")

    after = seen = eligible = changed = skipped_explicit = 0
    dist: dict = {}
    try:
        while True:
            async with Session() as db:
                rows = (await db.execute(SELECT_SQL,
                                         {"after": after, "batch": BATCH})).all()
                if not rows:
                    break
                for r in rows:
                    seen += 1
                    after = r.id
                    codes = _kept_codes(r.content_original)
                    if len(codes) < 2:
                        continue
                    # 안전 게이트 — 저장값이 옛 규칙 결과와 다르면 명시매핑/레거시다
                    if (r.stored_code or "").upper() != codes[0].upper():
                        skipped_explicit += 1
                        continue
                    eligible += 1
                    new_code = _pick_primary(r.content_original, codes)
                    if new_code == codes[0]:
                        continue
                    changed += 1
                    dist[new_code] = dist.get(new_code, 0) + 1
                    dist[codes[0]] = dist.get(codes[0], 0) - 1
                    if not APPLY:
                        continue
                    new_pid = pmap.get(new_code.upper())
                    if new_pid is None:
                        continue
                    demoted = ("compared" if _COMPARE_RE.search(r.content_original)
                               else "mentioned")
                    await db.execute(text(
                        "UPDATE voc_records SET product_id = :pid WHERE id = :id"),
                        {"pid": new_pid, "id": r.id})
                    await db.execute(text(
                        "UPDATE voc_product_links SET role = :role "
                        "WHERE voc_id = :id AND product_id = :pid"),
                        {"role": demoted, "id": r.id, "pid": r.product_id})
                    await db.execute(text(
                        "UPDATE voc_product_links SET role = 'primary' "
                        "WHERE voc_id = :id AND product_id = :pid"),
                        {"id": r.id, "pid": new_pid})
                if APPLY:
                    await db.commit()
            if seen % 50000 < BATCH:
                log.info(f"  진행 {seen} — 대상 {eligible}, 변경 {changed}, "
                         f"명시매핑 보호 {skipped_explicit}")
    finally:
        await engine.dispose()

    top = sorted(dist.items(), key=lambda kv: -abs(kv[1]))[:10]
    log.info(f"=== 완료: 스캔 {seen}, 재선정 대상 {eligible}, 변경 {changed}, "
             f"명시매핑 보호 {skipped_explicit} (apply={APPLY}) ===")
    log.info(f"제품별 증감 상위: {top}")


if __name__ == "__main__":
    asyncio.run(main())
