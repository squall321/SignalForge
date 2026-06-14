"""차트 규격 endpoint — MCP charts.py 와 동일 로직을 backend FastAPI 로 노출.

frontend 가 axios 로 호출 → {chart_type, raw, echarts_option, summary} 수령 →
echarts_option 을 ReactECharts 에 그대로 전달 (chartTheme.ts 규격 동일).

voc_active 기반 (archived 제외) → /data-quality 와 수치 정합.
MCP 도구 (mcp-server/tools/charts.py) 와 SQL/스키마를 동기화 유지.
"""
from __future__ import annotations
from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.chart_builders import (
    build_line, build_bar, build_timeline, build_graph, chart_response,
)

router = APIRouter(prefix="/charts", tags=["charts"])

# deep_service.CRISIS_CATALOG 와 동기화 (code/title/기간). 출처: deep_service.py:110
_CRISIS_CATALOG = [
    {"code": "GN7",   "title": "Galaxy Note 7 발화",            "start": "2016-08-19", "end": "2016-12-31"},
    {"code": "GZF1",  "title": "Galaxy Fold 1 디스플레이 결함", "start": "2019-04-15", "end": "2019-12-31"},
    {"code": "GS22U", "title": "Galaxy S22 GoS 게임 성능 제한", "start": "2022-02-25", "end": "2022-06-30"},
    {"code": "GZFL3", "title": "Galaxy Z Flip 3 힌지 논란",     "start": "2021-08-01", "end": "2022-03-31"},
    {"code": "GS20",  "title": "Galaxy S20 5G 가격 논란",       "start": "2020-02-01", "end": "2020-12-31"},
]


@router.get("/sentiment-timeseries")
async def chart_sentiment_timeseries(
    product_codes: List[str] = Query(..., description="제품 코드 목록 (반복 파라미터)"),
    days: int = Query(90), granularity: str = Query("week"),
    db: AsyncSession = Depends(get_db),
):
    """다제품 sentiment 시계열 → 라인 차트 규격."""
    trunc = granularity if granularity in ("day", "week", "month") else "week"
    codes = [c.upper() for c in product_codes]
    rows = (await db.execute(text("""
        SELECT p.code AS product, date_trunc(:trunc, v.published_at)::date AS period,
               COUNT(*) AS cnt, ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v JOIN products p ON p.id = v.product_id
        WHERE p.code = ANY(:codes)
          AND v.published_at >= NOW() - make_interval(days => :days)
          AND v.published_at IS NOT NULL
        GROUP BY p.code, period ORDER BY period
    """), {"trunc": trunc, "codes": codes, "days": days})).mappings().all()

    per_product: dict = {c: [] for c in codes}
    periods: list = []
    for r in rows:
        per_product.setdefault(r["product"], []).append(
            {"period": str(r["period"]), "count": int(r["cnt"]),
             "avg_score": float(r["avg_score"] or 0)})
        if str(r["period"]) not in periods:
            periods.append(str(r["period"]))
    periods.sort()
    series = []
    for code in codes:
        by_p = {d["period"]: d["count"] for d in per_product.get(code, [])}
        series.append({"name": code, "data": [by_p.get(p, 0) for p in periods]})
    opt = build_line(periods, series, f"제품별 VOC 추세 ({granularity})")
    total = sum(sum(s["data"]) for s in series)
    return chart_response("line", {"per_product": per_product, "periods": periods},
                          opt, f"{len(codes)}개 제품 {len(periods)}구간 총 {total:,}건")


@router.get("/country-distribution")
async def chart_country_distribution(
    product_code: Optional[str] = Query(None), top_n: int = Query(15),
    db: AsyncSession = Depends(get_db),
):
    """국가별 VOC 분포 → 가로 막대."""
    top_n = max(1, min(top_n, 50))
    join = "JOIN products p ON p.id = v.product_id" if product_code else ""
    filt = "AND p.code = :code" if product_code else ""
    params: dict = {"top_n": top_n}
    if product_code:
        params["code"] = product_code.upper()
    rows = (await db.execute(text(f"""
        SELECT v.country_code AS country, COUNT(*) AS cnt,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v {join}
        WHERE v.country_code IS NOT NULL {filt}
        GROUP BY v.country_code ORDER BY cnt DESC LIMIT :top_n
    """), params)).mappings().all()
    raw = [{"country_code": r["country"], "voc_count": int(r["cnt"]),
            "avg_score": float(r["avg_score"] or 0)} for r in rows]
    opt = build_bar([d["country_code"] for d in raw], [d["voc_count"] for d in raw],
                    f"국가별 VOC 분포{' — '+product_code.upper() if product_code else ''}",
                    horizontal=True)
    return chart_response("bar", raw, opt,
                          f"상위 {len(raw)}개국 / 1위 {raw[0]['country_code'] if raw else '-'}")


