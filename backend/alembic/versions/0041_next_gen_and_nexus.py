"""next_gen_and_nexus — 미등록 차세대/구형 모델 10종 등록.

배경:
  코퍼스에 대량으로 언급되는데 카탈로그에 없던 모델들이다(2026-09-11 실측) —
    iPhone Fold      2,490   ← 루머가 아니라 실사용 토론
                             ("The weight of the iPhone Fold ruined it, 254 grams")
    iPhone 18         1,951  (18 Pro 1,511 · 18 Pro Max 599 · 18e 146)
    Galaxy Nexus      1,643  ← 2011년 삼성·구글 공동 제품인데 자사 카탈로그에 없었다
    Galaxy S27        1,640  (S27 Ultra 1,167 · S27+ 269)
    iPhone Air 2        195
  합계 약 8,000건이 태깅되지 않고 있었다.

선등록 관행:
  GS26(2026-01 출시)이 출시 전부터 등록돼 있던 것과 같은 방식이다. 미출시라도
  언급량이 크면 넣는다 — 출시창 데이터가 없으면 나중에 소급할 수 없기 때문이다
  (GS26 출시창이 118건인데 GZF8 은 24,677건이던 209배 격차가 그 결과였다).

제외한 것 — 언급이 적어 추세 분석이 불가능하다:
  S27 Edge 13 · Galaxy Z Flip 9 46 · Galaxy Watch 10 9 · Pixel 12 26.
  Galaxy Z Fold 9 는 191건으로 경계선이라 다음 라운드로 미룬다.

패턴 주의:
  APFOLD 는 'fold' 가 갤럭시와 겹치므로 iphone/apple 인접을 반드시 요구한다.
  검증 — 'Galaxy Z Fold 8 hinge dust' → GZF8, '폴더블 아이폰' → APFOLD (오염 0).
"""
import re
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_CODE_RE = re.compile(r"^([A-Z]+)(\d+)([A-Z]*)$")

# (code, brand, series_code, category, name_en, name_ko, released_at, predecessor)
PRODUCTS = [
    ("GS27",   "samsung", "GS", "phone", "Galaxy S27", "갤럭시 S27", "2027-01-20", "GS26"),
    ("GS27P",  "samsung", "GS", "phone", "Galaxy S27+", "갤럭시 S27 플러스", "2027-01-20", "GS26P"),
    ("GS27U",  "samsung", "GS", "phone", "Galaxy S27 Ultra", "갤럭시 S27 울트라", "2027-01-20", "GS26U"),
    ("GNEXUS", "samsung", "GN", "phone", "Galaxy Nexus", "갤럭시 넥서스", "2011-11-17", None),
    ("AP18",   "apple", "AP", "phone", "iPhone 18", "아이폰 18", "2027-03-01", "AP17"),
    ("AP18P",  "apple", "AP", "phone", "iPhone 18 Pro", "아이폰 18 Pro", "2026-09-15", "AP17P"),
    ("AP18PM", "apple", "AP", "phone", "iPhone 18 Pro Max", "아이폰 18 Pro Max", "2026-09-15", "AP17PM"),
    ("AP18E",  "apple", "AP", "phone", "iPhone 18e", "아이폰 18e", "2027-03-01", "AP17E"),
    ("APFOLD", "apple", "AP", "phone", "iPhone Fold", "아이폰 폴드", "2026-09-15", None),
    ("APAIR2", "apple", "AP", "phone", "iPhone Air 2", "아이폰 에어 2세대", "2027-03-01", "APAIR"),
]


def upgrade():
    conn = op.get_bind()
    for code, brand, series, cat, en, ko, rel, pred in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at,
                     predecessor_code, brand, category, is_active, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, :brand, :cat, true, now())
                ON CONFLICT (code) DO NOTHING
            """),
            # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다
            {"c": code, "s": series, "en": en, "ko": ko,
             "rel": date.fromisoformat(rel), "pred": pred,
             "brand": brand, "cat": cat},
        )

    # 0031·0037·0040 의 코드규칙 유도는 실행 시점 1회성이라 신규 행에 적용되지 않는다
    have = {r[0] for r in conn.execute(sa.text("SELECT code FROM products")).fetchall()}
    for code, *_ in PRODUCTS:
        m = _CODE_RE.match(code)
        if not m:
            continue
        prev = f"{m.group(1)}{int(m.group(2)) - 1}{m.group(3)}"
        if prev in have and prev != code:
            conn.execute(
                sa.text("UPDATE products SET predecessor_code = :p "
                        "WHERE code = :c AND predecessor_code IS NULL"),
                {"p": prev, "c": code},
            )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
