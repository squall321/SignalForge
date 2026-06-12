"""MCP 그래프 도구 — backend 차트 로직을 voc_active SQL 로 차용해
   {chart_type, raw, echarts_option, summary} 표준 스키마 반환.

SQL 출처 (voc_active 통일로 backend /data-quality 와 수치 정합):
  ① chart_sentiment_timeseries ← analytics.analyze_sentiment_trend (다제품 확장)
  ② chart_country/category_distribution ← analytics.get_country_breakdown / query.get_top_issues
  ③ chart_crisis_timeline ← backend deep_service.crisis_cases (CRISIS_CATALOG 최소 복제)
"""
from __future__ import annotations
from datetime import date
from typing import Optional, List
from sqlalchemy import text
from db import get_db_session
from tools.chart_spec import (
    build_line, build_bar, build_timeline, chart_response,
)

# deep_service.CRISIS_CATALOG 의 code/title/기간 복제 (backend app 패키지는 MCP sys.path 에
# 없어 import 불가). backend 가 catalog 변경 시 여기도 갱신 — 출처: deep_service.py:110.
# period 는 backend crisis_cases 와 수치 정합을 위해 동일하게 사용 (해당 기간만 timeline).
_CRISIS_CATALOG = [
    {"code": "GN7",   "title": "Galaxy Note 7 발화",            "start": "2016-08-19", "end": "2016-12-31"},
    {"code": "GZF1",  "title": "Galaxy Fold 1 디스플레이 결함", "start": "2019-04-15", "end": "2019-12-31"},
    {"code": "GS22U", "title": "Galaxy S22 GoS 게임 성능 제한", "start": "2022-02-25", "end": "2022-06-30"},
    {"code": "GZFL3", "title": "Galaxy Z Flip 3 힌지 논란",     "start": "2021-08-01", "end": "2022-03-31"},
    {"code": "GS20",  "title": "Galaxy S20 5G 가격 논란",       "start": "2020-02-01", "end": "2020-12-31"},
]


