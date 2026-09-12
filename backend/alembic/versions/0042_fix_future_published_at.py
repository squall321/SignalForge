"""fix_future_published_at — 미래 발행일 32건 보정 (quasarzone MM-DD 파싱 잔재).

배경:
  quasarzone 목록은 'MM-DD'(연도 없음) 표기다. 이를 올해로 가정해 파싱하면
  5월에 수집한 12월 글이 **미래 날짜**가 된다. 파서에는 이미 가드가 있으나
  (a766831, "미래면 작년"), 그 이전에 수집된 32건이 남아 있었다.

  평소에는 드러나지 않다가 MCP search_voc 를 최신순 정렬로 바꾸자
  이 32건이 검색 결과 최상단을 차지했다 — 오늘이 2026-09-12 인데
  2026-12-27·12-22·12-18 이 1위로 올라왔다.

검증:
  · 32건 전부 collected_at = 2026-05-30 단일 일자 (가드 이후 신규 유입 0)
  · 1년을 빼면 32/32 가 collected_at 이전이 된다 (still_bad 0)
    → 파서의 현행 로직(미래면 작년)과 정확히 같은 보정이다
  · 소스는 100% quasarzone
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    res = conn.execute(sa.text("""
        UPDATE voc_records
           SET published_at = published_at - interval '1 year'
         WHERE published_at > now()
           AND published_at - interval '1 year' <= collected_at
    """))
    print(f"[0042] 미래 발행일 보정: {res.rowcount}행")
    # 보정 후에도 미래인 행이 남으면 파싱을 신뢰할 수 없으므로 NULL 로 둔다
    res2 = conn.execute(sa.text(
        "UPDATE voc_records SET published_at = NULL WHERE published_at > now()"))
    if res2.rowcount:
        print(f"[0042] 보정 불가로 NULL 처리: {res2.rowcount}행")


def downgrade():
    # 원본 값을 복원할 수 없다(어느 행을 보정했는지 기록하지 않았다). no-op.
    pass
