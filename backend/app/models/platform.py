import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional, List

from app.database import Base


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    code: Mapped[str] = mapped_column(sa.String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    region: Mapped[Optional[str]] = mapped_column(sa.String(10))     # 'KR', 'US', 'GLOBAL'
    base_url: Mapped[Optional[str]] = mapped_column(sa.String(255))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    # Relationships
    voc_records: Mapped[List["VocRecord"]] = relationship(back_populates="platform")
    crawl_jobs: Mapped[List["CrawlJob"]] = relationship(back_populates="platform")