@router.get("/category-distribution")
async def chart_category_distribution(
    product_code: Optional[str] = Query(None), top_n: int = Query(15),
    db: AsyncSession = Depends(get_db),
):
    """카테고리별 VOC 분포 → 가로 막대."""
    top_n = max(1, min(top_n, 50))
    join = "JOIN products p ON p.id = v.product_id" if product_code else ""
    filt = "AND p.code = :code" if product_code else ""
    params: dict = {"top_n": top_n}
    if product_code:
        params["code"] = product_code.upper()
    rows = (await db.execute(text(f"""
        SELECT unnest(v.categories) AS cat, COUNT(*) AS cnt,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v {join}
        WHERE v.categories IS NOT NULL {filt}
        GROUP BY cat ORDER BY cnt DESC LIMIT :top_n
    """), params)).mappings().all()
    raw = [{"category": r["cat"], "voc_count": int(r["cnt"]),
            "avg_score": float(r["avg_score"] or 0)} for r in rows]
    opt = build_bar([d["category"] for d in raw], [d["voc_count"] for d in raw],
                    f"카테고리별 VOC 분포{' — '+product_code.upper() if product_code else ''}",
                    horizontal=True)
    return chart_response("bar", raw, opt,
                          f"상위 {len(raw)}개 카테고리 / 1위 {raw[0]['category'] if raw else '-'}")


@router.get("/crisis-timeline")
async def chart_crisis_timeline(
    case_code: Optional[str] = Query(None), db: AsyncSession = Depends(get_db),
):
    """위기 사례 일별 timeline → line+area (peak marker). 출처: deep_service.crisis_cases."""
    specs = ([s for s in _CRISIS_CATALOG if s["code"] == (case_code or "").upper()]
             if case_code else _CRISIS_CATALOG)
    if not specs:
        return chart_response("line", {"error": f"unknown case_code: {case_code}"},
                              build_timeline([], [], "위기 타임라인"), "사례 없음")
    results = []
    for spec in specs:
        pid_row = (await db.execute(
            text("SELECT id FROM products WHERE code = :code"), {"code": spec["code"]})).first()
        if not pid_row:
            continue
        pp = {"pid": pid_row[0], "lo": date.fromisoformat(spec["start"]),
              "hi": date.fromisoformat(spec["end"])}
        agg = (await db.execute(text("""
            SELECT COUNT(*) AS n,
                   (SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::numeric
                    / NULLIF(COUNT(*),0)) AS neg_rate
            FROM voc_active WHERE product_id = :pid AND published_at IS NOT NULL
              AND published_at::date BETWEEN :lo AND :hi
        """), pp)).first()
        tl = (await db.execute(text("""
            SELECT published_at::date AS d, COUNT(*) AS c
            FROM voc_active WHERE product_id = :pid AND published_at IS NOT NULL
              AND published_at::date BETWEEN :lo AND :hi
            GROUP BY d ORDER BY d
        """), pp)).all()
        results.append({"code": spec["code"], "title": spec["title"],
                        "total_voc": int(agg.n) if agg else 0,
                        "neg_rate": round(float(agg.neg_rate or 0), 4) if agg else 0.0,
                        "timeline": [{"day": str(r.d), "count": int(r.c)} for r in tl]})
    primary = results[0] if results else {"timeline": [], "title": "위기"}
    labels = [p["day"] for p in primary["timeline"]]
    values = [p["count"] for p in primary["timeline"]]
    markers = []
    if values:
        mx = max(values); pk = values.index(mx)
        markers = [{"coord": [labels[pk], mx], "name": "peak", "value": mx}]
    opt = build_timeline(labels, values, f"위기 타임라인 — {primary['title']}", markers=markers)
    raw = results if not case_code else (results[0] if results else {})
    return chart_response("line", raw, opt,
                          f"{len(results)}개 위기 사례 / 표시: {primary['title']}")


