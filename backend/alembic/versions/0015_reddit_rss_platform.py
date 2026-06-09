"""R12 트랙 B — reddit_rss platform seed.

Reddit OAuth 차단 우회용 RSS 크롤러 신규 platform.

배경:
- 기존 'reddit' (id=1) 는 OAuth 의존이며, REDDIT_CLIENT_ID/SECRET 미입력 시 skip.
- 본 row 는 https://www.reddit.com/r/<sub>.rss 무인증 Atom feed 를 사용하는
  reddit_rss 크롤러용 별도 platform. region=GLOBAL, is_active=true.

Revision ID: 0015
Revises: 0014
"""
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('reddit_rss', 'Reddit (RSS)', 'GLOBAL', 'https://www.reddit.com', true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'reddit_rss'")
