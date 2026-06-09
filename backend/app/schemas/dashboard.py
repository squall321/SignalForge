"""
Dashboard overview Pydantic schemas (P1 MVP).

P1-2: GET /api/v1/dashboard/overview 응답 모델.
- kpis: KPI 카드 4종 (총 VOC, 부정률, Top 제품, alert 건수)
- trend14d: 최근 14일 일별 (date, count, sent_avg)
- top_sites: 상위 5개 platform (code, count, sent_avg)
"""
from pydantic import BaseModel
from typing import List, Optional


class DashboardKPIs(BaseModel):
    total_voc: int
    neg_rate: float                  # % (0~100)
    top_product: Optional[str] = None
    alert_count: int                 # 부정률 > 임계값(50%) 인 제품 수


class TrendPoint(BaseModel):
    date: str                        # 'YYYY-MM-DD'
    count: int
    sent_avg: float                  # sentiment_score 평균 (-1~1)


class TopSiteItem(BaseModel):
    code: str                        # platform code
    count: int
    sent_avg: float


class DashboardOverviewResponse(BaseModel):
    period: str                      # '7d' | '30d' | '90d'
    filters: dict                    # 실제 적용된 필터 echo
    kpis: DashboardKPIs
    trend14d: List[TrendPoint]
    top_sites: List[TopSiteItem]
