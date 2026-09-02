"""release_dates_backfill — 출시 완료된 최신 모델 released_at 채움 + GS25 연도 오류 수정.

배경:
  0009 가 당시 미출시 예정이던 GS26 family·GZF8/GZFL8·GB4 family·GR2·GFE25 를
  "미정"으로 released_at NULL 유지했다. 2026-09 현재 모두 출시·활발히 수집 중
  (GZF8 28k VOC)인데 아무도 출시일을 backfill 하지 않아 14종이 NULL 로 남았다.
  released_at 은 라이프사이클·런칭윈도우 분석의 핵심이라 채운다.

  또한 GS25 family 가 2026-01-22 로 잘못 기록돼 있었다. S24=2024-01, GZF7=2025-07
  로 미뤄 S25 는 2025-01 이어야 하며(연간 cadence), 2026-01 이면 GS26 과 충돌하고
  today(2026-09) 기준 이미 8k VOC 인 S26 이 미래 제품이 되는 모순 → 2025-01-22 로 정정.

날짜 근거(실제 Samsung 스케줄, DB 규칙과 일치):
  - S26 family: 2026-01 Unpacked (S-시리즈 1월).
  - Fold8/Flip8/Watch9/Buds4/Buds4Pro/Ring2: 2026-07 Unpacked(폴더블·워치·버즈·링 동시).
  - 추정(월 단위): FE25(S25 FE)~2025-10, A57/A37/A27~2026-03, F25~2025-03.
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

# 출시 완료된 모델 — released_at NULL → 실제 출시일
FILLS = [
    # 확정(플래그십·실제 Unpacked 일정)
    ("GS26",   "2026-01-22"),
    ("GS26P",  "2026-01-22"),
    ("GS26U",  "2026-01-22"),
    ("GZF8",   "2026-07-25"),
    ("GZFL8",  "2026-07-25"),
    ("GW9",    "2026-07-25"),
    ("GB4",    "2026-07-25"),
    ("GB4P",   "2026-07-25"),
    ("GR2",    "2026-07-25"),
    # 추정(월 단위 근사 — 시리즈 cadence 기반)
    ("GFE25",  "2025-10-01"),
    ("GA57",   "2026-03-01"),
    ("GA37",   "2026-03-01"),
    ("GA27",   "2026-03-01"),
    ("GF25",   "2025-03-01"),
]

# 잘못 기록된 값 정정 (연도 오류) — 무조건 덮어씀
CORRECTIONS = [
    ("GS25",   "2025-01-22"),
    ("GS25P",  "2025-01-22"),
    ("GS25U",  "2025-01-22"),
]


def upgrade():
    # NULL 인 것만 채움(멱등 — 이후 수동 정정 보존)
    for code, date_iso in FILLS:
        op.execute(f"""
            UPDATE products SET released_at = DATE '{date_iso}'
             WHERE code = '{code}' AND released_at IS NULL
        """)
    # 연도 오류 정정 — 기존 잘못된 2026-01-22 만 정확히 타겟
    for code, date_iso in CORRECTIONS:
        op.execute(f"""
            UPDATE products SET released_at = DATE '{date_iso}'
             WHERE code = '{code}' AND released_at = DATE '2026-01-22'
        """)


def downgrade():
    for code, _ in FILLS:
        op.execute(f"UPDATE products SET released_at = NULL WHERE code = '{code}'")
    for code, _ in CORRECTIONS:
        op.execute(f"""
            UPDATE products SET released_at = DATE '2026-01-22'
             WHERE code = '{code}' AND released_at = DATE '2025-01-22'
        """)
