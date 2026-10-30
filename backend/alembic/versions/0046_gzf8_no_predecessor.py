"""gzf8_no_predecessor — 넓은 폼팩터 Galaxy Z Fold8(GZF8)은 앞 세대 없는 새 계열로 둔다.

배경 (2026-10-31):
  Fold7 의 직접 후속은 Galaxy Z Fold8 Ultra(GZF8U, 0045 — 앞 세대 GZF7)다. 넓은 폼팩터의 Galaxy Z Fold8 은 Fold7 을
  잇지 않는다. 0031 이 번호 규칙(숫자를 1 내려 카탈로그에 있으면 잇는다)으로 GZF8 의 앞 세대를 GZF7 로 두어, 결함 급등
  기준선 · 라이프사이클 주차 비교(mcp tools/defects — released_at + predecessor_code)가 넓은 폼팩터를 Fold7 과 견주었다.
  이제 GZF7 을 잇는 것은 GZF8U 뿐이다.

Revision ID: 0046
Revises: 0045
"""
import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execute(sa.text(
        "UPDATE products SET predecessor_code = NULL WHERE code = 'GZF8' AND predecessor_code = 'GZF7'"))


def downgrade():
    op.get_bind().execute(sa.text(
        "UPDATE products SET predecessor_code = 'GZF7' WHERE code = 'GZF8' AND predecessor_code IS NULL"))
