"""defect_modality — 결함 레코드에 양상(1인칭 고장/우려/질문/전언/리뷰) 컬럼 추가.

배경:
  실데이터 150건을 통독해 라벨한 결과 결함으로 집계된 문서 중 **firsthand(본인이 실제
  겪은 고장)는 49.3%뿐**이었다. 나머지는 리뷰·구매 전 우려·질문·전언이다. 매체(media)
  27건은 firsthand 가 0건으로 전부 스펙·할인 기사였다.

  실제로 급등 알림 근거를 열어보면 "Hinge Concerns (Possible New Owner)",
  "I'm considering getting the Fold 8, but concerned about IP48 dust rating" 같은
  **구매 전 우려**와 iFixit 분해 기사 재게시가 다수였다. 증상 렉시콘 정밀도를 올려도
  이 층은 걸러지지 않는다 — 문장은 진짜 결함 표현이고 화자의 입장만 다르기 때문이다.

  nlp/modality.py 가 CORE(자기기기×실현증상) − VETO(비현실·전언·기사) 마진으로 판별한다
  (라벨 표본 실측 firsthand P=0.855 R=0.878 F1=0.867, 베이스라인 0.493).

주의: 이 값은 **집계 필터용**이지 삭제 근거가 아니다. P=0.86 은 7건 중 1건 오분류라
원본을 지우기엔 부족하다. 볼륨·추이 지표는 전량을 유지하고 급등 판정에서만 firsthand 를 쓴다.
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("voc_defects", sa.Column("modality", sa.String(12), nullable=True))
    # 급등 탐지가 (modality, component, symptom) 으로 필터·집계한다
    op.create_index("ix_defect_modality", "voc_defects", ["modality"])


def downgrade():
    op.drop_index("ix_defect_modality", table_name="voc_defects")
    op.drop_column("voc_defects", "modality")