# ── ① 시계열 ──────────────────────────────────────────────────
async def chart_sentiment_timeseries_tool(
    product_codes: List[str], days: int = 90, granularity: str = "week"
) -> dict:
    """다제품 sentiment 시계열 → 제품별 라인 차트."""
    trunc = granularity if granularity in ("day", "week", "month") else "week"
    codes = [c.upper() for c in product_codes]
    stmt = text(f"""
        SELECT p.code AS product, date_trunc(:trunc, v.published_at)::date AS period,
               COUNT(*) AS cnt, ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v JOIN products p ON p.id = v.product_id
        WHERE p.code = ANY(:codes)
          AND v.published_at >= NOW() - make_interval(days => :days)
          AND v.published_at IS NOT NULL
        GROUP BY p.code, period ORDER BY period
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, {"trunc": trunc, "codes": codes, "days": days})).mappings().all()

    per_product: dict[str, list] = {c: [] for c in codes}
    all_periods: list[str] = []
    for r in rows:
        per_product.setdefault(r["product"], []).append(
            {"period": str(r["period"]), "count": int(r["cnt"]),
             "avg_score": float(r["avg_score"] or 0)})
        if str(r["period"]) not in all_periods:
            all_periods.append(str(r["period"]))
    all_periods.sort()

    # 라인: x=기간, 시리즈=제품별 count (기간 누락분 0 채움)
    series = []
    for code in codes:
        by_period = {d["period"]: d["count"] for d in per_product.get(code, [])}
        series.append({"name": code, "data": [by_period.get(p, 0) for p in all_periods]})
    opt = build_line(all_periods, series, f"제품별 VOC 추세 ({granularity})")
    total = sum(sum(s["data"]) for s in series)
    return chart_response("line", {"per_product": per_product, "periods": all_periods},
                          opt, f"{len(codes)}개 제품 {len(all_periods)}구간 총 {total:,}건")


# ── ② 분포 ────────────────────────────────────────────────────
async def chart_country_distribution_tool(
    product_code: Optional[str] = None, top_n: int = 15
) -> dict:
    """국가별 VOC 분포 → 가로 막대."""
    top_n = max(1, min(top_n, 50))
    filt = "AND p.code = :code" if product_code else ""
    stmt = text(f"""
        SELECT v.country_code AS country, COUNT(*) AS cnt,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v {"JOIN products p ON p.id = v.product_id" if product_code else ""}
        WHERE v.country_code IS NOT NULL {filt}
        GROUP BY v.country_code ORDER BY cnt DESC LIMIT :top_n
    """)
    params = {"top_n": top_n}
    if product_code:
        params["code"] = product_code.upper()
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    raw = [{"country_code": r["country"], "voc_count": int(r["cnt"]),
            "avg_score": float(r["avg_score"] or 0)} for r in rows]
    opt = build_bar([d["country_code"] for d in raw], [d["voc_count"] for d in raw],
                    f"국가별 VOC 분포{' — '+product_code.upper() if product_code else ''}",
                    horizontal=True)
    return chart_response("bar", raw, opt,
                          f"상위 {len(raw)}개국 / 1위 {raw[0]['country_code'] if raw else '-'}")


async def chart_category_distribution_tool(
    product_code: Optional[str] = None, top_n: int = 15
) -> dict:
    """카테고리별 VOC 분포 → 가로 막대."""
    top_n = max(1, min(top_n, 50))
    filt = "AND p.code = :code" if product_code else ""
    stmt = text(f"""
        SELECT unnest(v.categories) AS cat, COUNT(*) AS cnt,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v {"JOIN products p ON p.id = v.product_id" if product_code else ""}
        WHERE v.categories IS NOT NULL {filt}
        GROUP BY cat ORDER BY cnt DESC LIMIT :top_n
    """)
    params = {"top_n": top_n}
    if product_code:
        params["code"] = product_code.upper()
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    raw = [{"category": r["cat"], "voc_count": int(r["cnt"]),
            "avg_score": float(r["avg_score"] or 0)} for r in rows]
    opt = build_bar([d["category"] for d in raw], [d["voc_count"] for d in raw],
                    f"카테고리별 VOC 분포{' — '+product_code.upper() if product_code else ''}",
                    horizontal=True)
    return chart_response("bar", raw, opt,
                          f"상위 {len(raw)}개 카테고리 / 1위 {raw[0]['category'] if raw else '-'}")


# ── ③ 위기 타임라인 ──────────────────────────────────────────
async def chart_crisis_timeline_tool(case_code: Optional[str] = None) -> dict:
    """위기 사례 일별 timeline → line+area (스파이크 marker). 출처: deep_service.crisis_cases."""
    specs = ([s for s in _CRISIS_CATALOG if s["code"] == (case_code or "").upper()]
             if case_code else _CRISIS_CATALOG)
    if not specs:
        return chart_response("line", {"error": f"unknown case_code: {case_code}"},
                              build_timeline([], [], "위기 타임라인"), "사례 없음")

    results = []
    async with get_db_session() as db:
        for spec in specs:
            pid_row = (await db.execute(
                text("SELECT id FROM products WHERE code = :code"),
                {"code": spec["code"]})).first()
            if not pid_row:
                continue
            pid = pid_row[0]
            # backend crisis_cases 와 정합: period_start~period_end 기간만 집계.
            # asyncpg 는 date 컬럼 비교에 Python date 객체를 요구 (str 불가).
            pp = {"pid": pid,
                  "lo": date.fromisoformat(spec["start"]),
                  "hi": date.fromisoformat(spec["end"])}
            agg = (await db.execute(text("""
                SELECT COUNT(*) AS n,
                       (SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::numeric
                        / NULLIF(COUNT(*),0)) AS neg_rate
                FROM voc_active
                WHERE product_id = :pid AND published_at IS NOT NULL
                  AND published_at::date BETWEEN :lo AND :hi
            """), pp)).first()
            tl_rows = (await db.execute(text("""
                SELECT published_at::date AS d, COUNT(*) AS c
                FROM voc_active
                WHERE product_id = :pid AND published_at IS NOT NULL
                  AND published_at::date BETWEEN :lo AND :hi
                GROUP BY d ORDER BY d
            """), pp)).all()
            timeline = [{"day": str(r.d), "count": int(r.c)} for r in tl_rows]
            results.append({
                "code": spec["code"], "title": spec["title"],
                "total_voc": int(agg.n) if agg else 0,
                "neg_rate": round(float(agg.neg_rate or 0), 4) if agg else 0.0,
                "timeline": timeline,
            })

    # 단일 사례면 그 timeline 으로 차트, 다건이면 첫 사례 차트 + 전체 raw
    primary = results[0] if results else {"timeline": [], "title": "위기"}
    labels = [p["day"] for p in primary["timeline"]]
    values = [p["count"] for p in primary["timeline"]]
    markers = []
    if values:
        mx = max(values)
        peak_i = values.index(mx)
        markers = [{"coord": [labels[peak_i], mx], "name": "peak", "value": mx}]
    opt = build_timeline(labels, values, f"위기 타임라인 — {primary['title']}", markers=markers)
    raw = results if not case_code else (results[0] if results else {})
    return chart_response("line", raw, opt,
                          f"{len(results)}개 위기 사례 / 표시: {primary['title']}")
