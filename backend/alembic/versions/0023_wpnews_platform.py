"""wpnews platform seed — WP REST 뉴스 옛기사 backfill.

crawler/platforms/wpnews.py 용 platform row. WordPress REST 로 9to5google/phandroid/
sammobile 의 삼성 옛 기사를 연도 슬라이싱 소급. region=GLOBAL.

Revision ID: 0023
Revises: 0022
"""
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('wpnews', 'WP News (옛기사 backfill)', 'GLOBAL', 'https://news.google.com', true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'wpnews'")
