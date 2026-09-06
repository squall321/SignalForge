"""voc_defects — VOC 본문에서 뽑은 (부품·증상·심각도) 구조화 결함 레코드.

배경:
  결함 분석의 최선이 "hinge 248 / dust 100" 같은 단어 카운트였다. 어떤 부품이 어떤
  증상을 내는지, 얼마나 심각한지가 없어 "폴드8 힌지에 먼지"를 "개폐부 이물 유입,
  기능저하" 로 보고할 수 없었다. nlp/defect_extract.py 가 삼중항을 추출해 여기 적재한다.

PK(voc_id, component, symptom) 으로 문서 내 중복을 자연 차단한다.
severity — safety > non_functional > degraded > cosmetic.
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voc_defects",
        sa.Column("voc_id", sa.BigInteger(),
                  sa.ForeignKey("voc_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("symptom", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("voc_id", "component", "symptom"),
        sa.CheckConstraint(
            "severity IN ('safety','non_functional','degraded','cosmetic')",
            name="ck_defect_severity"),
    )
    # 부품×증상 집계용 (결함 랭킹의 주 쿼리)
    op.create_index("ix_defect_comp_symp", "voc_defects", ["component", "symptom"])
    # 심각도 필터 (safety 만 뽑기 등)
    op.create_index("ix_defect_severity", "voc_defects", ["severity"])


def downgrade():
    op.drop_index("ix_defect_severity", table_name="voc_defects")
    op.drop_index("ix_defect_comp_symp", table_name="voc_defects")
    op.drop_table("voc_defects")
