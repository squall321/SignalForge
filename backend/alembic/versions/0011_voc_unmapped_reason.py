"""voc_unmapped_reason — Track E NULL % 정책 재정의.

Track E (2026-06-04) — product_id NULL 후기를 단일 "미커버" 로 묶지 말고
실패 원인별로 분리하여 운영 metric 재정의.

배경:
  - 161,491 voc / 32,065 linked / 129,426 NULL (80.14%).
  - 그러나 NULL 중 다수는 "모델 미언급 일반 후기" 거나 "스팸/잠금" 이라
    매핑 사전을 더 키워도 매칭 불가.  진정한 "분석 가능" 데이터 비율을 측정하려면
    NULL 원인 분리가 필요.

설계:
  - ``voc_records.unmapped_reason`` text NULL 컬럼 추가.
  - 값 ENUM (CHECK 없이 문자열로 유연하게):
      * ``no_model_mention`` — 본문에 모델명 언급 없음 (정상 후기지만 매핑 불가)
      * ``noise``            — 잠금/회원전용/삭제된 글 등 스팸 패턴
      * ``too_short``        — 본문 < 10자
      * ``non_galaxy``       — iPhone/Pixel/Xiaomi 만 언급 (Samsung 컨텍스트 부재)
      * ``unknown`` (NULL)   — 분류기 미실행 또는 매핑 가능했어야 함
  - 인덱스: ``idx_voc_unmapped_reason`` partial (NULL 제외) — coverage-status 집계용.
  - 멱등: 컬럼/인덱스 IF NOT EXISTS.

Revision ID: 0011
Revises: 0010
"""
from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE voc_records
            ADD COLUMN IF NOT EXISTS unmapped_reason text
        """
    )
    # product_id IS NOT NULL 행은 reason 이 무의미하므로 partial index.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_voc_unmapped_reason
            ON voc_records (unmapped_reason)
            WHERE product_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_voc_unmapped_reason")
    op.execute("ALTER TABLE voc_records DROP COLUMN IF EXISTS unmapped_reason")
