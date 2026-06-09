"""
Knowledge Graph 집계 서비스 (P2 T1).

source MV: kg_edges_daily (day, edge_type, source, target, weight, sent_avg)
  - source 는 항상 'product:<code>'
  - target 은 'category:<name>' | 'platform:<code>' | 'country:<code>'
  - edge_type ∈ {product_category, product_platform, product_country}

설계 메모:
- /kg/graph: 기간/edge_type/product 필터 후 weight 합계로 edge 응집,
  top_n / min_weight 적용, 노드는 양 끝점 합집합.
- /kg/node/{id}/samples: voc_records 직접 조회. category 는 voc_records.categories
  JSONB 배열 안의 문자열 매칭(category:battery -> 'battery').
- /kg/search: products / platforms / voc_keywords 텍스트 검색을 union.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.kg import (
    KGEdge,
    KGGraphResponse,
    KGNode,
    KGNodeSample,
    KGSearchHit,
    KGStats,
)


_ALLOWED_EDGE_TYPES = {"product_category", "product_platform", "product_country"}
_VALID_NODE_TYPES = {"product", "category", "platform", "country"}


def _normalize_edge_types(edge_types: Optional[List[str]]) -> List[str]:
    """edge_types 파라미터를 정규화. 'all' 또는 None 이면 전체."""
    if not edge_types:
        return sorted(_ALLOWED_EDGE_TYPES)
    flat: List[str] = []
    for e in edge_types:
        flat.extend(s.strip() for s in e.split(","))
    if any(x == "all" for x in flat):
        return sorted(_ALLOWED_EDGE_TYPES)
    out = [x for x in flat if x in _ALLOWED_EDGE_TYPES]
    return out or sorted(_ALLOWED_EDGE_TYPES)


def _default_period(start: Optional[date], end: Optional[date]) -> (date, date):
    """start/end 기본값: 최근 30일."""
    today = datetime.utcnow().date()
    e = end or today
    s = start or (e - timedelta(days=30))
    return s, e


def _parse_node_id(node_id: str) -> (str, str):
    """'type:label' -> (type, label). 잘못된 형식이면 (None, None)."""
    if not node_id or ":" not in node_id:
        return None, None
    t, _, label = node_id.partition(":")
    if t not in _VALID_NODE_TYPES:
        return None, None
    return t, label


class KGService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── /kg/graph ────────────────────────────────────────────

    async def get_graph(
        self,
        start: Optional[date] = None,
        end: Optional[date] = None,
        edge_types: Optional[List[str]] = None,
        product_ids: Optional[List[str]] = None,
        top_n: int = 80,
        min_weight: int = 5,
        lang: Optional[str] = None,
    ) -> KGGraphResponse:
        s, e = _default_period(start, end)
        ets = _normalize_edge_types(edge_types)

        # product filter: kg_edges_daily.source 는 'product:CODE'
        params: Dict[str, Any] = {
            "start": s,
            "end": e,
            "min_weight": int(min_weight),
            "top_n": int(top_n),
            "edge_types": ets,
        }
        product_filter_sql = ""
        if product_ids:
            params["products"] = [f"product:{p.upper()}" for p in product_ids]
            product_filter_sql = "AND source = ANY(:products)"

        # 1) edges 집계 (weight 합 + sent 가중 평균)
        edges_stmt = text(f"""
            SELECT
                source,
                target,
                edge_type,
                SUM(weight)::int                                AS weight,
                CASE WHEN SUM(weight) > 0
                     THEN ROUND((SUM(sent_avg * weight) / SUM(weight))::numeric, 4)
                     ELSE 0
                END                                             AS sent_avg
            FROM kg_edges_daily
            WHERE day >= :start AND day <= :end
              AND edge_type = ANY(:edge_types)
              {product_filter_sql}
            GROUP BY source, target, edge_type
            HAVING SUM(weight) >= :min_weight
            ORDER BY weight DESC
            LIMIT :top_n
        """)
        edge_rows = (await self.db.execute(edges_stmt, params)).all()

        edges: List[KGEdge] = [
            KGEdge(
                source=r.source,
                target=r.target,
                type=r.edge_type,
                weight=int(r.weight or 0),
                sent_avg=float(r.sent_avg or 0),
            )
            for r in edge_rows
        ]

        # 2) 노드 집계: 양 끝점 합집합. count = 노드가 참여한 edge weight 합.
        node_acc: Dict[str, Dict[str, float]] = {}
        for ed in edges:
            for nid in (ed.source, ed.target):
                slot = node_acc.setdefault(nid, {"count": 0.0, "sent_num": 0.0})
                slot["count"] += ed.weight
                slot["sent_num"] += ed.sent_avg * ed.weight

        # product 라벨 보강: name_ko 우선, 없으면 name_en, 없으면 code.
        product_codes = [
            nid.split(":", 1)[1] for nid in node_acc.keys()
            if nid.startswith("product:")
        ]
        product_labels: Dict[str, str] = {}
        if product_codes:
            label_field = "name_ko" if (lang and lang.lower().startswith("ko")) else "name_en"
            row_q = text(f"""
                SELECT code,
                       COALESCE({label_field}, name_en, name_ko, code) AS label
                FROM products
                WHERE code = ANY(:codes)
            """)
            rows = (await self.db.execute(row_q, {"codes": product_codes})).all()
            for r in rows:
                product_labels[r.code] = r.label

        platform_codes = [
            nid.split(":", 1)[1] for nid in node_acc.keys()
            if nid.startswith("platform:")
        ]
        platform_labels: Dict[str, str] = {}
        if platform_codes:
            rows = (await self.db.execute(
                text("SELECT code, name FROM platforms WHERE code = ANY(:codes)"),
                {"codes": platform_codes},
            )).all()
            for r in rows:
                platform_labels[r.code] = r.name or r.code

        nodes: List[KGNode] = []
        for nid, acc in node_acc.items():
            ntype, _, label_raw = nid.partition(":")
            if ntype == "product":
                label = product_labels.get(label_raw, label_raw)
            elif ntype == "platform":
                label = platform_labels.get(label_raw, label_raw)
            else:
                label = label_raw
            cnt = int(acc["count"])
            sent = round(acc["sent_num"] / acc["count"], 4) if acc["count"] else 0.0
            nodes.append(KGNode(
                id=nid, type=ntype, label=label, count=cnt, sent_avg=float(sent),
            ))

        # count 내림차순 정렬 — 클라이언트 안정성 확보.
        nodes.sort(key=lambda n: (-n.count, n.id))

        return KGGraphResponse(
            nodes=nodes,
            edges=edges,
            stats=KGStats(
                nodes_count=len(nodes),
                edges_count=len(edges),
                period={"start": str(s), "end": str(e)},
            ),
        )

    # ── /kg/node/{id}/samples ───────────────────────────────

    async def get_node_samples(
        self,
        node_id: str,
        limit: int = 5,
    ) -> List[KGNodeSample]:
        ntype, label = _parse_node_id(node_id)
        if ntype is None:
            return []

        limit = max(1, min(int(limit), 50))
        params: Dict[str, Any] = {"limit": limit}

        if ntype == "product":
            where_sql = "p.code = :label"
            params["label"] = label.upper()
        elif ntype == "platform":
            where_sql = "pf.code = :label"
            params["label"] = label
        elif ntype == "country":
            where_sql = "v.country_code = :label"
            params["label"] = label.upper()
        elif ntype == "category":
            # voc_records.categories 는 text[] 배열.
            where_sql = ":label = ANY(v.categories)"
            params["label"] = label
        else:
            return []

        stmt = text(f"""
            SELECT
                v.id                                          AS voc_id,
                LEFT(COALESCE(v.content_translated, v.content_original, ''), 240) AS snippet,
                v.sentiment_label,
                v.sentiment_score,
                v.source_url,
                pf.code                                       AS platform_code,
                v.country_code,
                v.published_at
            FROM voc_active v
            LEFT JOIN products  p  ON p.id  = v.product_id
            LEFT JOIN platforms pf ON pf.id = v.platform_id
            WHERE {where_sql}
            ORDER BY v.published_at DESC NULLS LAST, v.id DESC
            LIMIT :limit
        """)
        rows = (await self.db.execute(stmt, params)).all()

        return [
            KGNodeSample(
                voc_id=int(r.voc_id),
                snippet=(r.snippet or "").strip(),
                sentiment_label=r.sentiment_label,
                sentiment_score=float(r.sentiment_score) if r.sentiment_score is not None else None,
                source_url=r.source_url,
                platform_code=r.platform_code,
                country_code=r.country_code,
                published_at=r.published_at.isoformat() if r.published_at else None,
            )
            for r in rows
        ]

    # ── /kg/search ──────────────────────────────────────────

    async def search(self, q: str, limit: int = 10) -> List[KGSearchHit]:
        q = (q or "").strip()
        if not q:
            return []
        limit = max(1, min(int(limit), 50))
        like = f"%{q}%"

        # products: code/name_en/name_ko 매칭
        prod_stmt = text("""
            SELECT code,
                   COALESCE(name_en, name_ko, code) AS label,
                   CASE
                     WHEN LOWER(code) = LOWER(:q)        THEN 100
                     WHEN LOWER(code) LIKE LOWER(:like)  THEN 60
                     WHEN LOWER(name_en) LIKE LOWER(:like) THEN 40
                     WHEN name_ko LIKE :like             THEN 40
                     ELSE 10
                   END AS score
            FROM products
            WHERE code ILIKE :like
               OR name_en ILIKE :like
               OR name_ko LIKE :like
            ORDER BY score DESC, code
            LIMIT :limit
        """)
        prod_rows = (await self.db.execute(
            prod_stmt, {"q": q, "like": like, "limit": limit}
        )).all()

        # platforms: code/name
        plat_stmt = text("""
            SELECT code,
                   COALESCE(name, code) AS label,
                   CASE
                     WHEN LOWER(code) = LOWER(:q)        THEN 90
                     WHEN LOWER(code) LIKE LOWER(:like)  THEN 55
                     WHEN LOWER(name) LIKE LOWER(:like)  THEN 35
                     ELSE 10
                   END AS score
            FROM platforms
            WHERE code ILIKE :like OR name ILIKE :like
            ORDER BY score DESC, code
            LIMIT :limit
        """)
        plat_rows = (await self.db.execute(
            plat_stmt, {"q": q, "like": like, "limit": limit}
        )).all()

        # voc_keywords: keyword aggregation
        kw_stmt = text("""
            SELECT keyword, COUNT(*)::int AS cnt
            FROM voc_keywords
            WHERE keyword ILIKE :like
            GROUP BY keyword
            ORDER BY cnt DESC
            LIMIT :limit
        """)
        kw_rows = (await self.db.execute(
            kw_stmt, {"like": like, "limit": limit}
        )).all()

        hits: List[KGSearchHit] = []
        for r in prod_rows:
            hits.append(KGSearchHit(
                type="product",
                id=f"product:{r.code}",
                label=r.label,
                score=float(r.score),
            ))
        for r in plat_rows:
            hits.append(KGSearchHit(
                type="platform",
                id=f"platform:{r.code}",
                label=r.label,
                score=float(r.score),
            ))
        for r in kw_rows:
            # 정확 일치 시 부스트.
            base = 30 if r.keyword.lower() != q.lower() else 80
            hits.append(KGSearchHit(
                type="keyword",
                id=r.keyword,
                label=r.keyword,
                score=float(base + min(int(r.cnt), 50)),
            ))

        hits.sort(key=lambda h: (-h.score, h.label))
        return hits[:limit]
