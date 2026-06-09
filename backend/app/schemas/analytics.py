from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class SentimentDataPoint(BaseModel):
    date: str                   # 'YYYY-MM-DD' or 'YYYY-WW'
    positive: int
    negative: int
    neutral: int
    avg_score: float


class SentimentTrendResponse(BaseModel):
    product_code: str
    granularity: str            # 'day' | 'week' | 'month'
    data: List[SentimentDataPoint]


class CategoryDistItem(BaseModel):
    category: str
    name_ko: Optional[str] = None
    count: int
    percentage: float


class CategoryDistResponse(BaseModel):
    product_code: str
    data: List[CategoryDistItem]


class CountryVOC(BaseModel):
    country_code: str
    count: int
    positive_rate: float
    avg_score: float


class CountryHeatmapResponse(BaseModel):
    product_code: str
    data: List[CountryVOC]


class IssueRanking(BaseModel):
    rank: int
    category: str
    name_ko: Optional[str] = None
    count: int
    negative_rate: float
    sample_texts: List[str]


class TopIssuesResponse(BaseModel):
    product_code: str
    period_days: int
    issues: List[IssueRanking]


class ProductCompareItem(BaseModel):
    product_code: str
    product_name: str
    battery: float = 0.0
    camera: float = 0.0
    display: float = 0.0
    performance: float = 0.0
    software: float = 0.0
    build_quality: float = 0.0
    price: float = 0.0
    design: float = 0.0


class CompareResponse(BaseModel):
    products: List[ProductCompareItem]


# ── 신규 endpoint 응답 schema ──────────────────────────────

class KeywordTrackPoint(BaseModel):
    date: str
    count: int
    positive: int
    negative: int
    neutral: int
    avg_score: float


class KeywordTrackResponse(BaseModel):
    keyword: str
    period_days: int
    granularity: str
    total_matches: int
    data: List[KeywordTrackPoint]


class CohortSentimentMetric(BaseModel):
    product_code: str
    product_name: str
    total: int
    positive: int
    negative: int
    neutral: int
    positive_rate: float
    negative_rate: float
    avg_score: float


class CohortCategoryItem(BaseModel):
    category: str
    count: int


class CohortCategoryMetric(BaseModel):
    product_code: str
    product_name: str
    total: int
    categories: List[CohortCategoryItem]


class CohortCompareResponse(BaseModel):
    dimension: str               # 'sentiment' | 'category'
    period_days: int
    products: List[str]
    sentiment: Optional[List[CohortSentimentMetric]] = None
    category: Optional[List[CohortCategoryMetric]] = None


class SiteHealthItem(BaseModel):
    platform_code: str
    platform_name: str
    region: Optional[str] = None
    count_24h: int
    count_7d: int
    avg_content_length: float
    tagged_rate: float           # categories 비어있지 않은 비율 (%)


class SiteHealthResponse(BaseModel):
    generated_at: str
    sites: List[SiteHealthItem]


class RecentIssueItem(BaseModel):
    id: int
    platform_code: Optional[str] = None
    country_code: Optional[str] = None
    sentiment_score: Optional[float] = None
    categories: Optional[List[str]] = None
    content: str
    published_at: Optional[str] = None
    engagement_score: Optional[float] = None


class RecentIssuesResponse(BaseModel):
    product_code: str
    top_n: int
    issues: List[RecentIssueItem]
