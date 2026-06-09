"""Bluesky platform seed — Twitter 무료 대안 1순위.

Track D — Twitter API 비용 회피하면서 X.com VOC 일부라도 수집.
Bluesky AT Protocol (https://bsky.social) 은 무료 계정 1개로 검색 가능.

platforms 신규 row: bluesky
  - region: GLOBAL
  - is_active: false (초기) — 키 입력 후 운영자가 활성화
  - base_url: https://bsky.app

키 미입력 환경에서도 crawler 가 graceful skip 하지만, 굳이 비활성 상태에서
스케줄러가 도는 것을 막기 위해 platforms.is_active=false 로 두고 키 입력 후
docs/dashboard/TWITTER_ALTERNATIVES.md 에 따라 true 로 전환한다.

Revision ID: 0006
Revises: 0005
"""
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('bluesky', 'Bluesky', 'GLOBAL', 'https://bsky.app', false)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'bluesky'")
