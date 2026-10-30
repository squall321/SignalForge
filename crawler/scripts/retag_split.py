# 한 제품 코드에 섞여 있던 다른 제품을 새 코드로 갈라 지난 VOC 를 다시 태깅한다(되돌리기 CSV 를 남긴다)
"""
제품 나누기 다시 태깅.

카탈로그가 한 제품으로 세던 것을 둘로 나눌 때(SG-04 안 A — PLM 프로젝트가 다른 두 기기가 한 코드로 합쳐져 있었다)
매처에 새 코드를 넣은 뒤 지난 글을 갈라 준다. 전 코퍼스 재추론은 하지 않는다(backfill_rival_retag 의 교훈 —
크롤러 명시 매핑이 조용히 덮인다). **원래 코드에 붙어 있고, 본문이 새 코드를 실제로 짚는 글만** 고친다.

  - 후보: 원래 코드(--from)가 대표(product_id)이거나 링크(역할 무관)로 붙은 활성 글
  - 고침: 현행 매처(레거시 폴백 없이)가 새 코드(--to)를 찾은 글만
      · 대표가 원래 코드(또는 없음)면 — 대표 ← 매처의 대표, 링크 ← 매처 결과 전체
      · 대표가 다른 제품이면 — 대표는 두고 링크만: 본문이 원래 코드를 더는 짚지 않으면 떼고, 새 코드를 잇는다
        (대표 역할은 대표 제품 몫이라 새 코드는 'mentioned'/'compared')
  - 되돌리기 — 고친 행마다 (voc_id, 옛 대표, 옛 링크, 새 대표, 새 링크) 를 CSV 로 남긴다

실행: DATABASE_URL=... python3 -m scripts.retag_split --from GZF8 --to GZF8U [--apply] [--log 경로]
      (기본은 dry-run — 무엇이 바뀌는지 세고 CSV 만 쓴다)
"""
import argparse
import asyncio
import csv
import datetime
import logging
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from base.product_match import infer_all_product_codes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("retag_split")

CANDIDATES_SQL = text("""
    SELECT v.id, v.content_original, pp.code AS primary_code,
           COALESCE((SELECT string_agg(p2.code || ':' || l2.role, ';' ORDER BY p2.code)
                     FROM voc_product_links l2 JOIN products p2 ON p2.id = l2.product_id
                     WHERE l2.voc_id = v.id), '') AS links
    FROM voc_records v
    LEFT JOIN products pp ON pp.id = v.product_id
    WHERE v.archived_at IS NULL AND v.content_original IS NOT NULL AND v.id > :after
      AND (v.product_id = :from_id
           OR EXISTS (SELECT 1 FROM voc_product_links l WHERE l.voc_id = v.id AND l.product_id = :from_id))
    ORDER BY v.id
    LIMIT :batch
""")


def plan(primary_code, links, out, src, dst):
    """한 글의 새 (대표, 링크[(code, role)]) — 바꿀 것이 없으면 None."""
    codes = [c for c, _ in out]
    if dst not in codes:
        return None                                       # 본문이 새 제품을 짚지 않는다 — 손대지 않음
    if primary_code in (None, src):
        return out[0][0], list(out)
    # 대표가 다른 제품 — 대표는 두고 링크만 고친다
    old = dict(x.split(":", 1) for x in links.split(";") if x)
    new = {c: r for c, r in old.items() if not (c == src and src not in codes)}
    role = dict(out)[dst]
    new[dst] = "mentioned" if role == "primary" else role
    return primary_code, sorted(new.items())


async def main(args) -> dict:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL 미설정")
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    src, dst = args.src.upper(), args.dst.upper()
    async with Session() as db:
        pmap = {r.code.upper(): r.id for r in (await db.execute(text("SELECT code, id FROM products"))).all()}
    for c in (src, dst):
        if c not in pmap:
            raise SystemExit(f"카탈로그에 없는 코드: {c} — 카탈로그 행을 먼저 넣는다")
    logp = pathlib.Path(args.log or f"logs/retag-split-{src}-{dst}-{datetime.date.today().isoformat()}.csv")
    logp.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    after = 0
    try:
        with logp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["voc_id", "old_primary", "old_links", "new_primary", "new_links"])
            while True:
                async with Session() as db:
                    rows = (await db.execute(CANDIDATES_SQL, {"after": after, "from_id": pmap[src],
                                                              "batch": args.batch})).all()
                    if not rows:
                        break
                    for r in rows:
                        after = r.id
                        stats["candidates"] += 1
                        out = infer_all_product_codes(r.content_original, legacy=False)
                        p = plan(r.primary_code, r.links, out, src, dst)
                        if p is None:
                            continue
                        new_primary, new_links = p
                        stats["changed"] += 1
                        stats[f"primary {r.primary_code}->{new_primary}"] += 1
                        w.writerow([r.id, r.primary_code or "", r.links,
                                    new_primary, ";".join(f"{c}:{role}" for c, role in new_links)])
                        if not args.apply:
                            continue
                        if new_primary != r.primary_code:
                            await db.execute(text("UPDATE voc_records SET product_id = :p WHERE id = :v"),
                                             {"p": pmap[new_primary], "v": r.id})
                        await db.execute(text("DELETE FROM voc_product_links WHERE voc_id = :v"), {"v": r.id})
                        for code, role in new_links:
                            if code.upper() in pmap:
                                await db.execute(text(
                                    "INSERT INTO voc_product_links (voc_id, product_id, role) VALUES (:v, :p, :r)"),
                                    {"v": r.id, "p": pmap[code.upper()], "r": role})
                    if args.apply:
                        await db.commit()
    finally:
        await engine.dispose()
    log.info(f"=== {src} → {dst}: 후보 {stats['candidates']} · 고침 {stats['changed']} (apply={args.apply}) — {logp} ===")
    for k, v in stats.most_common():
        if k.startswith("primary"):
            log.info(f"    {k}: {v}")
    return dict(stats)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="제품 나누기 다시 태깅")
    ap.add_argument("--from", dest="src", required=True, help="원래 코드(예 GZF8)")
    ap.add_argument("--to", dest="dst", required=True, help="새 코드(예 GZF8U)")
    ap.add_argument("--apply", action="store_true", help="실제로 고친다(없으면 dry-run)")
    ap.add_argument("--log", help="되돌리기 CSV 경로")
    ap.add_argument("--batch", type=int, default=2000)
    asyncio.run(main(ap.parse_args()))
