"""
T4 국가 지도 (Geo) Pydantic schemas (P3-2).

엔드포인트:
- GET /api/v1/analytics/country/choropleth
- GET /api/v1/analytics/country/{code}/drilldown
- GET /api/v1/analytics/country/diffusion
- GET /api/v1/analytics/country/product-compare
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── 1) choropleth ───────────────────────────────────────────────────
class ChoroplethItem(BaseModel):
    iso2: str                               # 'KR' / 'US' ...
    n: int                                  # 합계 건수
    sent_avg: float                         # -1 ~ 1
    sent_z: float                           # 전 국가 평균 대비 z-score
    covered: bool                           # n>0 데이터 보유 여부


class ChoroplethTotals(BaseModel):
    countries: int                          # covered 국가 수
    n: int                                  # 전체 건수
    sent_avg: float                         # 전 세계 가중 평균


class ChoroplethResponse(BaseModel):
    items: List[ChoroplethItem]
    totals: ChoroplethTotals
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 2) drilldown ────────────────────────────────────────────────────
class DrilldownSite(BaseModel):
    code: str                               # platforms.code
    name: str                               # platforms.name
    n: int
    sent_avg: float


class DrilldownProduct(BaseModel):
    code: str                               # products.code
    name: str                               # products.name_ko
    n: int
    sent_avg: float


class DrilldownCategory(BaseModel):
    category: str                           # voc_categories.code
    name: Optional[str] = None              # voc_categories.name_ko
    n: int
    sent_avg: float


class DrilldownResponse(BaseModel):
    iso2: str
    n: int                                  # 국가 총 건수
    sent_avg: float
    top_sites: List[DrilldownSite]
    top_products: List[DrilldownProduct]
    top_categories: List[DrilldownCategory]


# ── 3) diffusion ────────────────────────────────────────────────────
class DiffusionItem(BaseModel):
    iso2: str
    n: int                                  # 해당 frame n


class DiffusionFrame(BaseModel):
    day: str                                # 'YYYY-MM-DD'
    items: List[DiffusionItem]


class DiffusionResponse(BaseModel):
    frames: List[DiffusionFrame]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 4) product-compare ──────────────────────────────────────────────
class ProductCompareRow(BaseModel):
    country: str                            # iso2
    n: int
    sent_avg: float
    ci_lo: float                            # 95% 신뢰구간 하한 (Wald, normal approx)
    ci_hi: float                            # 95% 신뢰구간 상한


class ProductCompareResponse(BaseModel):
    rows: List[ProductCompareRow]
    meta: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ChoroplethItem",
    "ChoroplethTotals",
    "ChoroplethResponse",
    "DrilldownSite",
    "DrilldownProduct",
    "DrilldownCategory",
    "DrilldownResponse",
    "DiffusionItem",
    "DiffusionFrame",
    "DiffusionResponse",
    "ProductCompareRow",
    "ProductCompareResponse",
]
