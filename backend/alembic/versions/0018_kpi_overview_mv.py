"""R16 트랙 C — kpi_overview MV (dashboard 응답 가속)."""
from __future__ import annotations
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


_MV_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS kpi_overview AS
WITH base AS (
  SELECT
    count(*) AS voc_24h,
    avg(sentiment_score)::numeric(6,4) AS sent_avg_24h,
    (count(*) FILTER (WHERE sentiment_label='negative')::numeric
     / NULLIF(count(*),0))::numeric(6,4) AS neg_rate_24h
  FROM voc_records
  WHERE collected_at >= now() - interval '24 hours'
),
platforms_now AS (
  SELECT
    count(*) FILTER (WHERE is_active) AS active_platforms,
    count(*) FILTER (WHERE NOT is_active) AS inactive_platforms
  FROM platforms
),
top_p AS (
  SELECT p.code, count(*) AS n
  FROM voc_records v JOIN products p ON p.id=v.product_id
  WHERE v.collected_at >= now() - interval '24 hours'
  GROUP BY 1 ORDER BY 2 DESC LIMIT 1
),
top_s AS (
  SELECT pl.code, count(*) AS n
  FROM voc_records v JOIN platforms pl ON pl.id=v.platform_id
  WHERE v.collected_at >= now() - interval '24 hours'
  GROUP BY 1 ORDER BY 2 DESC LIMIT 1
),
alerts_recent AS (
  SELECT count(*) AS alerts_24h FROM alert_events
  WHERE fired_at >= now() - interval '24 hours'
)
SELECT
  1 AS id,
  b.voc_24h, b.sent_avg_24h, b.neg_rate_24h,
  pn.active_platforms, pn.inactive_platforms,
  (SELECT code FROM top_p) AS top_product_24h,
  (SELECT code FROM top_s) AS top_platform_24h,
  ar.alerts_24h,
  now() AS generated_at
FROM base b, platforms_now pn, alerts_recent ar
WITH DATA;
"""

_UNIQUE_IDX = "CREATE UNIQUE INDEX IF NOT EXISTS ix_kpi_overview_id ON kpi_overview (id);"


def upgrade() -> None:
    op.execute(_MV_SQL)
    op.execute(_UNIQUE_IDX)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS kpi_overview;")
