"""waybacknews platform seed — archive.org 아카이브 RSS 옛뉴스 backfill.

crawler/platforms/wayback_news.py 용 platform row. RSS-only(WP REST 막힌) 매체
(The Verge/Engadget/PhoneArena 등)의 옛 기사를 Wayback 아카이브 RSS 스냅샷으로
연도별 소급. region=GLOBAL.

Revision ID: 0024
Revises: 0023
"""
from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('waybacknews', 'Wayback News (아카이브 옛기사)', 'GLOBAL', 'https://web.archive.org', true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'waybacknews'")
