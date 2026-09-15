"""crawl_jobs.items_fetched — 긁은 건수와 저장한 건수를 구분한다.

배경:
  crawl_jobs 는 items_collected(=신규 저장 건수)만 기록했다. 그래서 헬스 체크가
  "아무것도 못 긁었다"와 "긁었지만 전부 중복이었다"를 구분하지 못했다.

  실측(2026-09-15) — ifixit 는 700건을 긁는데 전부 기존 글이라 신규 저장이 0이고,
  그 결과 "48h 동안 12회 실행했으나 수집 0건 — 은퇴가 아니라 고장이다"로 경보가
  떴다. 정상 동작하는 소스다. 같은 이유로 lowyat·stackexchange·techinafrica·
  hackerone·recalls 도 오경보였다.

  이 소음이 진짜 장애를 묻는다. androidcentral 은 봇 차단벽에 막혀 **정말로**
  0건이었는데 같은 문구로 35일간 리포트에 떠 있었고 아무도 움직이지 않았다.

  items_fetched 를 따로 기록하면 둘을 가를 수 있다 —
    fetched 0  · collected 0  → 못 긁었다. 고장이거나 차단이다.
    fetched >0 · collected 0  → 긁었는데 신규가 없다. 정상이다.
"""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crawl_jobs", sa.Column("items_fetched", sa.Integer(), nullable=True))
    # 기존 행은 알 수 없으므로 NULL 로 둔다. 헬스 체크는 NULL 을 "모름"으로
    # 취급해 기존 문구를 쓴다 — 없는 정보를 있는 척하지 않는다.


def downgrade():
    op.drop_column("crawl_jobs", "items_fetched")
