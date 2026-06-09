"""P3 객체 — platform_health / country_daily (Phase 3 대시보드 T3/T4)

Phase 3 대시보드:
  T3 커뮤니티 비교 — 60+ 사이트 간 활동/감성 비교 → platform_health MV
  T4 국가 지도 — 세계지도 choropleth, 일별 / 국가 / 제품 단위 → country_daily MV

두 MV 모두 UNIQUE INDEX 보유 → REFRESH CONCURRENTLY 가능 (READ 차단 없음).
Celery beat 의 refresh-p3-mvs-30m (crawler/tasks.py:run_refresh_p3_mvs) 가
30 분마다 동시 갱신.

Revision ID: 0004
Revises: 0003
"""
from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1) platform_health (Materialized View) ───────────────────────────
    # 60+ 사이트의 24h / 7d 활동량 + 감성 요약 + 본문 평균 길이 + 상태 라벨.
    # 상태(status) 룰:
    #   - posts_7d  = 0 → 'dead'  (7일간 무수집)
    #   - posts_24h = 0 → 'idle'  (수집 일시 정지)
    #   - else      → 'active'
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS platform_health AS
        SELECT
            pl.id                                                              AS platform_id,
            pl.code                                                            AS code,
            pl.region                                                          AS region,
            pl.base_url                                                        AS base_url,
            count(v.id) FILTER (WHERE v.collected_at > now() - interval '24 hours') AS posts_24h,
            count(v.id) FILTER (WHERE v.collected_at > now() - interval '7 days')   AS posts_7d,
            avg(v.sentiment_score) FILTER (WHERE v.collected_at > now() - interval '7 days')
                ::numeric(6,4)                                                  AS sent_avg_7d,
            avg(length(v.content_original)) FILTER (WHERE v.collected_at > now() - interval '7 days')
                ::int                                                           AS avg_body_len_7d,
            max(v.collected_at)                                                AS last_collected,
            CASE
                WHEN count(v.id) FILTER (WHERE v.collected_at > now() - interval '7 days') = 0  THEN 'dead'
                WHEN count(v.id) FILTER (WHERE v.collected_at > now() - interval '24 hours') = 0 THEN 'idle'
                ELSE 'active'
            END                                                                AS status
        FROM platforms pl
        LEFT JOIN voc_records v ON v.platform_id = pl.id
        GROUP BY pl.id, pl.code, pl.region, pl.base_url
        WITH NO DATA;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS platform_health_uniq
        ON platform_health (platform_id);
    """)
    op.execute("CREATE INDEX IF NOT EXISTS platform_health_status ON platform_health (status);")
    op.execute("CREATE INDEX IF NOT EXISTS platform_health_region ON platform_health (region);")

    # ── 2) country_daily (Materialized View) ─────────────────────────────
    # 일별 × 국가코드 × 제품 단위 빈도/감성. 세계지도 choropleth + 국가 drilldown
    # + 확산(diffusion) 플레이어의 베이스 데이터.
    #
    # NOTE: REFRESH CONCURRENTLY 의 UNIQUE INDEX 는 WHERE/표현식 컬럼을 허용하지
    # 않는다(WHERE-less, 단순 컬럼). product_id NULL 값을 키에 포함하기 위해
    # SELECT 단계에서 COALESCE(product_id, -1) 를 product_key 컬럼으로 머티리얼라이즈.
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS country_daily AS
        SELECT
            date_trunc('day', collected_at)::date          AS day,
            country_code                                   AS country_code,
            product_id                                     AS product_id,
            COALESCE(product_id, -1)                       AS product_key,
            count(*)                                       AS n,
            avg(sentiment_score)::numeric(6,4)             AS sent_avg,
            sum((sentiment_label = 'positive')::int)       AS pos,
            sum((sentiment_label = 'negative')::int)       AS neg,
            sum((sentiment_label = 'neutral')::int)        AS neu
        FROM voc_records
        WHERE country_code IS NOT NULL
        GROUP BY 1, 2, 3
        WITH NO DATA;
    """)
    # REFRESH CONCURRENTLY 전제: WHERE-less / 단순 컬럼 UNIQUE INDEX.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS country_daily_uniq
        ON country_daily (day, country_code, product_key);
    """)
    op.execute("CREATE INDEX IF NOT EXISTS country_daily_country ON country_daily (country_code);")
    op.execute("CREATE INDEX IF NOT EXISTS country_daily_day ON country_daily (day);")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS country_daily;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS platform_health;")
