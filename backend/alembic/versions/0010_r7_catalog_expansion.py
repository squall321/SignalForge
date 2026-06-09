"""r7_catalog_expansion — R7 매핑 사전 확장을 위한 신규 products 시드.

Track A R7 (2026-06-04) — Discovery 결과 1,700건+ 미커버 모델군 catalog 추가.

배경:
  - R6 까지 122개 products. NULL voc 84%.
  - Discovery (deep-research 에이전트) 가 catalog 미보유 신규 패턴 발견:
      Tab S6~S11 / Tab A 8/9/11 / Tab Active5 / Watch9 /
      Galaxy A 07/16/17/26/27/36/37/57 / M 시리즈 / F 시리즈 /
      XCover4~7 / Wide 2~8 (KR SKT/KT) / Jump 1~4 (KR KT) / Note Pro 12.2
  - 이들 패턴이 commerce/리뷰 글 전반에 다수 출현 — 추가 시 NULL 비율 개선.

설계:
  - is_active=false (신규 수집 대상 아닌 옛/지역 모델은 false, 최신은 true)
  - released_at 알려진 것만 채움. 불명 모델은 NULL.
  - ON CONFLICT (code) DO UPDATE 멱등.

Revision ID: 0010
Revises: 0009
"""
from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


# (code, series_code, name_en, name_ko, released_at, is_active)
# 주의: series_code 는 VARCHAR(4) — 4자 이하 유지.
NEW_PRODUCTS = [
    # ── Galaxy Tab S 시리즈 (태블릿) — series=TABS ───────────────────
    ("GTABS6",  "TABS", "Galaxy Tab S6",      "갤럭시 탭 S6",     "2019-08-07", False),
    ("GTABS7",  "TABS", "Galaxy Tab S7",      "갤럭시 탭 S7",     "2020-09-04", False),
    ("GTABS7P", "TABS", "Galaxy Tab S7+",     "갤럭시 탭 S7+",    "2020-09-04", False),
    ("GTABS8",  "TABS", "Galaxy Tab S8",      "갤럭시 탭 S8",     "2022-02-25", False),
    ("GTABS8P", "TABS", "Galaxy Tab S8+",     "갤럭시 탭 S8+",    "2022-02-25", False),
    ("GTABS8U", "TABS", "Galaxy Tab S8 Ultra","갤럭시 탭 S8 울트라","2022-02-25", False),
    ("GTABS9",  "TABS", "Galaxy Tab S9",      "갤럭시 탭 S9",     "2023-08-11", False),
    ("GTABS9P", "TABS", "Galaxy Tab S9+",     "갤럭시 탭 S9+",    "2023-08-11", False),
    ("GTABS9U", "TABS", "Galaxy Tab S9 Ultra","갤럭시 탭 S9 울트라","2023-08-11", False),
    ("GTABS9F", "TABS", "Galaxy Tab S9 FE",   "갤럭시 탭 S9 FE",  "2023-10-04", False),
    ("GTABS10", "TABS", "Galaxy Tab S10",     "갤럭시 탭 S10",    "2024-10-03", True),
    ("GTABS10P","TABS", "Galaxy Tab S10+",    "갤럭시 탭 S10+",   "2024-10-03", True),
    ("GTABS10U","TABS", "Galaxy Tab S10 Ultra","갤럭시 탭 S10 울트라","2024-10-03", True),
    ("GTABS10F","TABS", "Galaxy Tab S10 FE",  "갤럭시 탭 S10 FE", "2025-04-01", True),
    ("GTABS11", "TABS", "Galaxy Tab S11",     "갤럭시 탭 S11",    "2025-09-01", True),
    ("GTABS11U","TABS", "Galaxy Tab S11 Ultra","갤럭시 탭 S11 울트라","2025-09-01", True),
    # ── Galaxy Tab A 시리즈 — series=TABA ─────────────────────────
    ("GTABA8",  "TABA", "Galaxy Tab A8",      "갤럭시 탭 A8",     "2021-12-10", False),
    ("GTABA9",  "TABA", "Galaxy Tab A9",      "갤럭시 탭 A9",     "2023-10-19", False),
    ("GTABA9P", "TABA", "Galaxy Tab A9+",     "갤럭시 탭 A9+",    "2023-10-19", False),
    ("GTABA11", "TABA", "Galaxy Tab A11",     "갤럭시 탭 A11",    "2025-09-01", True),
    # ── Tab Active (러기드 라인) — series=TABA ──────────────────────
    ("GTABACT5","TABA", "Galaxy Tab Active5","갤럭시 탭 액티브5","2024-04-01", True),
    # ── Watch9 ─────────────────────────────────────────────────────
    ("GW9",     "GW",   "Galaxy Watch9",      "갤럭시 워치9",     None,         True),
    # ── Galaxy A 시리즈 신규 ──────────────────────────────────────
    ("GA07",    "GA",   "Galaxy A07",         "갤럭시 A07",       "2025-09-01", True),
    ("GA16",    "GA",   "Galaxy A16",         "갤럭시 A16",       "2024-10-01", True),
    ("GA17",    "GA",   "Galaxy A17",         "갤럭시 A17",       "2025-09-01", True),
    ("GA26",    "GA",   "Galaxy A26",         "갤럭시 A26",       "2025-03-01", True),
    ("GA27",    "GA",   "Galaxy A27",         "갤럭시 A27",       None,         True),
    ("GA36",    "GA",   "Galaxy A36",         "갤럭시 A36",       "2025-06-12", True),
    ("GA37",    "GA",   "Galaxy A37",         "갤럭시 A37",       None,         True),
    ("GA57",    "GA",   "Galaxy A57",         "갤럭시 A57",       None,         True),
    # ── Galaxy M 시리즈 (인도/동남아) ───────────────────────────────
    ("GM14",    "GM",   "Galaxy M14",         "갤럭시 M14",       "2023-03-15", False),
    ("GM34",    "GM",   "Galaxy M34",         "갤럭시 M34",       "2023-08-07", False),
    ("GM54",    "GM",   "Galaxy M54",         "갤럭시 M54",       "2023-03-29", False),
    ("GM55",    "GM",   "Galaxy M55",         "갤럭시 M55",       "2024-04-04", True),
    # ── Galaxy F 시리즈 (인도) ─────────────────────────────────────
    ("GF23",    "GF",   "Galaxy F23",         "갤럭시 F23",       "2022-03-08", False),
    ("GF25",    "GF",   "Galaxy F25",         "갤럭시 F25",       None,         True),
    ("GF55",    "GF",   "Galaxy F55",         "갤럭시 F55",       "2024-05-17", True),
    # ── XCover (러기드) — series=GXC ─────────────────────────────
    ("GXC4",    "GXC",  "Galaxy XCover4",     "갤럭시 엑스커버4", "2017-06-01", False),
    ("GXC5",    "GXC",  "Galaxy XCover5",     "갤럭시 엑스커버5", "2021-04-15", False),
    ("GXC6",    "GXC",  "Galaxy XCover6 Pro", "갤럭시 엑스커버6 프로","2022-07-13", False),
    ("GXC7",    "GXC",  "Galaxy XCover7",     "갤럭시 엑스커버7", "2024-01-17", True),
    # ── Wide (KR SKT/KT) — series=WIDE ───────────────────────────
    ("GWIDE2",  "WIDE", "Galaxy Wide 2",      "갤럭시 와이드 2",  "2017-06-09", False),
    ("GWIDE3",  "WIDE", "Galaxy Wide 3",      "갤럭시 와이드 3",  "2018-06-29", False),
    ("GWIDE4",  "WIDE", "Galaxy Wide 4",      "갤럭시 와이드 4",  "2019-09-27", False),
    ("GWIDE5",  "WIDE", "Galaxy Wide 5",      "갤럭시 와이드 5",  "2021-04-29", False),
    ("GWIDE6",  "WIDE", "Galaxy Wide 6",      "갤럭시 와이드 6",  "2022-05-26", False),
    ("GWIDE7",  "WIDE", "Galaxy Wide 7",      "갤럭시 와이드 7",  "2023-12-22", False),
    ("GWIDE8",  "WIDE", "Galaxy Wide 8",      "갤럭시 와이드 8",  "2025-01-01", True),
    # ── Jump (KR KT) — series=JUMP ───────────────────────────────
    ("GJUMP1",  "JUMP", "Galaxy Jump",        "갤럭시 점프",      "2021-04-29", False),
    ("GJUMP2",  "JUMP", "Galaxy Jump 2",      "갤럭시 점프 2",    "2022-09-23", False),
    ("GJUMP3",  "JUMP", "Galaxy Jump 3",      "갤럭시 점프 3",    "2023-09-08", False),
    ("GJUMP4",  "JUMP", "Galaxy Jump 4",      "갤럭시 점프 4",    "2024-10-25", True),
    # ── Note Pro 12.2 (2014 태블릿) ─────────────────────────────────
    ("GNT122",  "GN",   "Galaxy Note Pro 12.2", "갤럭시 노트 프로 12.2","2014-02-13", False),
]


def upgrade() -> None:
    for code, series, name_en, name_ko, released_at, is_active in NEW_PRODUCTS:
        date_clause = f"DATE '{released_at}'" if released_at else "NULL"
        # name_ko 단순 문자열 — 작은따옴표 없음
        op.execute(
            f"""
            INSERT INTO products
                (code, series_code, name_en, name_ko, released_at, is_active)
            VALUES
                ('{code}', '{series}', '{name_en}', '{name_ko}',
                 {date_clause}, {str(is_active).upper()})
            ON CONFLICT (code) DO UPDATE SET
                released_at = COALESCE(products.released_at, EXCLUDED.released_at),
                name_ko     = COALESCE(products.name_ko,     EXCLUDED.name_ko)
            """
        )


def downgrade() -> None:
    codes = ",".join(f"'{r[0]}'" for r in NEW_PRODUCTS)
    op.execute(f"DELETE FROM products WHERE code IN ({codes})")
