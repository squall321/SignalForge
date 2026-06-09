from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, List


# ── Product ───────────────────────────────────────────────

class ProductBase(BaseModel):
    code: str
    series_code: str
    name_en: str
    name_ko: Optional[str] = None
    is_active: bool = True


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    released_at: Optional[str] = None
    created_at: datetime


# ── Platform ──────────────────────────────────────────────

class PlatformRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    region: Optional[str] = None
    base_url: Optional[str] = None
    is_active: bool


# ── VOC Record ────────────────────────────────────────────

class VocRecordBase(BaseModel):
    content_original: str
    content_translated: Optional[str] = None
    language_detected: Optional[str] = None
    country_code: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    categories: Optional[List[str]] = None
    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0
    engagement_score: Optional[float] = None
    source_url: Optional[str] = None
    author_name: Optional[str] = None
    published_at: Optional[datetime] = None


class VocRecordRead(VocRecordBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: Optional[int] = None
    platform_id: Optional[int] = None
    collected_at: datetime
    processed_at: Optional[datetime] = None


class VocRecordCreate(VocRecordBase):
    product_id: Optional[int] = None
    platform_id: Optional[int] = None
    external_id: Optional[str] = None


# ── VOC List Response ─────────────────────────────────────

class VocListResponse(BaseModel):
    total: int
    items: List[VocRecordRead]
    limit: int
    offset: int


# ── Product Stats ─────────────────────────────────────────

class ProductStats(BaseModel):
    product_code: str
    product_name: str
    total_voc: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_rate: float
    negative_rate: float
    avg_sentiment_score: float
    latest_collected_at: Optional[datetime] = None


# ── CrawlJob ──────────────────────────────────────────────

class CrawlJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform_id: Optional[int] = None
    product_id: Optional[int] = None
    status: str
    items_collected: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None


class CrawlJobTriggerRequest(BaseModel):
    platform_code: str
    product_code: Optional[str] = None
