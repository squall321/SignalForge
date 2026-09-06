"""voc_product_links — VOC↔제품 다대다 링크 (비교글 신호 확보).

배경:
  voc_records.product_id 는 1행 1제품이라 "S26 Ultra vs Fold8" 같은 비교글이
  먼저 매칭된 쪽으로만 잡힌다. 실측으로 Fold8 언급 글이 GS26U 1,280건·GS26 402건·
  GS25U 310건에 묻혀 있었고, iPhone 은 언급 30,927건 중 태깅 2,896건뿐이라
  경쟁사 분석이 사실상 불가능했다.

설계(비파괴):
  product_id 는 **primary 제품으로 그대로 유지**한다. 백엔드 20개 파일과 MV 7종이
  이 컬럼에 의존해 컬럼을 옮기면 회귀 위험이 크다. 다대다는 이 테이블로 추가하고
  새 분석만 여기를 쓴다. primary 링크도 함께 저장해 이 테이블만으로 완결되게 한다.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voc_product_links",
        sa.Column("voc_id", sa.BigInteger(),
                  sa.ForeignKey("voc_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(),
                  sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        # primary = 우선순위 최상위 매칭(=voc_records.product_id 와 일치)
        # compared = 비교 마커(vs/대비/비교) 있는 글의 non-primary
        # mentioned = 그 외 non-primary
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("voc_id", "product_id"),
        sa.CheckConstraint("role IN ('primary','compared','mentioned')",
                           name="ck_vpl_role"),
    )
    # "제품 X 를 언급한 VOC 전부" 조회용 (역할 필터 포함)
    op.create_index("ix_vpl_product_role", "voc_product_links", ["product_id", "role"])


def downgrade():
    op.drop_index("ix_vpl_product_role", table_name="voc_product_links")
    op.drop_table("voc_product_links")
