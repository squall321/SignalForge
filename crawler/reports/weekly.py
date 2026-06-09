"""
주간 VOC 리포트 — 최근 7일 vs 전주 7일 비교.

산출:
  - 총 수집량 / 사이트별 / 지역별 (전주 비교)
  - 사이트 health: 24h 0건 (= 어제 UTC 0건) 사이트 목록
  - 제품별 7일 trend — ASCII sparkline (TOP 10)
  - 마크다운 출력: reports/weekly_YYYY-MM-DD.md
      YYYY-MM-DD = 리포트 작성일 (UTC) — 주간 윈도우의 종료일+1

CLI:
  python -m reports.weekly              # 오늘 UTC 기준, 최근 7일
  python -m reports.weekly 2026-06-01   # 해당 UTC 일자 기준 (어제까지 7일)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import asyncpg

try:
    from ._common import connect, ensure_dir, fmt_delta
except ImportError:  # pragma: no cover
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from reports._common import connect, ensure_dir, fmt_delta  # type: ignore

logger = logging.getLogger(__name__)

SPARK_BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    if not values:
        return ""
    vmax = max(values)
    if vmax == 0:
        return SPARK_BARS[0] * len(values)
    return "".join(SPARK_BARS[min(len(SPARK_BARS) - 1, int(v / vmax * (len(SPARK_BARS) - 1)))] for v in values)


# ---------------------------------------------------------------------------
# 쿼리
# ---------------------------------------------------------------------------
async def _q_total(conn, start, end) -> int:
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM voc_records WHERE collected_at >= $1 AND collected_at < $2",
        start, end,
    )
    return int(row["n"])


async def _q_by_platform(conn, start, end):
    return await conn.fetch(
        """
        SELECT p.code, p.name, p.region, COUNT(v.id) AS cnt
        FROM platforms p
        LEFT JOIN voc_records v
          ON v.platform_id = p.id
         AND v.collected_at >= $1 AND v.collected_at < $2
        WHERE p.is_active = true
        GROUP BY p.code, p.name, p.region
        ORDER BY cnt DESC
        """,
        start, end,
    )


async def _q_by_region(conn, start, end):
    return await conn.fetch(
        """
        SELECT COALESCE(p.region, 'UNKNOWN') AS region, COUNT(v.id) AS cnt
        FROM voc_records v
        JOIN platforms p ON p.id = v.platform_id
        WHERE v.collected_at >= $1 AND v.collected_at < $2
        GROUP BY p.region
        ORDER BY cnt DESC
        """,
        start, end,
    )


async def _q_silent_platforms(conn, start, end):
    """대상 24h 윈도우 동안 0건인 활성 플랫폼."""
    return await conn.fetch(
        """
        SELECT p.code, p.name, p.region
        FROM platforms p
        WHERE p.is_active = true
          AND NOT EXISTS (
            SELECT 1 FROM voc_records v
            WHERE v.platform_id = p.id
              AND v.collected_at >= $1 AND v.collected_at < $2
          )
        ORDER BY p.code
        """,
        start, end,
    )


async def _q_product_daily(conn, start, end):
    """제품 × 일자 트렌드 (TOP 10 제품)."""
    return await conn.fetch(
        """
        WITH top_products AS (
            SELECT product_id, COUNT(*) AS total
            FROM voc_records
            WHERE collected_at >= $1 AND collected_at < $2
              AND product_id IS NOT NULL
            GROUP BY product_id
            ORDER BY total DESC
            LIMIT 10
        )
        SELECT pr.code, pr.name_ko, pr.name_en,
               (date_trunc('day', v.collected_at AT TIME ZONE 'UTC'))::date AS d,
               COUNT(v.id) AS cnt
        FROM voc_records v
        JOIN top_products tp ON tp.product_id = v.product_id
        JOIN products pr ON pr.id = v.product_id
        WHERE v.collected_at >= $1 AND v.collected_at < $2
        GROUP BY pr.code, pr.name_ko, pr.name_en, d
        ORDER BY pr.code, d
        """,
        start, end,
    )


# ---------------------------------------------------------------------------
# 렌더
# ---------------------------------------------------------------------------
def _render_md(
    today: date,
    win_start: date,
    win_end: date,
    total_curr: int,
    total_prev: int,
    plat_curr,
    plat_prev,
    regions_curr,
    silent_24h,
    prod_rows,
    days: list[date],
) -> str:
    out: list[str] = []
    out.append(f"# SignalForge Weekly VOC Report — {today.isoformat()}")
    out.append("")
    out.append(f"- 윈도우 (UTC): {win_start.isoformat()} ~ {win_end.isoformat()} (7일)")
    out.append(f"- 총 수집량: **{total_curr:,}** 건 (전주 {total_prev:,} → {fmt_delta(total_curr, total_prev)})")
    out.append("")

    # 사이트별 (TOP 20) — 전주 비교
    out.append("## 사이트별 수집량 (TOP 20, 전주 대비)")
    out.append("")
    prev_map = {r["code"]: int(r["cnt"]) for r in plat_prev}
    rows = [r for r in plat_curr if int(r["cnt"]) > 0][:20]
    if not rows:
        out.append("_데이터 없음_")
    else:
        out.append("| # | 플랫폼 | 지역 | 이번주 | 전주 | Δ |")
        out.append("|---:|---|---:|---:|---:|---|")
        for i, r in enumerate(rows, 1):
            curr = int(r["cnt"])
            prev = prev_map.get(r["code"], 0)
            out.append(
                f"| {i} | {r['code']} ({r['name']}) | {r['region'] or '-'} | "
                f"{curr:,} | {prev:,} | {fmt_delta(curr, prev)} |"
            )
    out.append("")

    # 지역별
    out.append("## 지역별 분포 (이번주)")
    out.append("")
    if not regions_curr:
        out.append("_데이터 없음_")
    else:
        out.append("| 지역 | 건수 | 비중 |")
        out.append("|---|---:|---:|")
        for r in regions_curr:
            pct = (int(r["cnt"]) / total_curr * 100.0) if total_curr else 0.0
            out.append(f"| {r['region']} | {int(r['cnt']):,} | {pct:.1f}% |")
    out.append("")

    # Health: 24h 0건
    out.append("## 사이트 Health — 최근 24h 0건")
    out.append("")
    if not silent_24h:
        out.append("_모든 활성 플랫폼이 24h 내 1건 이상 수집됨_")
    else:
        out.append(f"⚠️ 활성 플랫폼 중 **{len(silent_24h)}개**가 24h 무수집:")
        out.append("")
        out.append("| 플랫폼 | 지역 |")
        out.append("|---|---:|")
        for p in silent_24h:
            out.append(f"| {p['code']} ({p['name']}) | {p['region'] or '-'} |")
    out.append("")

    # 제품 trend (sparkline)
    out.append("## 제품 7일 Trend (TOP 10, 일별 수집량)")
    out.append("")
    if not prod_rows:
        out.append("_제품 매칭된 레코드 없음_")
    else:
        # 일별 buckets
        prod_map: dict[str, dict] = {}
        for r in prod_rows:
            code = r["code"]
            if code not in prod_map:
                prod_map[code] = {
                    "name": r["name_ko"] or r["name_en"],
                    "code": code,
                    "days": {d: 0 for d in days},
                    "total": 0,
                }
            d = r["d"]
            if d in prod_map[code]["days"]:
                prod_map[code]["days"][d] = int(r["cnt"])
                prod_map[code]["total"] += int(r["cnt"])
        # 정렬
        ordered = sorted(prod_map.values(), key=lambda x: x["total"], reverse=True)
        out.append("| 제품 | 총량 | Trend ({} ~ {}) | 일별 |".format(days[0].isoformat(), days[-1].isoformat()))
        out.append("|---|---:|---|---|")
        for p in ordered:
            series = [p["days"][d] for d in days]
            spark = sparkline(series)
            inline = " ".join(str(v) for v in series)
            out.append(f"| {p['name']} ({p['code']}) | {p['total']:,} | `{spark}` | {inline} |")
    out.append("")

    out.append("---")
    out.append(f"_generated_at: {datetime.now(timezone.utc).isoformat()}_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
async def build_weekly_report(today: Optional[date] = None) -> Path:
    """today (UTC) 기준 최근 7일 리포트 생성. today=None 이면 오늘 UTC."""
    if today is None:
        today = datetime.now(timezone.utc).date()

    end = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)  # 오늘 00:00 (배타)
    start = end - timedelta(days=7)  # 7일 전 00:00 (포함)
    prev_end = start
    prev_start = prev_end - timedelta(days=7)
    win_start = start.date()
    win_end = (end - timedelta(days=1)).date()  # 표기용

    # 24h 무수집 = 어제 UTC 0건
    silent_start = end - timedelta(days=1)
    silent_end = end

    days = [(start + timedelta(days=i)).date() for i in range(7)]

    conn = await connect()
    try:
        total_curr = await _q_total(conn, start, end)
        total_prev = await _q_total(conn, prev_start, prev_end)
        plat_curr = await _q_by_platform(conn, start, end)
        plat_prev = await _q_by_platform(conn, prev_start, prev_end)
        regions_curr = await _q_by_region(conn, start, end)
        silent = await _q_silent_platforms(conn, silent_start, silent_end)
        prod_rows = await _q_product_daily(conn, start, end)
    finally:
        await conn.close()

    md = _render_md(
        today=today,
        win_start=win_start,
        win_end=win_end,
        total_curr=total_curr,
        total_prev=total_prev,
        plat_curr=plat_curr,
        plat_prev=plat_prev,
        regions_curr=regions_curr,
        silent_24h=silent,
        prod_rows=prod_rows,
        days=days,
    )

    out_dir = ensure_dir()
    path = out_dir / f"weekly_{today.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    logger.info(f"weekly report 작성: {path}")
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today: Optional[date] = None
    if len(sys.argv) >= 2:
        today = date.fromisoformat(sys.argv[1])
    path = asyncio.run(build_weekly_report(today))
    print(str(path))


if __name__ == "__main__":
    main()