@router.get("/keyword-network")
async def chart_keyword_network(
    product_code: Optional[str] = Query(None), days: int = Query(30),
    min_cooccur: int = Query(3), max_nodes: int = Query(40),
    db: AsyncSession = Depends(get_db),
):
    """키워드 동시출현 force-graph (union-find 군집). 출처: deep_service.keyword_network."""
    min_cooccur = max(2, min_cooccur)
    max_nodes = max(5, min(max_nodes, 80))
    pfilt = "AND vr.product_id = (SELECT id FROM products WHERE code = :code)" if product_code else ""
    params: dict = {"days": days, "min_cooccur": min_cooccur}
    if product_code:
        params["code"] = product_code.upper()
    rows = (await db.execute(text(f"""
        WITH pair AS (
            SELECT a.keyword AS k1, b.keyword AS k2
            FROM voc_keywords a
            JOIN voc_keywords b ON a.voc_id = b.voc_id AND a.keyword < b.keyword
            JOIN voc_active vr ON vr.id = a.voc_id
            WHERE vr.published_at >= NOW() - make_interval(days => :days)
              AND vr.published_at IS NOT NULL {pfilt}
        ), ed AS (
            SELECT k1, k2, COUNT(*) AS w FROM pair
            GROUP BY k1, k2 HAVING COUNT(*) >= :min_cooccur
        ), kw_freq AS (
            SELECT vk.keyword, COUNT(*) AS f,
                   MODE() WITHIN GROUP (ORDER BY vr.language_detected) AS lang
            FROM voc_keywords vk JOIN voc_active vr ON vr.id = vk.voc_id
            WHERE vr.published_at >= NOW() - make_interval(days => :days)
              AND vr.published_at IS NOT NULL {pfilt}
            GROUP BY vk.keyword
        )
        SELECT e.k1, e.k2, e.w, f1.f AS f1, f1.lang AS l1, f2.f AS f2, f2.lang AS l2
        FROM ed e
        JOIN kw_freq f1 ON f1.keyword = e.k1
        JOIN kw_freq f2 ON f2.keyword = e.k2
        ORDER BY e.w DESC
    """), params)).all()

    deg: dict = {}; meta_map: dict = {}; edge_list = []
    for r in rows:
        deg[r.k1] = deg.get(r.k1, 0) + int(r.w)
        deg[r.k2] = deg.get(r.k2, 0) + int(r.w)
        meta_map[r.k1] = (int(r.f1 or 0), r.l1)
        meta_map[r.k2] = (int(r.f2 or 0), r.l2)
        edge_list.append((r.k1, r.k2, int(r.w)))
    top_ids = {k for k, _ in sorted(deg.items(), key=lambda x: -x[1])[:max_nodes]}
    parent = {k: k for k in top_ids}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    edges_out = []
    for k1, k2, w in edge_list:
        if k1 in top_ids and k2 in top_ids:
            ra, rb = _find(k1), _find(k2)
            if ra != rb:
                parent[ra] = rb
            edges_out.append({"source": k1, "target": k2, "value": w})
    root_to_cid: dict = {}; nodes_out = []
    for k in sorted(top_ids, key=lambda k: -deg[k]):
        cid = root_to_cid.setdefault(_find(k), len(root_to_cid))
        freq, lang = meta_map.get(k, (0, None))
        nodes_out.append({"id": k, "name": k, "value": freq, "category": cid, "lang": lang})
    raw = {"nodes": nodes_out, "edges": edges_out,
           "meta": {"node_count": len(nodes_out), "edge_count": len(edges_out),
                    "community_count": len(root_to_cid), "days": days}}
    opt = build_graph(nodes_out, edges_out,
                      f"키워드 네트워크{' — '+product_code.upper() if product_code else ''} ({days}일)")
    return chart_response("graph", raw, opt,
                          f"노드 {len(nodes_out)}개 / 엣지 {len(edges_out)}개 / 군집 {len(root_to_cid)}개")
