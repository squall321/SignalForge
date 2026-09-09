"""product_brand — products.brand 컬럼 추가 (경쟁사 380종 유입으로 브랜드 차원이 필요해짐).

배경:
  0037 로 products 가 389→769종이 되면서 제품 랭킹 소비처(geo drilldown top_products,
  analytics 7일 랭킹, community 서비스 2곳)가 삼성과 경쟁사를 **구분 없이 섞어** 보여준다.
  삼성 VOC 대시보드에서 'Top products' 1위가 iPhone 17 로 뜨면 오해를 부른다.

  브랜드는 코드 접두사로 유도할 수 있지만, 소비처마다 접두사를 제각기 추측하게 두면
  crawler/base/product_match.py 의 _CODE_BRAND_PREFIX 와 어긋나기 시작한다.
  그래서 **DB 컬럼 하나를 단일 출처로** 둔다.

규칙: 가장 긴 접두사가 이긴다(GM=갤럭시M vs GMN=가민). 매칭 없으면 samsung.
      product_match._CODE_BRAND_PREFIX 와 동일한 표이며, 새 브랜드 추가 시 양쪽을 함께
      갱신해야 한다(crawler 쪽은 test_new_codes_resolve_to_own_brand 가 지킨다).
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# (코드 접두사, 브랜드) — 긴 접두사 우선으로 정렬돼 있다
PREFIXES = [
    ("BSE", "bose"),
    ("FTB", "fitbit"),
    ("GMN", "garmin"),
    ("JBR", "jabra"),
    ("RDW", "xiaomi"),
    ("AB", "apple"),
    ("AP", "apple"),
    ("AS", "asus"),
    ("AW", "apple"),
    ("AZ", "amazfit"),
    ("HO", "honor"),
    ("HW", "huawei"),
    ("MT", "motorola"),
    ("NK", "nokia"),
    ("NT", "nothing"),
    ("OO", "oppo"),
    ("OP", "oneplus"),
    ("PB", "google"),
    ("PC", "xiaomi"),
    ("PW", "google"),
    ("PX", "google"),
    ("RL", "realme"),
    ("RM", "xiaomi"),
    ("SN", "sony"),
    ("VV", "vivo"),
    ("XM", "xiaomi"),
]


def upgrade():
    op.add_column("products",
                  sa.Column("brand", sa.String(24), nullable=False,
                            server_default="samsung"))
    conn = op.get_bind()
    # 긴 접두사부터 적용하고, 이미 채워진(=더 구체적인 접두사가 잡은) 행은 건너뛴다
    for prefix, brand in PREFIXES:
        conn.execute(
            sa.text("UPDATE products SET brand = :b "
                    "WHERE code LIKE :p AND brand = 'samsung'"),
            {"b": brand, "p": f"{prefix}%"},
        )
    op.create_index("ix_products_brand", "products", ["brand"])


def downgrade():
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_column("products", "brand")
