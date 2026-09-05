"""clear_gw1_mistags — GW1(2018 워치1) 오태깅 잔재 제거.

product_match 에서 GW1 패턴은 이미 제거됐으나, 과거 generic 'Galaxy Watch' 를
GW1 로 흡수하던 옛 패턴이 태깅한 잔재가 DB 에 남아 있다(dev 82건 실측, 전부 재추론
결과 None = generic 워치 잡담). GW1 은 현재 어떤 패턴으로도 태깅되지 않으므로
이 코드로 태깅된 행은 모두 오태깅 → product_id NULL 로 되돌린다(멱등).
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE voc_records
           SET product_id = NULL
         WHERE product_id = (SELECT id FROM products WHERE code = 'GW1')
    """)


def downgrade():
    # 오태깅 복원은 무의미(원래 잘못된 값) → no-op.
    pass
