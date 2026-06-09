"""voc_topics — voc_records.topics text[] 컬럼 + gin 인덱스 (Track B, R8).

배경:
  - 모델 미언급 짧은 댓글 (Instiz/Dogdrip 등) 을 topic 으로 분류해
    NULL product_id 후기의 의미를 살리기 위함.
  - categorizer 12 카테고리와 독립된 의도/감정 축.

설계:
  - ADD COLUMN topics text[] DEFAULT '{}' (NOT NULL 미설정 — 백필 단계 단순화)
  - GIN 인덱스 — topic 다중 매칭 조회 성능
  - 멱등 — IF NOT EXISTS

Revision ID: 0012
Revises: 0011
"""
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE voc_records
        ADD COLUMN IF NOT EXISTS topics text[] DEFAULT '{}'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_voc_records_topics
        ON voc_records USING gin(topics)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_voc_records_topics")
    op.execute("ALTER TABLE voc_records DROP COLUMN IF EXISTS topics")
