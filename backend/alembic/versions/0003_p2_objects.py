"""P2 객체 — category_daily / kg_edges_daily / voc_keywords / timeline_events

Phase 2 대시보드(지식 그래프 + 시계열 인사이트)를 위한 신규 DB 객체:

- category_daily       : 일별 product × category × country × language 카테고리 빈도/감성 (MV)
- kg_edges_daily       : 일별 지식 그래프 엣지 (product↔category/platform/country, MV)
- voc_keywords         : VOC 본문에서 추출한 키워드 저장 (P2-2 키워드 추출 파이프라인 산출)
- timeline_events      : 출시일/OS 업데이트/이슈 등 이벤트 마커
- timeline_events seed : 알려진 갤럭시 2025 라인업 출시일 8건

Revision ID: 0003_p2_objects
Revises: 0002_mv_voc_daily
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1) category_daily (Materialized View) ───────────────────────────
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS category_daily AS
        SELECT
            date_trunc('day', collected_at)::date          AS day,
            product_id,
            cat                                            AS category,
            country_code,
            language_detected,
            count(*)                                       AS n,
            avg(sentiment_score)::numeric(6,4)             AS sent_avg,
            sum((sentiment_label = 'positive')::int)       AS pos_cnt,
            sum((sentiment_label = 'negative')::int)       AS neg_cnt,
            sum((sentiment_label = 'neutral')::int)        AS neu_cnt
        FROM voc_records v,
             unnest(v.categories) AS cat
        WHERE v.categories IS NOT NULL
          AND array_length(v.categories, 1) > 0
        GROUP BY 1, 2, 3, 4, 5
        WITH NO DATA;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS category_daily_uniq
        ON category_daily (
            day,
            COALESCE(product_id, -1),
            category,
            COALESCE(country_code, '_NULL_'),
            COALESCE(language_detected, '_NULL_')
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS category_daily_day ON category_daily (day);")
    op.execute("CREATE INDEX IF NOT EXISTS category_daily_cat ON category_daily (category);")

    # ── 2) kg_edges_daily (Materialized View) ────────────────────────────
    # 지식 그래프 엣지: product↔category, product↔platform, product↔country
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS kg_edges_daily AS
        SELECT day, edge_type, source, target, weight, sent_avg
        FROM (
            -- product ↔ category
            SELECT
                date_trunc('day', v.collected_at)::date AS day,
                'product_category'                       AS edge_type,
                ('product:' || p.code)                   AS source,
                ('category:' || cat)                     AS target,
                count(*)                                 AS weight,
                avg(v.sentiment_score)::numeric(6,4)     AS sent_avg
            FROM voc_records v
            JOIN products p ON p.id = v.product_id,
                 unnest(v.categories) AS cat
            WHERE v.product_id IS NOT NULL
              AND v.categories IS NOT NULL
            GROUP BY 1, 2, 3, 4

            UNION ALL

            -- product ↔ platform
            SELECT
                date_trunc('day', v.collected_at)::date AS day,
                'product_platform'                       AS edge_type,
                ('product:' || p.code)                   AS source,
                ('platform:' || pl.code)                 AS target,
                count(*)                                 AS weight,
                avg(v.sentiment_score)::numeric(6,4)     AS sent_avg
            FROM voc_records v
            JOIN products p   ON p.id  = v.product_id
            JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.product_id IS NOT NULL
            GROUP BY 1, 2, 3, 4

            UNION ALL

            -- product ↔ country
            SELECT
                date_trunc('day', v.collected_at)::date AS day,
                'product_country'                        AS edge_type,
                ('product:' || p.code)                   AS source,
                ('country:' || v.country_code)           AS target,
                count(*)                                 AS weight,
                avg(v.sentiment_score)::numeric(6,4)     AS sent_avg
            FROM voc_records v
            JOIN products p ON p.id = v.product_id
            WHERE v.product_id IS NOT NULL
              AND v.country_code IS NOT NULL
            GROUP BY 1, 2, 3, 4
        ) sub
        WITH NO DATA;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS kg_edges_daily_uniq
        ON kg_edges_daily (day, edge_type, source, target);
    """)
    op.execute("CREATE INDEX IF NOT EXISTS kg_edges_daily_weight ON kg_edges_daily (weight DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS kg_edges_daily_day ON kg_edges_daily (day);")
    op.execute("CREATE INDEX IF NOT EXISTS kg_edges_daily_type ON kg_edges_daily (edge_type);")

    # ── 3) voc_keywords (테이블) ──────────────────────────────────────────
    op.create_table(
        "voc_keywords",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("voc_id", sa.BigInteger,
                  sa.ForeignKey("voc_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword", sa.Text, nullable=False),
        sa.Column("lang", sa.String(10), nullable=True),
        sa.Column("weight", sa.Float, server_default=sa.text("1.0")),
        sa.Column("extracted_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("voc_keywords_voc", "voc_keywords", ["voc_id"])
    op.create_index("voc_keywords_kw_lang", "voc_keywords", ["keyword", "lang"])
    op.create_index("voc_keywords_extracted", "voc_keywords", ["extracted_at"])

    # ── 4) timeline_events (테이블) ───────────────────────────────────────
    op.create_table(
        "timeline_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),  # release|update|incident|pr
        sa.Column("product_code", sa.String(10), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("timeline_events_date", "timeline_events", ["event_date"])
    op.create_index("timeline_events_product", "timeline_events", ["product_code"])
    op.create_index("timeline_events_type", "timeline_events", ["event_type"])

    # ── 5) timeline_events seed (Galaxy 2025-26 출시일 8건) ───────────────
    op.execute("""
        INSERT INTO timeline_events (event_date, event_type, product_code, title, description) VALUES
          ('2025-01-22', 'release', 'GS25',   'Galaxy S25 Unpacked',          'S25/S25+/S25 Ultra 동시 공개 (Galaxy Unpacked 2025)'),
          ('2025-01-22', 'release', 'GS25P',  'Galaxy S25+ Unpacked',         'S25+ 정식 발표 (Galaxy Unpacked 2025)'),
          ('2025-01-22', 'release', 'GS25U',  'Galaxy S25 Ultra Unpacked',    'S25 Ultra 정식 발표 (Galaxy Unpacked 2025)'),
          ('2025-07-10', 'release', 'GZF7',   'Galaxy Z Fold7 Unpacked',      '7세대 Fold 공개 (Galaxy Unpacked 2025 여름)'),
          ('2025-07-10', 'release', 'GZFL7',  'Galaxy Z Flip7 Unpacked',      '7세대 Flip 공개 (Galaxy Unpacked 2025 여름)'),
          ('2025-07-10', 'release', 'GW8',    'Galaxy Watch8 출시',           '신규 Watch8 라인업'),
          ('2025-07-10', 'release', 'GB3',    'Galaxy Buds3 출시',            'Buds3 동시 발표'),
          ('2025-07-10', 'release', 'GR2',    'Galaxy Ring 2 출시',           '2세대 Galaxy Ring')
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS timeline_events;")
    op.execute("DROP TABLE IF EXISTS voc_keywords;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS kg_edges_daily;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS category_daily;")
