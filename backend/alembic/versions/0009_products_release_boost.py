"""products_release_boost — 기존 48행 released_at 채움 + 옛 iPhone/Pixel 보강.

Track A R6 — 0008 후속.

배경:
  - 0008 (legacy_products) 가 옛 모델 66개 시드 → products 114 행, dated 66.
  - 그런데 기존 48행 (GS22~26, GFE23~25, Z 5~8, Watch 6~8, Buds 2~4,
    AP14~16, PX8~9, GR2) 은 여전히 released_at NULL.
  - 0008 의 ON CONFLICT 분기가 *옛 코드 INSERT* 시에만 활성화 → 기존 코드는
    EXCLUDED 대상이 아니라 한 번도 UPDATE 되지 않았다.
  - R6 컨텍스트: 옛 디바이스 글 6,324건 product_id NULL 매칭 + 시기별 비교
    필터에서 released_at 가 핵심. 모든 정해진 출시일을 채워야 한다.

설계:
  - 단순 UPDATE WHERE released_at IS NULL — 의도된 미정 모델 (GS26 family,
    GFE25, GZF8/GZFL8, GB4 family, GR2) 은 제외.
  - 0008 누락 항목 (옛 iPhone 6/7/8/X, 옛 Pixel 1~4) 추가 INSERT.

Revision ID: 0009
Revises: 0008
"""
from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


# ──────────────────────────────────────────────────────────────────────
# 1) 기존 48행 중 출시일 알려진 것들 — UPDATE released_at
#    (코드, ISO 날짜)
# ──────────────────────────────────────────────────────────────────────
UPDATES = [
    # Galaxy S22~25 (S26 family + GFE25 는 미정 → NULL 유지)
    ("GS22",   "2022-02-25"),
    ("GS22U",  "2022-02-25"),
    ("GS23",   "2023-02-17"),
    ("GS23P",  "2023-02-17"),
    ("GS23U",  "2023-02-17"),
    ("GFE23",  "2023-10-04"),
    ("GS24",   "2024-01-31"),
    ("GS24P",  "2024-01-31"),
    ("GS24U",  "2024-01-31"),
    ("GFE24",  "2024-10-03"),
    ("GS25",   "2026-01-22"),
    ("GS25P",  "2026-01-22"),
    ("GS25U",  "2026-01-22"),
    # Galaxy A56
    ("GA56",   "2025-03-14"),
    # Z Fold/Flip 5~7 (8세대는 미정 → NULL 유지)
    ("GZF5",   "2023-08-11"),
    ("GZFL5",  "2023-08-11"),
    ("GZF6",   "2024-07-24"),
    ("GZFL6",  "2024-07-24"),
    ("GZF7",   "2025-07-25"),
    ("GZFL7",  "2025-07-25"),
    # Watch 6~8 + Ultra
    ("GW6",    "2023-08-11"),
    ("GW7",    "2024-07-24"),
    ("GW8",    "2025-07-25"),
    ("GWU",    "2024-07-24"),
    # Buds 2~3 (Buds4 는 미정 → NULL 유지)
    ("GB2",    "2021-08-27"),
    ("GB2P",   "2022-08-26"),
    ("GB3",    "2024-07-24"),
    ("GB3P",   "2024-07-24"),
    # iPhone 14~16
    ("AP14",   "2022-09-16"),
    ("AP15",   "2023-09-22"),
    ("AP15P",  "2023-09-22"),
    ("AP15PM", "2023-09-22"),
    ("AP16",   "2024-09-20"),
    ("AP16P",  "2024-09-20"),
    ("AP16PM", "2024-09-20"),
    # Pixel 8~9
    ("PX8",    "2023-10-12"),
    ("PX8P",   "2023-10-12"),
    ("PX9",    "2024-08-22"),
    ("PX9P",   "2024-08-22"),
]


# ──────────────────────────────────────────────────────────────────────
# 2) 0008 에 누락된 옛 모델 — INSERT (ON CONFLICT 멱등)
#    spec R6 의 "옛 iPhone 6/7/8/X" + "옛 Pixel 1~4" 보강.
# ──────────────────────────────────────────────────────────────────────
INSERTS = [
    # iPhone 옛 (6, 7, 8, X)
    ("AP6",  "AP", "iPhone 6", "아이폰 6", "2014-09-19"),
    ("AP7",  "AP", "iPhone 7", "아이폰 7", "2016-09-16"),
    ("AP8",  "AP", "iPhone 8", "아이폰 8", "2017-09-22"),
    ("AP10", "AP", "iPhone X", "아이폰 X", "2017-11-03"),
    # Pixel 옛 (1~4)
    ("PX1",  "PX", "Pixel",   "픽셀",   "2016-10-20"),
    ("PX2",  "PX", "Pixel 2", "픽셀 2", "2017-10-19"),
    ("PX3",  "PX", "Pixel 3", "픽셀 3", "2018-10-18"),
    ("PX4",  "PX", "Pixel 4", "픽셀 4", "2019-10-22"),
]


def upgrade() -> None:
    # 1) UPDATE 기존 48행 — released_at NULL → ISO 날짜
    for code, date_iso in UPDATES:
        op.execute(
            f"""
            UPDATE products
               SET released_at = DATE '{date_iso}'
             WHERE code = '{code}'
               AND released_at IS NULL
            """
        )

    # 2) INSERT 옛 모델 (0008 누락분)
    for code, series, name_en, name_ko, date_iso in INSERTS:
        # name_ko 작은 따옴표 없음 — escape 생략
        op.execute(
            f"""
            INSERT INTO products
                (code, series_code, name_en, name_ko, released_at, is_active)
            VALUES
                ('{code}', '{series}', '{name_en}', '{name_ko}',
                 DATE '{date_iso}', false)
            ON CONFLICT (code) DO UPDATE SET
                released_at = COALESCE(products.released_at, EXCLUDED.released_at),
                name_ko     = COALESCE(products.name_ko,     EXCLUDED.name_ko)
            """
        )


def downgrade() -> None:
    # INSERT 분 삭제 — 0009 가 새로 만든 옛 모델만.
    new_codes = ",".join(f"'{r[0]}'" for r in INSERTS)
    op.execute(f"DELETE FROM products WHERE code IN ({new_codes})")
    # UPDATE 분은 released_at 을 NULL 로 되돌림 — 정보 손실이지만
    # downgrade 의미상 정합 (기존 48행이 원래 NULL 이었으므로).
    codes_sql = ",".join(f"'{c}'" for c, _ in UPDATES)
    op.execute(
        f"UPDATE products SET released_at = NULL WHERE code IN ({codes_sql})"
    )
