"""clean_error_page_translations — 번역기가 저장한 에러 페이지 텍스트 제거.

배경:
  content_translated 에 Google 의 오류 페이지("Error 500 (Server Error)!!1500.
  That's an error...")가 그대로 저장된 행이 dev 기준 3,887건 있었다. 원문은 정상적인
  한국어 결함 제보였다("s26 울트라 디스플레이 하자" 등). 이 값이 남아 있으면
  감성·카테고리·결함 추출이 전부 에러 텍스트 기준으로 계산돼 분석이 오염되고,
  교차플랫폼 근사중복 탐지에서도 이 3,887건이 서로 "동일 문서"로 잡힌다.

조치:
  content_translated 를 NULL 로 되돌린다. 그러면 번역 백로그 대상으로 다시 잡혀
  translate_backlog 가 정상 번역을 채운다(원문은 손상되지 않았으므로 복구 가능).
  오염 본문에서 뽑힌 voc_defects 레코드도 함께 삭제한다 — 재추출 대상이 된다.
  재발 방지는 nlp/translator.py 의 _looks_like_error_page 가드가 담당한다.
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

# 앞부분만 검사 — 뒤에 무엇이 붙든 오류 페이지면 걸린다.
_COND = (
    "content_translated LIKE 'Error 5%' "
    "OR content_translated LIKE 'Error 4%' "
    "OR content_translated LIKE '%That''s an error%' "
    "OR content_translated LIKE '<!DOCTYPE%' "
    "OR content_translated LIKE '<html%'"
)


def upgrade():
    # 오염 본문에서 추출된 결함 레코드 먼저 제거(재추출 대상)
    op.execute(f"""
        DELETE FROM voc_defects
         WHERE voc_id IN (SELECT id FROM voc_records WHERE {_COND})
    """)
    op.execute(f"""
        UPDATE voc_records
           SET content_translated = NULL
         WHERE {_COND}
    """)


def downgrade():
    # 오염 값 복원은 무의미(원래 잘못된 데이터) → no-op.
    pass
