# MCP 보강 R2 — matview/미태깅 차원 도구. 제품 생애주기·지식그래프 관계·미태깅 VOC 점검.
from typing import Optional, List
from db import get_db_session
from sqlalchemy import text


async def get_product_timeline_tool(
    product_code: str, recent_months: int = 0
) -> dict:
    """제품의 월별 VOC 생애주기 — galaxy_master_timeline matview.

    출시일(released_at) 기준으로 월별 VOC 건수/평균감성/부정율이 어떻게 변했는지.
    recent_months>0 이면 최근 N개월만, 0 이면 전체.
    """
    lim = "" if recent_months <= 0 else "AND month >= (CURRENT_DATE - make_interval(months => :rm))"
    stmt = text(f"""
        SELECT name_ko, series, released_at,
               month, voc_count, sent_avg, neg_rate
        FROM galaxy_master_timeline
        WHERE product_code = :code {lim}
        ORDER BY month
    """)
    params: dict = {"code": product_code.upper()}
    if recent_months > 0:
        params["rm"] = recent_months
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    if not rows:
        return {"product_code": product_code.upper(), "timeline": [], "note": "no timeline data"}
    meta = rows[0]
    return {
        "product_code": product_code.upper(),
        "name_ko": meta["name_ko"],
        "series": meta["series"],
        "released_at": str(meta["released_at"]) if meta["released_at"] else None,
        "months": len(rows),
        "timeline": [
            {"month": str(r["month"]), "voc_count": int(r["voc_count"]),
             "sent_avg": float(r["sent_avg"] or 0), "neg_rate": float(r["neg_rate"] or 0)}
            for r in rows
        ],
    }


async def get_kg_relations_tool(
    node: Optional[str] = None, edge_type: Optional[str] = None, top_n: int = 30
) -> List[dict]:
    """지식그래프 관계 — kg_edges_daily matview (product↔category/country/platform).

    Args:
        node: 예 'product:GS25' / 'category:battery' / 'country:US'. source 또는 target 매칭.
        edge_type: product_category / product_country / product_platform 중 — 선택.
        top_n: 가중치 합 상위 N (기본 30, 최대 100).
    기간 전체를 weight 합산·sentiment 평균으로 집계한다.
    """
    top_n = max(1, min(top_n, 100))
    conds = []
    params: dict = {"top_n": top_n}
    if node:
        conds.append("(source = :node OR target = :node)")
        params["node"] = node
    if edge_type:
        conds.append("edge_type = :et")
        params["et"] = edge_type
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    stmt = text(f"""
        SELECT edge_type, source, target,
               SUM(weight) AS weight, ROUND(AVG(sent_avg)::numeric, 3) AS sent_avg
        FROM kg_edges_daily
        {where}
        GROUP BY edge_type, source, target
        ORDER BY weight DESC
        LIMIT :top_n
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


async def get_unmapped_voc_tool(
    reason: Optional[str] = None, limit: int = 20
) -> dict:
    """미태깅(제품 미매핑) VOC 점검 — 전체 데이터의 ~82% 를 차지하는 미태깅분을 직접 들여다본다.

    unmapped_reason 분포 + 샘플을 함께 반환해, 무엇이 제품 매핑에서 빠지는지(no_model_mention 등)
    데이터 커버리지 한계를 진단할 수 있게 한다.

    Args:
        reason: no_model_mention / non_galaxy / too_short / noise 중 — 선택(샘플 한정).
        limit: 샘플 건수 (기본 20, 최대 100).
    """
    limit = max(1, min(limit, 100))
    async with get_db_session() as db:
        breakdown = (await db.execute(text("""
            SELECT COALESCE(unmapped_reason, '(tagged_or_general)') AS reason, COUNT(*) AS cnt
            FROM voc_active
            WHERE product_id IS NULL
            GROUP BY unmapped_reason ORDER BY cnt DESC
        """))).mappings().all()
        rfilt = "AND unmapped_reason = :reason" if reason else ""
        params: dict = {"limit": limit}
        if reason:
            params["reason"] = reason
        samples = (await db.execute(text(f"""
            SELECT v.id, COALESCE(pl.name,'unknown') AS platform_name, v.country_code,
                   v.unmapped_reason, v.sentiment_label,
                   LEFT(COALESCE(v.content_translated, v.content_original), 160) AS snippet
            FROM voc_active v
            LEFT JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.product_id IS NULL {rfilt}
            ORDER BY v.engagement_score DESC NULLS LAST
            LIMIT :limit
        """), params)).mappings().all()
    return {
        "untagged_total": sum(int(b["cnt"]) for b in breakdown),
        "breakdown": [{"reason": b["reason"], "count": int(b["cnt"])} for b in breakdown],
        "samples": [dict(s) for s in samples],
    }
