"""R14 트랙 A — voc_records.content_hash + uniqueness.

배경 (R13 권고 1):
- ON CONFLICT (platform_id, external_id) DO NOTHING 로는 본문 중복을 막지 못함.
- Discovery 결과: dcinside 29,920건 / ppomppu 5,789건 / clien 1,198건 본문 중복.
- 24h 윈도우 중복률 27.91% (R13 5.87% 대비 급등).

조치:
1. content_hash 컬럼 추가 (sha256(content_original) hex 첫 16자).
2. 기존 행 일괄 채움.
3. (platform_id, content_hash) 부분 UNIQUE INDEX 추가
   — content_hash IS NOT NULL 인 행에만 적용 (NULL/짧은 본문은 허용).

Revision ID: 0016
Revises: 0015
"""
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 컬럼 추가
    op.execute("ALTER TABLE voc_records ADD COLUMN IF NOT EXISTS content_hash text")

    # 2. 기존 행 채움 (sha256 첫 16자) — content_original 30자 이상만
    op.execute(
        """
        UPDATE voc_records
        SET content_hash = substr(
            encode(sha256(convert_to(content_original, 'UTF8')), 'hex'),
            1, 16
        )
        WHERE content_hash IS NULL
          AND content_original IS NOT NULL
          AND length(content_original) >= 30
        """
    )

    # 3. 부분 UNIQUE INDEX
    # NOTE: 마이그레이션은 기존 중복을 정리하지 않음 — dedup_voc.py 가 별도 수행.
    # 따라서 인덱스 생성 단계에서 충돌이 나면 dedup 먼저 수행해야 함.
    # 안전을 위해 일반 인덱스로 생성 후, dedup 스크립트가 끝나면 UNIQUE 로 승격.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_voc_content_hash
            ON voc_records (platform_id, content_hash)
            WHERE content_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_voc_platform_content_hash")
    op.execute("DROP INDEX IF EXISTS idx_voc_content_hash")
    op.execute("ALTER TABLE voc_records DROP COLUMN IF EXISTS content_hash")
