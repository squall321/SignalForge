"""Track C — youtube platform seed.

YouTube 댓글 수집기(crawler/platforms/youtube_comments.py)용 platform row.
Galaxy 리뷰/쇼츠 영상의 시청자 댓글을 YouTube Data API v3 로 수집한다.
region=GLOBAL, is_active=true. YOUTUBE_API_KEY 미설정 시 크롤러가 graceful skip.

Revision ID: 0020
Revises: 0019
"""
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('youtube', 'YouTube', 'GLOBAL', 'https://www.youtube.com', true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'youtube'")
