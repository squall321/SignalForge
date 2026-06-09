"""R11 트랙 D — galaxy_master_timeline MV

products × voc_records 월별 집계 MV.
- 시리즈/모델 단위로 (월, voc_count, sent_avg, neg_rate) 미리 계산
- /history 페이지의 master timeline 응답 속도 향상 목적
- Celery beat 1h refresh (refresh_galaxy_master_timeline_mv task)
"""
from __future__ import annotations

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_MV_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS galaxy_master_timeline AS
SELECT
  p.code         AS product_code,
  p.name_ko,
  p.released_at,
  p.series_code  AS series,
  date_trunc('month', v.published_at)::date AS month,
  COUNT(*)       AS voc_count,
  AVG(v.sentiment_score)::numeric(6,4) AS sent_avg,
  (COUNT(*) FILTER (WHERE v.sentiment_label='negative')::numeric
   / NULLIF(COUNT(*),0))::numeric(6,4)  AS neg_rate
FROM products p
JOIN voc_records v ON v.product_id = p.id
WHERE v.published_at IS NOT NULL
GROUP BY 1,2,3,4,5
WITH DATA;
"""

_INDEX_SERIES_SQL = """
CREATE INDEX IF NOT EXISTS ix_gmt_series_month
  ON galaxy_master_timeline (series, month);
"""

_INDEX_PRODUCT_SQL = """
CREATE INDEX IF NOT EXISTS ix_gmt_product_month
  ON galaxy_master_timeline (product_code, month);
"""

# REFRESH CONCURRENTLY 위해 unique index 필요
_INDEX_UNIQUE_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_gmt_unique
  ON galaxy_master_timeline (product_code, month);
"""


def upgrade() -> None:
    op.execute(_MV_SQL)
    op.execute(_INDEX_UNIQUE_SQL)
    op.execute(_INDEX_SERIES_SQL)
    op.execute(_INDEX_PRODUCT_SQL)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS galaxy_master_timeline;")
