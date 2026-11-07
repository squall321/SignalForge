"""catalog_gaps_2026_11 — 말뭉치가 자주 말하는데 카탈로그에 없던 삼성 기기 25종.

배경 (2026-11-07):
  카탈로그 공백은 오귀속으로 나타난다(2026-09-09 교훈). 이번 실측 —
  · Galaxy Tab S10 Lite 글 383행이 'tab s10' 패턴에 걸려 Tab S10(GTABS10)으로 갔고 262행은 미태깅이었다.
  · S25 Edge 는 GS25 로, Z Flip7 FE 는 GZFL7 로, Buds3 FE 는 GB3 로, Tab A11+ 는 GTABA11 로, XCover7 Pro 는 GXC7 로
    흡수됐다(라이브 매칭 바로 뒤의 변형어 실측 — 표본 15만 행에서 각 478 · 68 · 168 · 100 · 43).
  · 미태깅 행의 모델 표기 빈도 — Tab S12 Ultra/+ 약 360 · A18 176 · A06 146 · A05/A05s 135 · A08 98 · M47 90 ·
    F70/F70 Pro/F70e 105 · M35 68 · S26 FE 54 · A04 계열 47 · M56 35 · Fit 4 33 · M36 29.
  출시일은 웹으로 확인했다(제조사 발표 · GSMArena · 위키백과 · 인도 출시 기사). 월만 확인된 것은 그달 1일(A06 · F70).
  Galaxy Fit 4 는 아직 출시 전이라(2026-10 인증) GS27 처럼 미리 넣고 출시일은 비운다.

released_at — 위 확인값. predecessor_code — 같은 라인의 바로 앞 세대(Tab S10 Lite 는 Tab S6 Lite 를 잇는다).
is_active — 2024 년 이후 기기는 true(GA16 · GM55 와 같다), 2022~23 년 A04 · A05 계열은 false(GA14 · GA15 와 같다).

Revision ID: 0047
Revises: 0046
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# (code, series_code, name_en, name_ko, released_at, predecessor_code, is_active, category) — 앞 세대가 먼저 오게 둔다
PRODUCTS = [
    ("GTS10L",   "TABS", "Galaxy Tab S10 Lite",  "갤럭시 탭 S10 Lite",    "2025-09-04", "GTS6L",    True,  "tablet"),
    ("GTABS12U", "TABS", "Galaxy Tab S12 Ultra", "갤럭시 탭 S12 울트라",   "2026-10-07", "GTABS11U", True,  "tablet"),
    ("GTS12P",   "TABS", "Galaxy Tab S12+",      "갤럭시 탭 S12+",        "2026-10-07", "GTS11P",   True,  "tablet"),
    ("GTABA11P", "TABA", "Galaxy Tab A11+",      "갤럭시 탭 A11+",        "2025-11-02", None,       True,  "tablet"),
    ("GFE26",    "GS",   "Galaxy S26 FE",        "갤럭시 S26 FE",         "2026-09-04", "GFE25",    True,  "phone"),
    ("GS25E",    "GS",   "Galaxy S25 Edge",      "갤럭시 S25 엣지",        "2025-05-30", None,       True,  "phone"),
    ("GZFL7FE",  "GZ",   "Galaxy Z Flip7 FE",    "갤럭시 Z 플립7 FE",      "2025-07-25", None,       True,  "phone"),
    ("GB3FE",    "GB",   "Galaxy Buds3 FE",      "갤럭시 버즈3 FE",        "2025-09-05", None,       True,  "buds"),
    ("GXC7P",    "GXC",  "Galaxy XCover7 Pro",   "갤럭시 엑스커버7 프로",   "2025-05-08", None,       True,  "phone"),
    ("GA04",     "GA",   "Galaxy A04",           "갤럭시 A04",            "2022-08-24", "GA03",     False, "phone"),
    ("GA04S",    "GA",   "Galaxy A04s",          "갤럭시 A04s",           "2022-08-31", "GA03S",    False, "phone"),
    ("GA04E",    "GA",   "Galaxy A04e",          "갤럭시 A04e",           "2022-10-21", None,       False, "phone"),
    ("GA05",     "GA",   "Galaxy A05",           "갤럭시 A05",            "2023-10-15", "GA04",     False, "phone"),
    ("GA05S",    "GA",   "Galaxy A05s",          "갤럭시 A05s",           "2023-10-18", "GA04S",    False, "phone"),
    ("GA06",     "GA",   "Galaxy A06",           "갤럭시 A06",            "2024-08-01", "GA05",     True,  "phone"),
    ("GA08",     "GA",   "Galaxy A08",           "갤럭시 A08",            "2026-09-15", "GA07",     True,  "phone"),
    ("GA18",     "GA",   "Galaxy A18",           "갤럭시 A18",            "2026-09-18", "GA17",     True,  "phone"),
    ("GM35",     "GM",   "Galaxy M35",           "갤럭시 M35",            "2024-07-17", "GM34",     True,  "phone"),
    ("GM36",     "GM",   "Galaxy M36",           "갤럭시 M36",            "2025-06-27", "GM35",     True,  "phone"),
    ("GM47",     "GM",   "Galaxy M47",           "갤럭시 M47",            "2026-06-29", None,       True,  "phone"),
    ("GM56",     "GM",   "Galaxy M56",           "갤럭시 M56",            "2025-04-17", "GM55",     True,  "phone"),
    ("GF70",     "GF",   "Galaxy F70",           "갤럭시 F70",            "2026-10-01", None,       True,  "phone"),
    ("GF70P",    "GF",   "Galaxy F70 Pro",       "갤럭시 F70 프로",        "2026-08-03", None,       True,  "phone"),
    ("GF70E",    "GF",   "Galaxy F70e",          "갤럭시 F70e",           "2026-02-09", None,       True,  "phone"),
    ("GFIT4",    "GW",   "Galaxy Fit4",          "갤럭시 핏4",             None,         "GFIT3",    True,  "band"),
]


def upgrade():
    conn = op.get_bind()
    for code, series, en, ko, rel, pred, active, cat in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at, predecessor_code,
                     is_active, brand, category, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, :act, 'samsung', :cat, now())
                ON CONFLICT (code) DO NOTHING
            """),
            # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다
            {"c": code, "s": series, "en": en, "ko": ko, "rel": date.fromisoformat(rel) if rel else None,
             "pred": pred, "act": active, "cat": cat},
        )


def downgrade():
    conn = op.get_bind()
    # 링크·VOC 가 붙은 행은 지우지 않는다(참조 무결성) — 다시 태깅을 되돌린 뒤에만 지워진다
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
