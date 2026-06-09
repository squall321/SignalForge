import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional, List

from app.database import Base


# @lat: VocRecord — [[data-model#voc_records]] 참조. categories는 TEXT[] 배열.
class VocRecord(Base):
    __tablename__ = "voc_records"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    product_id: Mapped[Optional[int]] = mapped_column(sa.ForeignKey("products.id"), index=True)
    platform_id: Mapped[Optional[int]] = mapped_column(sa.ForeignKey("platforms.id"), index=True)

    # 원본 정보
    external_id: Mapped[Optional[str]] = mapped_column(sa.String(200))
    source_url: Mapped[Optional[str]] = mapped_column(sa.String(1000))
    author_name: Mapped[Optional[str]] = mapped_column(sa.String(200))

    # 콘텐츠
    content_original: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_translated: Mapped[Optional[str]] = mapped_column(sa.Text)
    language_detected: Mapped[Optional[str]] = mapped_column(sa.String(10))
    country_code: Mapped[Optional[str]] = mapped_column(sa.String(5), index=True)

    # 감성 분석
    sentiment_score: Mapped[Optional[float]] = mapped_column(sa.Float)
    sentiment_label: Mapped[Optional[str]] = mapped_column(sa.String(20), index=True)

    # 이슈 카테고리 (복수)
    categories: Mapped[Optional[List[str]]] = mapped_column(ARRAY(sa.String(30)))

    # 참여도 지표
    likes_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    comments_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    shares_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    engagement_score: Mapped[Optional[float]] = mapped_column(sa.Float)

    # 메타
    published_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))

    # Relationships
    product: Mapped[Optional["Product"]] = relationship(back_populates="voc_records")
    platform: Mapped[Optional["Platform"]] = relationship(back_populates="voc_records")

    __table_args__ = (
        sa.UniqueConstraint("platform_id", "external_id", name="uq_platform_external"),
        sa.Index("idx_voc_product", "product_id", "collected_at"),
        sa.Index("idx_voc_country", "country_code", "product_id"),
        sa.Index("idx_voc_sentiment", "sentiment_label", "product_id"),
    )


# @lat: VocCategory — [[categories]] 참조.
class VocCategory(Base):
    __tablename__ = "voc_categories"

    code: Mapped[str] = mapped_column(sa.String(30), primary_key=True)
    name_en: Mapped[Optional[str]] = mapped_column(sa.String(100))
    name_ko: Mapped[Optional[str]] = mapped_column(sa.String(100))
    keywords: Mapped[Optional[List[str]]] = mapped_column(ARRAY(sa.Text))
