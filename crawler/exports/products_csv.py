"""
제품별 통계 CSV.

행 = 제품 1개. 컬럼:
  product_code, product_name_en, product_name_ko, rows,
  sentiment_pos, sentiment_neg, sentiment_neu,
  top_categories,
  neg_excerpt_1 .. neg_excerpt_5   (최신 부정 5건 — 번역본 우선)

직접 실행:
  DATABASE_URL=postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \
    /home/koopark/claude/SignalForge/.venv/bin/python -m exports.products_csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CRAWLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

log = logging.getLogger("exports.products_csv")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DEFAULT_OUT_DIR = Path(
    os.getenv(
        "EXPORTS_OUT_DIR",
        str(Path(__file__).resolve().parents[2] / "reports" / "exports"),
    )
)

EXCERPT_LIMIT = 5
EXCERPT_LEN = 200
TOP_CATEGORIES_LIMIT = 5

CSV_COLUMNS = [
    "product_code",
    "product_name_en",
    "product_name_ko",
    "rows",
    "sentiment_pos",
    "sentiment_neg",
    "sentiment_neu",
    "top_categories",
] + [f"neg_excerpt_{i+1}" for i in range(EXCERPT_LIMIT)]


SUMMARY_SQL = text("""
    SELECT
        pr.id   AS product_id,
        pr.code AS product_code,
        pr.name_en AS name_en,
        pr.name_ko AS name_ko,
        COUNT(v.id)::bigint AS rows,
        COUNT(*) FILTER (WHERE v.sentiment_label = 'positive')::bigint AS pos,
        COUNT(*) FILTER (WHERE v.sentiment_label = 'negative')::bigint AS neg,
        COUNT(*) FILTER (WHERE v.sentiment_label = 'neutral') ::bigint AS neu
    FROM products pr
    LEFT JOIN voc_records v ON v.product_id = pr.id
    GROUP BY pr.id, pr.code, pr.name_en, pr.name_ko
    ORDER BY rows DESC, pr.code
""")


TOP_CATEGORIES_SQL = text("""
    SELECT cat, COUNT(*)::bigint AS cnt
    FROM (
        SELECT UNNEST(v.categories) AS cat
        FROM voc_records v
        WHERE v.product_id = :pid
          AND v.categories IS NOT NULL
    ) AS x
    GROUP BY cat
    ORDER BY cnt DESC
    LIMIT :limit
""")


NEG_EXCERPTS_SQL = text("""
    SELECT COALESCE(v.content_translated, v.content_original) AS body
    FROM voc_records v
    WHERE v.product_id = :pid
      AND v.sentiment_label = 'negative'
    ORDER BY COALESCE(v.published_at, v.collected_at) DESC
    LIMIT :limit
""")


def _excerpt(body: Optional[str]) -> str:
    if not body:
        return ""
    s = " ".join(body.split())   # 줄바꿈/탭 정리
    return s[:EXCERPT_LEN]


async def export_products_csv(
    *,
    out_dir: Path | str | None = None,
    filename: Optional[str] = None,
) -> dict:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 미설정")

    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = filename or f"products-{today}.csv"
    path = out_dir / name

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    rows_written = 0

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)

        async with engine.connect() as conn:
            products = (await conn.execute(SUMMARY_SQL)).fetchall()

            for p in products:
                # top categories
                cat_rows = (await conn.execute(
                    TOP_CATEGORIES_SQL,
                    {"pid": p.product_id, "limit": TOP_CATEGORIES_LIMIT},
                )).fetchall()
                top_cats = "|".join(f"{c.cat}:{c.cnt}" for c in cat_rows)

                # negative excerpts
                neg_rows = (await conn.execute(
                    NEG_EXCERPTS_SQL,
                    {"pid": p.product_id, "limit": EXCERPT_LIMIT},
                )).fetchall()
                excerpts = [_excerpt(r.body) for r in neg_rows]
                while len(excerpts) < EXCERPT_LIMIT:
                    excerpts.append("")

                writer.writerow([
                    p.product_code,
                    p.name_en or "",
                    p.name_ko or "",
                    p.rows,
                    p.pos,
                    p.neg,
                    p.neu,
                    top_cats,
                    *excerpts,
                ])
                rows_written += 1

    await engine.dispose()
    size = path.stat().st_size
    log.info(f"products export 완료: {path} ({rows_written} 행, {size} bytes)")
    return {"path": str(path), "rows": rows_written, "bytes": size}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="제품별 통계 CSV")
    p.add_argument("--out-dir", dest="out_dir", default=None)
    p.add_argument("--filename", default=None)
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    result = asyncio.run(export_products_csv(
        out_dir=args.out_dir,
        filename=args.filename,
    ))
    print(f"OK path={result['path']} rows={result['rows']} bytes={result['bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
