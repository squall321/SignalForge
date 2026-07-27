"""voc_active VIEW 정식화 — dev 에 수기 생성됐지만 마이그레이션에 없어 신규/prod DB 에서
   'relation voc_active does not exist' 로 조회가 죽던 gap 을 메운다(Data-Clean-2 잔재).
   정의는 voc_records 의 24컬럼을 archived_at IS NULL 로 거른 것(test_voc_active_queries 로 검증).
"""
from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE OR REPLACE — 뷰가 이미(수기로) 있는 dev 에서도 무해하게 정합화한다.
    op.execute(
        """
        CREATE OR REPLACE VIEW voc_active AS
         SELECT id, product_id, platform_id, external_id, source_url, author_name,
                content_original, content_translated, language_detected, country_code,
                sentiment_score, sentiment_label, categories, likes_count, comments_count,
                shares_count, engagement_score, published_at, collected_at, processed_at,
                unmapped_reason, topics, content_hash, archived_at
           FROM voc_records
          WHERE archived_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS voc_active")
