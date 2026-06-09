"""C1 데이터 정리 — voc_records.archived_at + 활성/아카이브 인덱스."""
from __future__ import annotations
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE voc_records ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_voc_archived_at "
        "ON voc_records (archived_at) WHERE archived_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_voc_active_collected "
        "ON voc_records (collected_at) WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_voc_active_collected")
    op.execute("DROP INDEX IF EXISTS ix_voc_archived_at")
    op.execute("ALTER TABLE voc_records DROP COLUMN IF EXISTS archived_at")
