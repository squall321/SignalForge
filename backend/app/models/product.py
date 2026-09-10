import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import date, datetime
from typing import Optional, List

from app.database import Base


# @lat: Product — [[data-model#products]] 참조.
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    code: Mapped[str] = mapped_column(sa.String(10), unique=True, nullable=False, index=True)
    series_code: Mapped[str] = mapped_column(sa.String(4), nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    name_ko: Mapped[Optional[str]] = mapped_column(sa.String(100))
    released_at: Mapped[Optional[date]] = mapped_column(sa.Date)
    # 0038 — 제품 랭킹에서 자사/타사를 구분하기 위한 단일 출처.
    # crawler/base/product_match.py 의 _CODE_BRAND_PREFIX 와 같은 표에서 유도한다.
    brand: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default="samsung", index=True
    )
    # 0039 — 제품군. series_code 는 브랜드와 라인이 섞여 있어 제품군을 못 대신한다
    # (series GW 에 Galaxy Fit(밴드)이, OPW 에 OnePlus Buds 가 들어 있었다).
    category: Mapped[str] = mapped_column(sa.String(16), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )

    # Relationships
    voc_records: Mapped[List["VocRecord"]] = relationship(back_populates="product")
    crawl_jobs: Mapped[List["CrawlJob"]] = relationship(back_populates="product")
