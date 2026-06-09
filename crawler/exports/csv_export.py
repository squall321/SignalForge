"""
voc_records → CSV (gzip) 익스포트.

대규모 11만+ 행을 메모리 폭주 없이 스트리밍 기록한다.
서버사이드 페이지네이션(LIMIT/OFFSET 대신 id > last_id)으로 안정적 페이징.

옵션:
  - days:        최근 N일 (collected_at 기준) — None 이면 전체
  - platform_code: 단일 사이트 필터 (예: 'reddit')
  - product_code:  단일 제품 필터 (예: 'GS24U')
  - out_dir:     출력 디렉토리 (기본 reports/exports)
  - batch:       배치 크기 (기본 5000)

스키마 (사양 고정 — 외부 소비자에 대한 계약):
  id, product_code, platform_code, country_code, content_original,
  content_translated, sentiment_score, sentiment_label, categories,
  published_at, collected_at

직접 실행:
  DATABASE_URL=postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \
    /home/koopark/claude/SignalForge/.venv/bin/python -m exports.csv_export --days 1
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import io
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# crawler/ 를 sys.path 에 (단독 실행 호환)
_CRAWLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

log = logging.getLogger("exports.csv_export")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DEFAULT_OUT_DIR = Path(
    os.getenv(
        "EXPORTS_OUT_DIR",
        str(Path(__file__).resolve().parents[2] / "reports" / "exports"),
    )
)

CSV_COLUMNS = [
    "id",
    "product_code",
    "platform_code",
    "country_code",
    "content_original",
    "content_translated",
    "sentiment_score",
    "sentiment_label",
    "categories",
    "published_at",
    "collected_at",
]


def _build_query(days: Optional[int], platform_code: Optional[str], product_code: Optional[str]) -> tuple[str, dict]:
    """id > :last_id 기반 keyset 페이지네이션 쿼리 생성."""
    conds = ["v.id > :last_id"]
    params: dict = {}
    if days is not None and days > 0:
        conds.append("v.collected_at >= NOW() - (:days || ' days')::interval")
        params["days"] = str(days)
    if platform_code:
        conds.append("p.code = :platform_code")
        params["platform_code"] = platform_code
    if product_code:
        conds.append("pr.code = :product_code")
        params["product_code"] = product_code

    where = " AND ".join(conds)
    sql = f"""
        SELECT
            v.id                AS id,
            pr.code             AS product_code,
            p.code              AS platform_code,
            v.country_code      AS country_code,
            v.content_original  AS content_original,
            v.content_translated AS content_translated,
            v.sentiment_score   AS sentiment_score,
            v.sentiment_label   AS sentiment_label,
            v.categories        AS categories,
            v.published_at      AS published_at,
            v.collected_at      AS collected_at
        FROM voc_records v
        LEFT JOIN products  pr ON pr.id = v.product_id
        LEFT JOIN platforms p  ON p.id  = v.platform_id
        WHERE {where}
        ORDER BY v.id ASC
        LIMIT :batch
    """
    return sql, params


def _serialize_categories(cats) -> str:
    """TEXT[] (또는 None) → 사람-친화 표현. CSV 안전한 단일 토큰."""
    if not cats:
        return ""
    if isinstance(cats, (list, tuple)):
        return "|".join(str(c) for c in cats)
    return str(cats)


def _serialize_dt(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).isoformat()
    return str(dt)


async def export_voc_csv(
    *,
    days: Optional[int] = None,
    platform_code: Optional[str] = None,
    product_code: Optional[str] = None,
    out_dir: Path | str | None = None,
    batch: int = 5000,
    filename: Optional[str] = None,
) -> dict:
    """
    voc_records → gzipped CSV.

    Returns:
        {"path": str, "rows": int, "bytes": int}
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 미설정")

    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = filename or f"voc-{today}.csv.gz"
    path = out_dir / name

    sql, params_template = _build_query(days, platform_code, product_code)

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    rows_total = 0
    last_id = 0

    # gzip text writer
    with gzip.open(path, mode="wt", encoding="utf-8", newline="") as gz:
        writer = csv.writer(gz, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)

        async with engine.connect() as conn:
            while True:
                params = dict(params_template)
                params["last_id"] = last_id
                params["batch"] = batch

                result = await conn.execute(text(sql), params)
                rows = result.fetchall()
                if not rows:
                    break

                for r in rows:
                    writer.writerow([
                        r.id,
                        r.product_code or "",
                        r.platform_code or "",
                        r.country_code or "",
                        (r.content_original or "").replace("\x00", ""),
                        (r.content_translated or "").replace("\x00", ""),
                        "" if r.sentiment_score is None else f"{r.sentiment_score:.4f}",
                        r.sentiment_label or "",
                        _serialize_categories(r.categories),
                        _serialize_dt(r.published_at),
                        _serialize_dt(r.collected_at),
                    ])

                last_id = rows[-1].id
                rows_total += len(rows)
                log.info(f"export 진행: {rows_total} 행 (last_id={last_id})")

    await engine.dispose()

    size = path.stat().st_size
    log.info(f"export 완료: {path} ({rows_total} 행, {size} bytes)")
    return {"path": str(path), "rows": rows_total, "bytes": size}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="voc_records → CSV.gz export")
    p.add_argument("--days", type=int, default=None, help="최근 N일 (기본 전체)")
    p.add_argument("--platform", dest="platform_code", default=None)
    p.add_argument("--product", dest="product_code", default=None)
    p.add_argument("--out-dir", dest="out_dir", default=None)
    p.add_argument("--batch", type=int, default=5000)
    p.add_argument("--filename", default=None)
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    result = asyncio.run(export_voc_csv(
        days=args.days,
        platform_code=args.platform_code,
        product_code=args.product_code,
        out_dir=args.out_dir,
        batch=args.batch,
        filename=args.filename,
    ))
    print(f"OK path={result['path']} rows={result['rows']} bytes={result['bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
