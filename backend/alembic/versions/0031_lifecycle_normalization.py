"""lifecycle_normalization — 세대 매핑(predecessor_code) + 라이프사이클 뷰.

배경:
  "폴드8 performance 부정 +195%" 같은 급등은 신제품이면 원래 나타난다. 출시 직후
  버즈와 초기 불량이 겹치기 때문이다. 진짜 이상 신호인지 보려면 **이전 세대의 같은
  주차**(폴드7 6주차 vs 폴드8 6주차)와 비교해야 한다. 0027 로 released_at 을 전 제품
  채워 이제 가능해졌다.

구성:
  1) products.predecessor_code — 직전 세대 코드. 코드가 PREFIX+숫자+SUFFIX 규칙이라
     숫자를 1 내려 카탈로그에 존재하면 연결한다(GZF8→GZF7, GS26U→GS25U, AP16P→AP15P).
  2) v_voc_lifecycle — voc_product_links 기반(Phase 1)이라 비교글 언급까지 포함한다.
     lifecycle_week = 출시일로부터 경과 주. 출시 이전 글(사전 루머)은 제외한다.
"""
from alembic import op
import sqlalchemy as sa
import re

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_CODE_RE = re.compile(r"^([A-Z]+?)(\d+)([A-Z]*)$")


def upgrade():
    op.add_column("products", sa.Column("predecessor_code", sa.String(32), nullable=True))

    # 코드 규칙으로 직전 세대 유도 (카탈로그에 실재하는 코드만 연결)
    conn = op.get_bind()
    codes = [r[0] for r in conn.execute(sa.text("SELECT code FROM products")).fetchall()]
    have = set(codes)
    for code in codes:
        m = _CODE_RE.match(code)
        if not m:
            continue
        prefix, num, suffix = m.group(1), int(m.group(2)), m.group(3)
        prev = f"{prefix}{num - 1}{suffix}"
        if prev in have and prev != code:
            conn.execute(
                sa.text("UPDATE products SET predecessor_code = :p WHERE code = :c"),
                {"p": prev, "c": code},
            )

    # 라이프사이클 뷰 — Phase 1 링크 기반이라 비교글 언급도 포함(role 로 필터 가능)
    op.execute("""
        CREATE VIEW v_voc_lifecycle AS
        SELECT
            l.voc_id,
            l.product_id,
            p.code              AS product_code,
            p.predecessor_code,
            l.role,
            v.published_at,
            p.released_at,
            (FLOOR(EXTRACT(EPOCH FROM (v.published_at - p.released_at)) / 604800))::int
                                AS lifecycle_week,
            v.sentiment_label
        FROM voc_product_links l
        JOIN voc_records v ON v.id = l.voc_id
        JOIN products     p ON p.id = l.product_id
        WHERE v.archived_at IS NULL
          AND v.published_at IS NOT NULL
          AND p.released_at IS NOT NULL
          AND v.published_at >= p.released_at
    """)


def downgrade():
    op.execute("DROP VIEW IF EXISTS v_voc_lifecycle")
    op.drop_column("products", "predecessor_code")
