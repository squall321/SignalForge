"""legacy_products — 옛 Galaxy/Apple/Pixel 디바이스 마스터 추가.

Track D — product_id NULL voc 재매칭의 전제조건.

배경:
  - 현 products 마스터 48개 — S22~S26, Z Fold/Flip 5~8, Watch 6~8/Ultra, Buds 2~4,
    Ring2, iPhone 14~16, Pixel 8~9 만 등록.
  - voc_records 의 product_id NULL = 106,224건, 본문 ≥ 20자 = 71,501건.
  - 옛 디바이스 (S1~S21, Note 1~20, Z Fold/Flip 1~4, Watch 1~5, Buds 1) 가
    products 에 없어 categorizer 매칭이 떨어져도 product_id 채울 곳이 없음.

설계:
  - 약 80여 종을 ON CONFLICT (code) DO UPDATE 로 멱등 삽입.
  - released_at NULL 이면 출시일자 채워 향후 연도 필터에도 활용.
  - is_active=False — 옛 모델은 신규 수집 대상 아님, 대시보드 필터에서 제외 가능.

Revision ID: 0008
Revises: 0007
"""
from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


# (code, series_code, name_en, name_ko, released_at, is_active)
LEGACY_PRODUCTS = [
    # Galaxy S 1~21
    ("GS1",     "GS",   "Galaxy S",          "갤럭시 S",          "2010-06-04", False),
    ("GS2",     "GS",   "Galaxy S2",         "갤럭시 S2",         "2011-05-01", False),
    ("GS3",     "GS",   "Galaxy S3",         "갤럭시 S3",         "2012-05-29", False),
    ("GS4",     "GS",   "Galaxy S4",         "갤럭시 S4",         "2013-04-27", False),
    ("GS5",     "GS",   "Galaxy S5",         "갤럭시 S5",         "2014-04-11", False),
    ("GS6",     "GS",   "Galaxy S6",         "갤럭시 S6",         "2015-04-10", False),
    ("GS6E",    "GS",   "Galaxy S6 Edge",    "갤럭시 S6 엣지",    "2015-04-10", False),
    ("GS7",     "GS",   "Galaxy S7",         "갤럭시 S7",         "2016-03-11", False),
    ("GS7E",    "GS",   "Galaxy S7 Edge",    "갤럭시 S7 엣지",    "2016-03-11", False),
    ("GS8",     "GS",   "Galaxy S8",         "갤럭시 S8",         "2017-04-21", False),
    ("GS8P",    "GS",   "Galaxy S8+",        "갤럭시 S8+",        "2017-04-21", False),
    ("GS9",     "GS",   "Galaxy S9",         "갤럭시 S9",         "2018-03-16", False),
    ("GS9P",    "GS",   "Galaxy S9+",        "갤럭시 S9+",        "2018-03-16", False),
    ("GS10",    "GS",   "Galaxy S10",        "갤럭시 S10",        "2019-03-08", False),
    ("GS10P",   "GS",   "Galaxy S10+",       "갤럭시 S10+",       "2019-03-08", False),
    ("GS10E",   "GS",   "Galaxy S10e",       "갤럭시 S10e",       "2019-03-08", False),
    ("GS105G",  "GS",   "Galaxy S10 5G",     "갤럭시 S10 5G",     "2019-04-05", False),
    ("GS20",    "GS",   "Galaxy S20",        "갤럭시 S20",        "2020-03-06", False),
    ("GS20P",   "GS",   "Galaxy S20+",       "갤럭시 S20+",       "2020-03-06", False),
    ("GS20U",   "GS",   "Galaxy S20 Ultra",  "갤럭시 S20 울트라",  "2020-03-06", False),
    ("GFE20",   "GS",   "Galaxy S20 FE",     "갤럭시 S20 FE",     "2020-10-02", False),
    ("GS21",    "GS",   "Galaxy S21",        "갤럭시 S21",        "2021-01-29", False),
    ("GS21P",   "GS",   "Galaxy S21+",       "갤럭시 S21+",       "2021-01-29", False),
    ("GS21U",   "GS",   "Galaxy S21 Ultra",  "갤럭시 S21 울트라",  "2021-01-29", False),
    ("GFE21",   "GS",   "Galaxy S21 FE",     "갤럭시 S21 FE",     "2022-01-11", False),
    # Galaxy Note 1~20
    ("GN1",     "GN",   "Galaxy Note",       "갤럭시 노트",        "2011-10-29", False),
    ("GN2",     "GN",   "Galaxy Note 2",     "갤럭시 노트 2",      "2012-09-26", False),
    ("GN3",     "GN",   "Galaxy Note 3",     "갤럭시 노트 3",      "2013-09-25", False),
    ("GN4",     "GN",   "Galaxy Note 4",     "갤럭시 노트 4",      "2014-09-26", False),
    ("GN5",     "GN",   "Galaxy Note 5",     "갤럭시 노트 5",      "2015-08-21", False),
    ("GN7",     "GN",   "Galaxy Note 7",     "갤럭시 노트 7",      "2016-08-19", False),
    ("GN8",     "GN",   "Galaxy Note 8",     "갤럭시 노트 8",      "2017-09-15", False),
    ("GN9",     "GN",   "Galaxy Note 9",     "갤럭시 노트 9",      "2018-08-24", False),
    ("GN10",    "GN",   "Galaxy Note 10",    "갤럭시 노트 10",     "2019-08-23", False),
    ("GN10P",   "GN",   "Galaxy Note 10+",   "갤럭시 노트 10+",    "2019-08-23", False),
    ("GN20",    "GN",   "Galaxy Note 20",    "갤럭시 노트 20",     "2020-08-21", False),
    ("GN20U",   "GN",   "Galaxy Note 20 Ultra", "갤럭시 노트 20 울트라", "2020-08-21", False),
    # Galaxy Z Fold 1~4
    ("GZF1",    "GZF",  "Galaxy Fold",       "갤럭시 폴드",        "2019-09-06", False),
    ("GZF2",    "GZF",  "Galaxy Z Fold2",    "갤럭시 Z 폴드2",     "2020-09-18", False),
    ("GZF3",    "GZF",  "Galaxy Z Fold3",    "갤럭시 Z 폴드3",     "2021-08-27", False),
    ("GZF4",    "GZF",  "Galaxy Z Fold4",    "갤럭시 Z 폴드4",     "2022-08-26", False),
    # Galaxy Z Flip 1~4
    ("GZFL1",   "GZFL", "Galaxy Z Flip",     "갤럭시 Z 플립",      "2020-02-14", False),
    ("GZFL3",   "GZFL", "Galaxy Z Flip3",    "갤럭시 Z 플립3",     "2021-08-27", False),
    ("GZFL4",   "GZFL", "Galaxy Z Flip4",    "갤럭시 Z 플립4",     "2022-08-26", False),
    # Galaxy Watch 1~5
    ("GW1",     "GW",   "Galaxy Watch",      "갤럭시 워치",        "2018-08-24", False),
    ("GWA",     "GW",   "Galaxy Watch Active", "갤럭시 워치 액티브", "2019-03-08", False),
    ("GWA2",    "GW",   "Galaxy Watch Active2", "갤럭시 워치 액티브2", "2019-09-23", False),
    ("GW3",     "GW",   "Galaxy Watch3",     "갤럭시 워치3",       "2020-08-06", False),
    ("GW4",     "GW",   "Galaxy Watch4",     "갤럭시 워치4",       "2021-08-27", False),
    ("GW5",     "GW",   "Galaxy Watch5",     "갤럭시 워치5",       "2022-08-26", False),
    ("GW5P",    "GW",   "Galaxy Watch5 Pro", "갤럭시 워치5 프로",  "2022-08-26", False),
    # Galaxy Buds 1
    ("GB1",     "GB",   "Galaxy Buds",       "갤럭시 버즈",        "2019-03-08", False),
    ("GBL",     "GB",   "Galaxy Buds Live",  "갤럭시 버즈 라이브", "2020-08-06", False),
    ("GBP",     "GB",   "Galaxy Buds Pro",   "갤럭시 버즈 프로",   "2021-01-14", False),
    # iPhone 4~13 (요청 옛 모델)
    ("AP11",    "AP",   "iPhone 11",         "아이폰 11",          "2019-09-20", False),
    ("AP12",    "AP",   "iPhone 12",         "아이폰 12",          "2020-10-23", False),
    ("AP13",    "AP",   "iPhone 13",         "아이폰 13",          "2021-09-24", False),
    # Pixel 5~7
    ("PX5",     "PX",   "Pixel 5",           "픽셀 5",             "2020-10-15", False),
    ("PX6",     "PX",   "Pixel 6",           "픽셀 6",             "2021-10-28", False),
    ("PX7",     "PX",   "Pixel 7",           "픽셀 7",             "2022-10-13", False),
    # Galaxy A 시리즈 보강 (자주 언급)
    ("GA50",    "GA",   "Galaxy A50",        "갤럭시 A50",         "2019-03-15", False),
    ("GA51",    "GA",   "Galaxy A51",        "갤럭시 A51",         "2020-01-09", False),
    ("GA52",    "GA",   "Galaxy A52",        "갤럭시 A52",         "2021-03-26", False),
    ("GA53",    "GA",   "Galaxy A53",        "갤럭시 A53",         "2022-04-01", False),
    ("GA54",    "GA",   "Galaxy A54",        "갤럭시 A54",         "2023-03-24", False),
    ("GA55",    "GA",   "Galaxy A55",        "갤럭시 A55",         "2024-03-11", False),
]


def upgrade() -> None:
    # ON CONFLICT (code) DO UPDATE — 기존 행 (예: GA56) 의 NULL released_at 도 채움.
    for code, series, name_en, name_ko, released_at, is_active in LEGACY_PRODUCTS:
        op.execute(
            f"""
            INSERT INTO products (code, series_code, name_en, name_ko, released_at, is_active)
            VALUES ('{code}', '{series}', '{name_en}', '{name_ko}',
                    '{released_at}'::date, {str(is_active).upper()})
            ON CONFLICT (code) DO UPDATE SET
                released_at = COALESCE(products.released_at, EXCLUDED.released_at),
                name_ko = COALESCE(products.name_ko, EXCLUDED.name_ko)
            """
        )


def downgrade() -> None:
    codes = ",".join(f"'{r[0]}'" for r in LEGACY_PRODUCTS)
    op.execute(f"DELETE FROM products WHERE code IN ({codes})")
