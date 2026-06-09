import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional

from app.database import Base


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    platform_id: Mapped[Optional[int]] = mapped_column(sa.ForeignKey("platforms.id"))
    product_id: Mapped[Optional[int]] = mapped_column(sa.ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(sa.String(20), default="pending", index=True)
    # 'pending' | 'running' | 'done' | 'failed'
    items_collected: Mapped[int] = mapped_column(sa.Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text)

    # Relationships
    platform: Mapped[Optional["Platform"]] = relationship(back_populates="crawl_jobs")
    product: Mapped[Optional["Product"]] = relationship(back_populates="crawl_jobs")
