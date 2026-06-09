"""
Knowledge Graph (KG) API Pydantic schemas (P2 T1).

- /api/v1/kg/graph        : 노드/엣지/통계
- /api/v1/kg/node/{id}/samples : 노드별 원문 샘플 (VOC)
- /api/v1/kg/search       : 텍스트 검색 (product/platform/category/country/keyword)

source: kg_edges_daily MV (P2-1 산출) — source/target 는 'type:label' 포맷.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ── /kg/graph ────────────────────────────────────────────────

class KGNode(BaseModel):
    id: str                         # 'product:GS25' 등
    type: str                       # 'product' | 'category' | 'platform' | 'country'
    label: str                      # 표시용 라벨 (product 는 name_ko/en, 그 외는 코드)
    count: int                      # 노드가 등장한 엣지 weight 합계
    sent_avg: float                 # 가중 평균 sentiment_score


class KGEdge(BaseModel):
    source: str
    target: str
    type: str                       # edge_type
    weight: int
    sent_avg: float


class KGStats(BaseModel):
    nodes_count: int
    edges_count: int
    period: Dict[str, str]          # {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'}


class KGGraphResponse(BaseModel):
    nodes: List[KGNode]
    edges: List[KGEdge]
    stats: KGStats


# ── /kg/node/{id}/samples ───────────────────────────────────

class KGNodeSample(BaseModel):
    voc_id: int
    snippet: str
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    source_url: Optional[str] = None
    platform_code: Optional[str] = None
    country_code: Optional[str] = None
    published_at: Optional[str] = None


# ── /kg/search ───────────────────────────────────────────────

class KGSearchHit(BaseModel):
    type: str                       # 'product' | 'platform' | 'category' | 'keyword'
    id: str                         # type:label 또는 keyword 자체
    label: str
    score: float                    # 단순 랭킹 점수 (등장빈도 또는 일치 강도)
