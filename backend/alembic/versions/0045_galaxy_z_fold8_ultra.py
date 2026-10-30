"""galaxy_z_fold8_ultra — 8세대 폴드 둘 가운데 Fold8 Ultra 를 카탈로그에 따로 둔다.

배경 (2026-10-31):
  8세대 폴드는 2026-07 에 둘이 나왔다 — Fold7 의 직접 후속인 Galaxy Z Fold8 Ultra 와 넓은 폼팩터의 Galaxy Z
  Fold8. 카탈로그에는 GZF8(Galaxy Z Fold8) 하나뿐이라 매처가 'Fold8 Ultra' 도 GZF8 로 이었고, 두 기기의
  VOC 가 한 코드로 섞였다(dev 말뭉치 — 'Fold8 Ultra' 언급 9,717건). 매처(base/product_match)에 GZF8U 를 넣고
  지난 글은 scripts/retag_split 으로 가른다.

released_at — 두 기기 모두 2026-07 Unpacked(GZF8 과 같은 날, 0027 참조). 월별 언급도 같은 달에 솟는다.
predecessor_code — Fold7 의 직접 후속이 Ultra 다. GZF8 의 앞 세대(GZF7)는 그대로 둔다 — 넓은 폼팩터의 앞
세대를 비울지는 결함 급등 기준선·라이프사이클 비교가 바뀌는 결정이라 따로 묻는다.

Revision ID: 0045
Revises: 0044
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# (code, series_code, name_en, name_ko, released_at, predecessor_code)
PRODUCTS = [
    ("GZF8U", "GZ", "Galaxy Z Fold8 Ultra", "갤럭시 Z 폴드8 울트라", "2026-07-25", "GZF7"),
]


def upgrade():
    conn = op.get_bind()
    for code, series, en, ko, rel, pred in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at, predecessor_code,
                     is_active, brand, category, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, true, 'samsung', 'phone', now())
                ON CONFLICT (code) DO NOTHING
            """),
            # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다
            {"c": code, "s": series, "en": en, "ko": ko,
             "rel": date.fromisoformat(rel), "pred": pred},
        )


def downgrade():
    conn = op.get_bind()
    # 링크·VOC 가 붙은 행은 지우지 않는다(참조 무결성) — 다시 태깅을 되돌린 뒤에만 지워진다
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
