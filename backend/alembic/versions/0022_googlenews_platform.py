"""종합뉴스 platform seed — Google News RSS 기반 삼성/갤럭시 메인스트림 보도.

crawler/platforms/googlenews.py 용 platform row. site 제한 없이 종합지(연합/조선/
Reuters/CNN 등)의 삼성 제품 이슈·결함·리콜 보도를 다국어로 수집. region=GLOBAL.

Revision ID: 0022
Revises: 0021
"""
from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('googlenews', 'Google News (종합뉴스)', 'GLOBAL', 'https://news.google.com', true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'googlenews'")
