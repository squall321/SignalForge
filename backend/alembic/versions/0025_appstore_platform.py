"""appstore platform seed — Google Play + Apple App Store Samsung 앱 리뷰.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
revision = "0025"; down_revision = "0024"; branch_labels = None; depends_on = None
def upgrade() -> None:
    op.execute("""INSERT INTO platforms (code, name, region, base_url, is_active)
        VALUES ('appstore', 'App Store Reviews (Play+Apple)', 'GLOBAL', 'https://play.google.com', true)
        ON CONFLICT (code) DO NOTHING""")
def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE code = 'appstore'")
