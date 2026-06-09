"""
일간 VOC 리포트 — 어제 (UTC 00:00 ~ 오늘 UTC 00:00) 수집분 집계.

산출:
  - 총 수집량 + 사이트별 / 지역별 분포
  - 제품별 sentiment 분포 (TOP 10, 수집량 기준)
  - 카테고리 빈도 (전주 동일 요일 대비 변화)
  - 부정 sentiment 비율이 급증한 제품 알림 (ALERT_WEBHOOK_URL 있을 때)
  - 마크다운 출력: reports/daily_YYYY-MM-DD.md
      (YYYY-MM-DD = 리포트 대상 날짜 = 어제 UTC)

CLI:
  python -m reports.daily              # 어제 UTC 기준
  python -m reports.daily 2026-05-30   # 특정 날짜 (UTC)

`window` 인자로 다른 날짜를 전달하면 해당 UTC 일자 00:00 ~ +24h 범위로 집계한다.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import asyncpg

# Celery 워커의 task import 시 절대경로로도, CLI 의 -m reports.daily 로도 동작하도록
# 양쪽 경로 모두 시도.
try:
    from ._common import connect, ensure_dir, fmt_delta, send_alert
except ImportError:  # pragma: no cover — 직접 실행 시
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from reports._common import connect, ensure_dir, fmt_delta, send_alert  # type: ignore

logger = logging.getLogger(__name__)

# 부정 sentiment 비율 급증 임계값 (전주 대비 +pp)
NEG_RATIO_ALERT_PP = 15.0
NEG_RATIO_MIN_VOLUME = 30  # 최소 수집량 (소수 데이터 노이즈 차단)


# ---------------------------------------------------------------------------
# 쿼리 헬퍼
# ---------------------------------------------------------------------------
async def _q_total(conn: asyncpg.Connection, start: datetime, end: datetime) -> int:
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM voc_records WHERE collected_at >= $1 AND collected_at < $2",
        start, end,
    )
    return int(row["n"])


async def _q_by_platform(conn: asyncpg.Connection, start: datetime, end: datetime):
    return await conn.fetch(
        """
        SELECT p.code, p.name, p.region, COUNT(v.id) AS cnt
        FROM voc_records v
        JOIN platforms p ON p.id = v.platform_id
        WHERE v.collected_at >= $1 AND v.collected_at < $2
        GROUP BY p.code, p.name, p.region
        ORDER BY cnt DESC
        """,
        start, end,
    )


async def _q_by_region(conn: asyncpg.Connection, start: datetime, end: datetime):
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


async def _q_product_sentiment(conn: asyncpg.Connection, start: datetime, end: datetime):
    """제품별 sentiment 분포 — TOP 10 (수집량 기준)."""
    return await conn.fetch(
        """
        SELECT
          pr.code, pr.name_en, pr.name_ko,
          COUNT(v.id) AS total,
          SUM(CASE WHEN v.sentiment_label = 'positive' THEN 1 ELSE 0 END) AS pos,
          SUM(CASE WHEN v.sentiment_label = 'neutral'  THEN 1 ELSE 0 END) AS neu,
          SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END) AS neg,
          AVG(v.sentiment_score) AS avg_score
        FROM voc_records v
        JOIN products pr ON pr.id = v.product_id
        WHERE v.collected_at >= $1 AND v.collected_at < $2
          AND v.product_id IS NOT NULL
        GROUP BY pr.code, pr.name_en, pr.name_ko
        ORDER BY total DESC
        LIMIT 10
        """,
        start, end,
    )


async def _q_category_counts(conn: asyncpg.Connection, start: datetime, end: datetime):
    """카테고리 빈도 (unnest 사용 — TEXT[] 칼럼)."""
    return await conn.fetch(
        """
        SELECT cat, COUNT(*) AS cnt
        FROM voc_records v, unnest(v.categories) AS cat
        WHERE v.collected_at >= $1 AND v.collected_at < $2
        GROUP BY cat
        ORDER BY cnt DESC
        """,
        start, end,
    )


async def _q_neg_ratio_per_product(conn: asyncpg.Connection, start: datetime, end: datetime):
    """제품별 부정 비율 — alert 비교용 (수집량 >= NEG_RATIO_MIN_VOLUME)."""
    return await conn.fetch(
        """
        SELECT pr.code, pr.name_ko, pr.name_en,
               COUNT(v.id) AS total,
               SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END)::float
                 / NULLIF(COUNT(v.id), 0) AS neg_ratio
        FROM voc_records v
        JOIN products pr ON pr.id = v.product_id
        WHERE v.collected_at >= $1 AND v.collected_at < $2
          AND v.product_id IS NOT NULL
        GROUP BY pr.code, pr.name_ko, pr.name_en
        HAVING COUNT(v.id) >= $3
        """,
        start, end, NEG_RATIO_MIN_VOLUME,
    )


# ---------------------------------------------------------------------------
# 마크다운 렌더링
# ---------------------------------------------------------------------------
def _render_md(
    target: date,
    total: int,
    platforms,
    regions,
    products,
    cats_today,
    cats_prev,
    neg_alerts,
) -> str:
    out: list[str] = []
    out.append(f"# SignalForge Daily VOC Report — {target.isoformat()}")
    out.append("")
    out.append(f"- 대상 (UTC): {target.isoformat()} 00:00 ~ {(target + timedelta(days=1)).isoformat()} 00:00")
    out.append(f"- 총 수집량: **{total:,}** 건")
    out.append("")

    # 사이트별 (TOP 20)
    out.append("## 사이트별 수집량 (TOP 20)")
    out.append("")
    if not platforms:
        out.append("_데이터 없음_")
    else:
        out.append("| # | 플랫폼 | 지역 | 건수 |")
        out.append("|---:|---|---:|---:|")
        for i, r in enumerate(platforms[:20], 1):
            out.append(f"| {i} | {r['code']} ({r['name']}) | {r['region'] or '-'} | {r['cnt']:,} |")
    out.append("")

    # 지역별
    out.append("## 지역별 분포")
    out.append("")
    if not regions:
        out.append("_데이터 없음_")
    else:
        out.append("| 지역 | 건수 | 비중 |")
        out.append("|---|---:|---:|")
        for r in regions:
            pct = (r["cnt"] / total * 100.0) if total else 0.0
            out.append(f"| {r['region']} | {r['cnt']:,} | {pct:.1f}% |")
    out.append("")

    # 제품별 sentiment TOP10
    out.append("## 제품별 Sentiment 분포 (TOP 10, 수집량 기준)")
    out.append("")
    if not products:
        out.append("_제품 매칭된 레코드 없음_")
    else:
        out.append("| 제품 | 총 | 긍정 | 중립 | 부정 | 평균 score |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for r in products:
            avg = r["avg_score"]
            avg_s = f"{avg:.2f}" if avg is not None else "-"
            name = r["name_ko"] or r["name_en"]
            out.append(
                f"| {name} ({r['code']}) | {r['total']:,} | "
                f"{r['pos']:,} | {r['neu']:,} | {r['neg']:,} | {avg_s} |"
            )
    out.append("")

    # 카테고리 — 전주 동일 요일 대비
    out.append("## 카테고리 빈도 (전주 동일 요일 대비)")
    out.append("")
    if not cats_today:
        out.append("_카테고리 태깅된 레코드 없음_")
    else:
        prev_map = {c["cat"]: int(c["cnt"]) for c in cats_prev}
        out.append("| 카테고리 | 오늘 | 7일 전 | Δ |")
        out.append("|---|---:|---:|---|")
        for c in cats_today:
            cat = c["cat"]
            curr = int(c["cnt"])
            prev = prev_map.get(cat, 0)
            out.append(f"| {cat} | {curr:,} | {prev:,} | {fmt_delta(curr, prev)} |")
    out.append("")

    # 부정 sentiment 급증 알림
    out.append("## 부정 sentiment 비율 급증 알림")
    out.append("")
    if not neg_alerts:
        out.append(f"_임계값 ({NEG_RATIO_ALERT_PP:+.1f}pp, 최소 {NEG_RATIO_MIN_VOLUME}건) 초과 제품 없음_")
    else:
        out.append(f"임계값: 전주 동일 요일 대비 부정비율 **+{NEG_RATIO_ALERT_PP:.1f}pp** 이상, 수집량 ≥ {NEG_RATIO_MIN_VOLUME}건")
        out.append("")
        out.append("| 제품 | 오늘 부정% | 7일 전 부정% | Δ (pp) | 오늘 수집량 |")
        out.append("|---|---:|---:|---:|---:|")
        for a in neg_alerts:
            out.append(
                f"| {a['name']} ({a['code']}) | {a['curr']*100:.1f}% | "
                f"{a['prev']*100:.1f}% | +{a['delta']*100:.1f}pp | {a['total']:,} |"
            )
    out.append("")
    out.append("---")
    out.append(f"_generated_at: {datetime.now(timezone.utc).isoformat()}_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
async def build_daily_report(target: Optional[date] = None) -> Path:
    """대상 일자의 daily 리포트를 생성하고 파일 경로를 반환한다.

    target=None 이면 어제 (UTC).
    """
    if target is None:
        target = (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    prev_start = start - timedelta(days=7)
    prev_end = end - timedelta(days=7)

    conn = await connect()
    try:
        total = await _q_total(conn, start, end)
        platforms = await _q_by_platform(conn, start, end)
        regions = await _q_by_region(conn, start, end)
        products = await _q_product_sentiment(conn, start, end)
        cats_today = await _q_category_counts(conn, start, end)
        cats_prev = await _q_category_counts(conn, prev_start, prev_end)

        neg_curr = await _q_neg_ratio_per_product(conn, start, end)
        neg_prev = await _q_neg_ratio_per_product(conn, prev_start, prev_end)
    finally:
        await conn.close()

    # 부정 비율 alert 계산
    prev_neg_map = {r["code"]: (r["neg_ratio"] or 0.0) for r in neg_prev}
    neg_alerts = []
    for r in neg_curr:
        curr_ratio = float(r["neg_ratio"] or 0.0)
        prev_ratio = float(prev_neg_map.get(r["code"], 0.0))
        delta = curr_ratio - prev_ratio
        if delta * 100.0 >= NEG_RATIO_ALERT_PP:
            neg_alerts.append({
                "code": r["code"],
                "name": r["name_ko"] or r["name_en"],
                "curr": curr_ratio,
                "prev": prev_ratio,
                "delta": delta,
                "total": int(r["total"]),
            })
    neg_alerts.sort(key=lambda x: x["delta"], reverse=True)

    md = _render_md(target, total, platforms, regions, products, cats_today, cats_prev, neg_alerts)

    out_dir = ensure_dir()
    path = out_dir / f"daily_{target.isoformat()}.md"
    path.write_text(md, encoding="utf-8")

    # Webhook 알림 — alert 가 있을 때만
    if neg_alerts:
        lines = [f"[SignalForge] {target.isoformat()} 부정 VOC 급증 — {len(neg_alerts)}개 제품"]
        for a in neg_alerts[:5]:
            lines.append(f"  - {a['name']} ({a['code']}): {a['prev']*100:.1f}% → {a['curr']*100:.1f}% (+{a['delta']*100:.1f}pp, n={a['total']})")
        send_alert("\n".join(lines))

    logger.info(f"daily report 작성: {path}")
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    target: Optional[date] = None
    if len(sys.argv) >= 2:
        target = date.fromisoformat(sys.argv[1])
    path = asyncio.run(build_daily_report(target))
    print(str(path))


if __name__ == "__main__":
    main()
