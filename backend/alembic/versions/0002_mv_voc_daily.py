"""mv_voc_daily materialized view

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01

P1-3: voc_records → mv_voc_daily 일일 집계 머티리얼라이즈드 뷰.
- REFRESH MATERIALIZED VIEW CONCURRENTLY 를 위해 UNIQUE INDEX 필수.
- raw(voc_records) 무기한 보존 → mv 손실 시 alembic downgrade/upgrade 한 줄로 재계산 가능.
- 30분 주기 자동 REFRESH 는 crawler/celery_app.py beat_schedule "refresh-mv-voc-daily-30m" 참조.

수동 재계산(전체 재빌드) 1줄:
    PGPASSWORD=signalforge_pass psql -h 127.0.0.1 -p 5434 -U signalforge -d signalforge \
        -c "REFRESH MATERIALIZED VIEW mv_voc_daily;"
"""
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_voc_daily AS
          SELECT date_trunc('day', collected_at)::date  AS day,
                 product_id,
                 platform_id,
                 country_code,
                 language_detected,
                 count(*)                                     AS n,
                 avg(sentiment_score)                         AS sent_avg,
                 sum((sentiment_label = 'positive')::int)     AS pos_cnt,
                 sum((sentiment_label = 'negative')::int)     AS neg_cnt,
                 sum((sentiment_label = 'neutral')::int)      AS neu_cnt
          FROM voc_records
          GROUP BY 1, 2, 3, 4, 5
        WITH DATA;
        """
    )
    # CONCURRENTLY REFRESH 의 전제조건: 최소 1개의 UNIQUE INDEX.
    op.execute(
        """
        CREATE UNIQUE INDEX mv_voc_daily_uniq
            ON mv_voc_daily (day, product_id, platform_id, country_code, language_detected);
        """
    )
    op.execute("CREATE INDEX mv_voc_daily_day ON mv_voc_daily(day);")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_voc_daily;")
