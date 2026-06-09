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
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), server_default=sa.func.now()
    )

    # Relationships
    voc_records: Mapped[List["VocRecord"]] = relationship(back_populates="product")
    crawl_jobs: Mapped[List["CrawlJob"]] = relationship(back_populates="product")
