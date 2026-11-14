"""catalog_fix_2026_11_14 — Watch Ultra2 를 넣고 자리표시로 남은 출시일 넷을 판매일로 바로잡는다.

배경 (2026-11-14, 웨어러블 경쟁사 조사의 VOC 분석):
  · Galaxy Watch Ultra2 가 카탈로그에 없어 'Watch Ultra 2' 글이 1세대 GWU 로 갔다(2026-07 이후 GWU 글의 71%).
  · GB4 · GB4P · GW9 · GR2 가 모두 2026-07-25 — 출시 전에 넣은 자리표시다. 판매일은 웹으로 확인했다 —
    Buds4 · Buds4 Pro 2026-03-11(02-25 예약, 삼성 뉴스룸 · SamMobile), Watch9 · Watch Ultra2 2026-08-07(07-22 언팩,
    삼성 일본 · TechRepublic). Galaxy Ring 후속작은 아직 출시 전이라(2027년 초 보도) 비운다.

released_at 은 판매일이다(GW8 2025-07-25 와 같다). predecessor_code — GWU2 는 GWU 를 잇는다.

Revision ID: 0048
Revises: 0047
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

# (code, series_code, name_en, name_ko, released_at, predecessor_code, is_active, category)
PRODUCTS = [
    ("GWU2", "GW", "Galaxy Watch Ultra2", "갤럭시 워치 울트라2", "2026-08-07", "GWU", True, "watch"),
]

# code → (바로잡은 판매일, 예전 자리표시)
RELEASED = {
    "GB4":  ("2026-03-11", "2026-07-25"),
    "GB4P": ("2026-03-11", "2026-07-25"),
    "GW9":  ("2026-08-07", "2026-07-25"),
    "GR2":  (None,         "2026-07-25"),
}


def _d(s):
    return date.fromisoformat(s) if s else None   # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다


def upgrade():
    conn = op.get_bind()
    for code, series, en, ko, rel, pred, active, cat in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at, predecessor_code,
                     is_active, brand, category, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, :act, 'samsung', :cat, now())
                ON CONFLICT (code) DO NOTHING
            """),
            {"c": code, "s": series, "en": en, "ko": ko, "rel": _d(rel), "pred": pred, "act": active, "cat": cat},
        )
    for code, (new, _old) in RELEASED.items():
        conn.execute(sa.text("UPDATE products SET released_at = :rel WHERE code = :c"), {"c": code, "rel": _d(new)})


def downgrade():
    conn = op.get_bind()
    for code, (_new, old) in RELEASED.items():
        conn.execute(sa.text("UPDATE products SET released_at = :rel WHERE code = :c"), {"c": code, "rel": _d(old)})
    # 링크 · VOC 가 붙은 행은 지우지 않는다(참조 무결성) — 다시 태깅을 되돌린 뒤에만 지워진다
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
