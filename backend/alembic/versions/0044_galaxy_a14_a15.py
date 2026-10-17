"""galaxy_a14_a15 — 레거시 사전에는 있는데 카탈로그에 없던 Galaxy A14·A15 를 등록한다.

배경 (2026-10-17):
  구형 모델 폴백(51e69a5)으로 미태깅 66만 행을 다시 판정했을 때 GA14·GA15 가 1,293건
  나왔지만 products 에 코드가 없어 태깅하지 못했다. 둘 다 판매량이 큰 2023년 보급형이다.
  레거시 사전(scripts/relink_products.MODEL_MAP)은 처음부터 두 코드를 냈는데 카탈로그만
  빠져 있었다 — 레거시 코드 381종 중 카탈로그에 없던 것은 이 둘뿐이다.

is_active=false — 0010 관례(신규 수집 대상이 아닌 옛 모델은 false).
predecessor_code — A13 → A14 → A15 → A16 사슬을 잇는다(A16 은 A15 가 없어 비어 있었다).

Revision ID: 0044
Revises: 0043
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

# (code, series_code, name_en, name_ko, released_at, predecessor_code)
PRODUCTS = [
    ("GA14", "GA", "Galaxy A14", "갤럭시 A14", "2023-01-04", "GA13"),
    ("GA15", "GA", "Galaxy A15", "갤럭시 A15", "2023-12-11", "GA14"),
]


def upgrade():
    conn = op.get_bind()
    for code, series, en, ko, rel, pred in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at, predecessor_code,
                     is_active, brand, category, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, false, 'samsung', 'phone', now())
                ON CONFLICT (code) DO NOTHING
            """),
            # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다
            {"c": code, "s": series, "en": en, "ko": ko,
             "rel": date.fromisoformat(rel), "pred": pred},
        )
    conn.execute(sa.text(
        "UPDATE products SET predecessor_code = 'GA15' "
        "WHERE code = 'GA16' AND predecessor_code IS NULL"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE products SET predecessor_code = NULL "
        "WHERE code = 'GA16' AND predecessor_code = 'GA15'"))
    # 링크·VOC 가 붙은 행은 지우지 않는다(참조 무결성)
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
