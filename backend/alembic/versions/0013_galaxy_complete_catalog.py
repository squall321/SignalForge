"""galaxy_complete_catalog — R8 Galaxy 전 세대 완전 커버리지.

Track A R8 (2026-06-04) — Discovery (207건 식별) 중 catalog 미보유 모델 시드.

배경:
  - R7 이후 175개 products. voc NULL 80.14%.
  - Discovery: A 구형(2015~18 + A01~A03/A11~A91), J 시리즈 전체, M (M01~M53),
    F (F02s~F62), Tab 1~A7/Pro/Active, XCover 1~6Pro, Gear 1~Sport, Buds 1/+/FE,
    IconX, Fit 1~3, Watch Active 3/Watch4 Classic/Watch FE/Watch 6 Classic/Watch8
    Classic, Ring 1, 옛 폰 (Mega/Grand/Core/Star/Win/Ace/On/Y/Mini/Fame/Note
    Edge/Note FE/S3 mini~S10 Lite/Note 10 Lite/Fold 5G 등) 약 207개.
  - 시리즈별 코드 컨벤션:
      A 구형: GA<NN>_<YY> (예: GA3_15, GA5_16) → 10자 이내
      A 신규: GA<NN> (GA10E/GA20S 등은 GA10E/GA20S)
      M : GM<NN>
      F : GF<NN>
      J : GJ<NN> 또는 GJ<NN>_<YY>
      Tab 구형: GT_<series>_<size> (예: GT_2_7, GT_S_84, GT_PRO_84)
      Tab A 구형: GTA_<size>_<YY>
      Tab Active: GTACT<N>
      XCover 구형: GXC<N>
      Watch/Gear: GGEAR<N>, GGS<N>, GGF<N>, GFIT<N>, GW<N>C, GWFE
      Buds 옛: GBPLUS, GBFE, GICX, GICX2
      Ring : GR1
      옛 폰: GO_<name> (Galaxy Old) — 8자 이내

  - 모든 코드 VARCHAR(10) 이내, series_code VARCHAR(4) 이내.
  - ON CONFLICT (code) DO UPDATE — 멱등.

Revision ID: 0013
Revises: 0012
"""
from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# (code, series_code, name_en, name_ko, released_at, is_active)
# 코드 명명 컨벤션:
#  - 10자 이내 강제. 4자 이내 series_code.
#  - "_" 는 연식/사이즈 구분에 사용.
NEW_PRODUCTS = [
    # ═══════════════════════════════════════════════════════════════════
    # A 시리즈 구형 (2015~2018) — series=GA
    # ═══════════════════════════════════════════════════════════════════
    ("GA3_15",   "GA",   "Galaxy A3 (2015)",     "갤럭시 A3 (2015)",   "2014-12-19", False),
    ("GA5_15",   "GA",   "Galaxy A5 (2015)",     "갤럭시 A5 (2015)",   "2014-12-19", False),
    ("GA7_15",   "GA",   "Galaxy A7 (2015)",     "갤럭시 A7 (2015)",   "2015-01-13", False),
    ("GA8_15",   "GA",   "Galaxy A8 (2015)",     "갤럭시 A8 (2015)",   "2015-07-16", False),
    ("GA9_15",   "GA",   "Galaxy A9 (2015)",     "갤럭시 A9 (2015)",   "2015-12-22", False),
    ("GA3_16",   "GA",   "Galaxy A3 (2016)",     "갤럭시 A3 (2016)",   "2015-12-01", False),
    ("GA5_16",   "GA",   "Galaxy A5 (2016)",     "갤럭시 A5 (2016)",   "2015-12-01", False),
    ("GA7_16",   "GA",   "Galaxy A7 (2016)",     "갤럭시 A7 (2016)",   "2016-01-05", False),
    ("GA9_16",   "GA",   "Galaxy A9 (2016)",     "갤럭시 A9 (2016)",   "2015-12-31", False),
    ("GA9P_16",  "GA",   "Galaxy A9 Pro (2016)", "갤럭시 A9 Pro (2016)", "2016-04-12", False),
    ("GA3_17",   "GA",   "Galaxy A3 (2017)",     "갤럭시 A3 (2017)",   "2017-01-02", False),
    ("GA5_17",   "GA",   "Galaxy A5 (2017)",     "갤럭시 A5 (2017)",   "2017-01-02", False),
    ("GA7_17",   "GA",   "Galaxy A7 (2017)",     "갤럭시 A7 (2017)",   "2017-01-02", False),
    ("GA6_18",   "GA",   "Galaxy A6 (2018)",     "갤럭시 A6 (2018)",   "2018-05-02", False),
    ("GA6P_18",  "GA",   "Galaxy A6+ (2018)",    "갤럭시 A6+ (2018)",  "2018-05-02", False),
    ("GA7_18",   "GA",   "Galaxy A7 (2018)",     "갤럭시 A7 (2018)",   "2018-09-20", False),
    ("GA8_18",   "GA",   "Galaxy A8 (2018)",     "갤럭시 A8 (2018)",   "2017-12-19", False),
    ("GA8P_18",  "GA",   "Galaxy A8+ (2018)",    "갤럭시 A8+ (2018)",  "2017-12-19", False),
    ("GA9_18",   "GA",   "Galaxy A9 (2018)",     "갤럭시 A9 (2018)",   "2018-11-20", False),
    # ── A0X / A1X / A2X (2019~2021) ──
    ("GA01",     "GA",   "Galaxy A01",           "갤럭시 A01",         "2019-12-04", False),
    ("GA10",     "GA",   "Galaxy A10",           "갤럭시 A10",         "2019-03-20", False),
    ("GA10E",    "GA",   "Galaxy A10e",          "갤럭시 A10e",        "2019-08-02", False),
    ("GA10S",    "GA",   "Galaxy A10s",          "갤럭시 A10s",        "2019-08-30", False),
    ("GA20",     "GA",   "Galaxy A20",           "갤럭시 A20",         "2019-04-12", False),
    ("GA20E",    "GA",   "Galaxy A20e",          "갤럭시 A20e",        "2019-05-17", False),
    ("GA20S",    "GA",   "Galaxy A20s",          "갤럭시 A20s",        "2019-09-30", False),
    ("GA30",     "GA",   "Galaxy A30",           "갤럭시 A30",         "2019-03-15", False),
    ("GA30S",    "GA",   "Galaxy A30s",          "갤럭시 A30s",        "2019-09-06", False),
    ("GA40",     "GA",   "Galaxy A40",           "갤럭시 A40",         "2019-04-05", False),
    ("GA60",     "GA",   "Galaxy A60",           "갤럭시 A60",         "2019-04-19", False),
    ("GA70",     "GA",   "Galaxy A70",           "갤럭시 A70",         "2019-04-26", False),
    ("GA80",     "GA",   "Galaxy A80",           "갤럭시 A80",         "2019-05-29", False),
    ("GA90",     "GA",   "Galaxy A90 5G",        "갤럭시 A90 5G",      "2019-09-04", False),
    ("GA11",     "GA",   "Galaxy A11",           "갤럭시 A11",         "2020-03-10", False),
    ("GA21",     "GA",   "Galaxy A21",           "갤럭시 A21",         "2020-06-19", False),
    ("GA21S",    "GA",   "Galaxy A21s",          "갤럭시 A21s",        "2020-06-19", False),
    ("GA31",     "GA",   "Galaxy A31",           "갤럭시 A31",         "2020-04-24", False),
    ("GA41",     "GA",   "Galaxy A41",           "갤럭시 A41",         "2020-05-15", False),
    ("GA42",     "GA",   "Galaxy A42 5G",        "갤럭시 A42 5G",      "2020-11-06", False),
    ("GA71",     "GA",   "Galaxy A71",           "갤럭시 A71",         "2020-01-17", False),
    ("GA02",     "GA",   "Galaxy A02",           "갤럭시 A02",         "2021-02-04", False),
    ("GA02S",    "GA",   "Galaxy A02s",          "갤럭시 A02s",        "2020-12-08", False),
    ("GA03",     "GA",   "Galaxy A03",           "갤럭시 A03",         "2021-11-19", False),
    ("GA03S",    "GA",   "Galaxy A03s",          "갤럭시 A03s",        "2021-08-26", False),
    ("GA03C",    "GA",   "Galaxy A03 Core",      "갤럭시 A03 Core",    "2021-10-21", False),
    ("GA12",     "GA",   "Galaxy A12",           "갤럭시 A12",         "2020-11-24", False),
    ("GA13",     "GA",   "Galaxy A13",           "갤럭시 A13",         "2022-03-25", False),
    ("GA22",     "GA",   "Galaxy A22",           "갤럭시 A22",         "2021-07-02", False),
    ("GA23",     "GA",   "Galaxy A23",           "갤럭시 A23",         "2022-03-15", False),
    ("GA24",     "GA",   "Galaxy A24",           "갤럭시 A24",         "2023-05-12", False),
    ("GA25",     "GA",   "Galaxy A25 5G",        "갤럭시 A25 5G",      "2023-12-26", False),
    ("GA32",     "GA",   "Galaxy A32",           "갤럭시 A32",         "2021-03-05", False),
    ("GA33",     "GA",   "Galaxy A33 5G",        "갤럭시 A33 5G",      "2022-04-22", False),
    ("GA34",     "GA",   "Galaxy A34 5G",        "갤럭시 A34 5G",      "2023-03-24", False),
    ("GA35",     "GA",   "Galaxy A35 5G",        "갤럭시 A35 5G",      "2024-03-11", True),
    ("GA72",     "GA",   "Galaxy A72",           "갤럭시 A72",         "2021-03-26", False),
    ("GA73",     "GA",   "Galaxy A73 5G",        "갤럭시 A73 5G",      "2022-04-22", False),
    ("GWIDE1",   "WIDE", "Galaxy Wide",          "갤럭시 와이드",       "2016-09-09", False),

    # ═══════════════════════════════════════════════════════════════════
    # M 시리즈 — series=GM
    # ═══════════════════════════════════════════════════════════════════
    ("GM01",     "GM",   "Galaxy M01",           "갤럭시 M01",         "2020-06-02", False),
    ("GM02",     "GM",   "Galaxy M02",           "갤럭시 M02",         "2021-02-02", False),
    ("GM10",     "GM",   "Galaxy M10",           "갤럭시 M10",         "2019-02-05", False),
    ("GM11",     "GM",   "Galaxy M11",           "갤럭시 M11",         "2020-04-02", False),
    ("GM12",     "GM",   "Galaxy M12",           "갤럭시 M12",         "2021-03-11", False),
    ("GM13",     "GM",   "Galaxy M13",           "갤럭시 M13",         "2022-07-14", False),
    ("GM21",     "GM",   "Galaxy M21",           "갤럭시 M21",         "2020-03-16", False),
    ("GM22",     "GM",   "Galaxy M22",           "갤럭시 M22",         "2021-09-23", False),
    ("GM23",     "GM",   "Galaxy M23 5G",        "갤럭시 M23 5G",      "2022-03-22", False),
    ("GM30",     "GM",   "Galaxy M30",           "갤럭시 M30",         "2019-02-27", False),
    ("GM30S",    "GM",   "Galaxy M30s",          "갤럭시 M30s",        "2019-09-23", False),
    ("GM31",     "GM",   "Galaxy M31",           "갤럭시 M31",         "2020-02-25", False),
    ("GM31S",    "GM",   "Galaxy M31s",          "갤럭시 M31s",        "2020-07-30", False),
    ("GM32",     "GM",   "Galaxy M32",           "갤럭시 M32",         "2021-06-28", False),
    ("GM33",     "GM",   "Galaxy M33 5G",        "갤럭시 M33 5G",      "2022-04-11", False),
    ("GM40",     "GM",   "Galaxy M40",           "갤럭시 M40",         "2019-06-18", False),
    ("GM42",     "GM",   "Galaxy M42 5G",        "갤럭시 M42 5G",      "2021-04-28", False),
    ("GM51",     "GM",   "Galaxy M51",           "갤럭시 M51",         "2020-09-10", False),
    ("GM52",     "GM",   "Galaxy M52 5G",        "갤럭시 M52 5G",      "2021-10-04", False),
    ("GM53",     "GM",   "Galaxy M53 5G",        "갤럭시 M53 5G",      "2022-04-21", False),

    # ═══════════════════════════════════════════════════════════════════
    # F 시리즈 — series=GF
    # ═══════════════════════════════════════════════════════════════════
    ("GF02S",    "GF",   "Galaxy F02s",          "갤럭시 F02s",        "2021-04-12", False),
    ("GF12",     "GF",   "Galaxy F12",           "갤럭시 F12",         "2021-04-12", False),
    ("GF22",     "GF",   "Galaxy F22",           "갤럭시 F22",         "2021-09-06", False),
    ("GF41",     "GF",   "Galaxy F41",           "갤럭시 F41",         "2020-10-08", False),
    ("GF42",     "GF",   "Galaxy F42 5G",        "갤럭시 F42 5G",      "2021-09-29", False),
    ("GF52",     "GF",   "Galaxy F52 5G",        "갤럭시 F52 5G",      "2021-05-26", False),
    ("GF54",     "GF",   "Galaxy F54 5G",        "갤럭시 F54 5G",      "2023-05-29", False),
    ("GF62",     "GF",   "Galaxy F62",           "갤럭시 F62",         "2021-02-22", False),

    # ═══════════════════════════════════════════════════════════════════
    # J 시리즈 — series=GJ (2015~2018 entry level)
    # ═══════════════════════════════════════════════════════════════════
    ("GJ1",      "GJ",   "Galaxy J1",            "갤럭시 J1",          "2015-01-26", False),
    ("GJ1M",     "GJ",   "Galaxy J1 mini",       "갤럭시 J1 미니",      "2016-02-11", False),
    ("GJ2",      "GJ",   "Galaxy J2",            "갤럭시 J2",          "2015-09-13", False),
    ("GJ2_16",   "GJ",   "Galaxy J2 (2016)",     "갤럭시 J2 (2016)",   "2016-08-26", False),
    ("GJ2PRO",   "GJ",   "Galaxy J2 Pro",        "갤럭시 J2 Pro",      "2018-04-10", False),
    ("GJ3_16",   "GJ",   "Galaxy J3 (2016)",     "갤럭시 J3 (2016)",   "2016-01-02", False),
    ("GJ3_17",   "GJ",   "Galaxy J3 (2017)",     "갤럭시 J3 (2017)",   "2017-06-30", False),
    ("GJ5",      "GJ",   "Galaxy J5",            "갤럭시 J5",          "2015-06-19", False),
    ("GJ5_16",   "GJ",   "Galaxy J5 (2016)",     "갤럭시 J5 (2016)",   "2016-04-04", False),
    ("GJ5_17",   "GJ",   "Galaxy J5 (2017)",     "갤럭시 J5 (2017)",   "2017-06-23", False),
    ("GJ5PRM",   "GJ",   "Galaxy J5 Prime",      "갤럭시 J5 프라임",    "2016-09-30", False),
    ("GJ7",      "GJ",   "Galaxy J7",            "갤럭시 J7",          "2015-06-12", False),
    ("GJ7_16",   "GJ",   "Galaxy J7 (2016)",     "갤럭시 J7 (2016)",   "2016-04-13", False),
    ("GJ7_17",   "GJ",   "Galaxy J7 (2017)",     "갤럭시 J7 (2017)",   "2017-06-30", False),
    ("GJ7PRO",   "GJ",   "Galaxy J7 Pro",        "갤럭시 J7 프로",      "2017-07-15", False),
    ("GJ7PRM",   "GJ",   "Galaxy J7 Prime",      "갤럭시 J7 프라임",    "2016-10-07", False),
    ("GJ7MAX",   "GJ",   "Galaxy J7 Max",        "갤럭시 J7 맥스",      "2017-06-30", False),
    ("GJ8",      "GJ",   "Galaxy J8",            "갤럭시 J8",          "2018-05-31", False),

    # ═══════════════════════════════════════════════════════════════════
    # Tab 구형 — series=TAB
    # ═══════════════════════════════════════════════════════════════════
    ("GTAB1",    "TAB",  "Galaxy Tab",           "갤럭시 탭",          "2010-11-11", False),
    ("GT2_7",    "TAB",  "Galaxy Tab 2 7.0",     "갤럭시 탭 2 7.0",    "2012-04-22", False),
    ("GT2_10",   "TAB",  "Galaxy Tab 2 10.1",    "갤럭시 탭 2 10.1",   "2012-05-13", False),
    ("GT3_7",    "TAB",  "Galaxy Tab 3 7.0",     "갤럭시 탭 3 7.0",    "2013-07-07", False),
    ("GT3_8",    "TAB",  "Galaxy Tab 3 8.0",     "갤럭시 탭 3 8.0",    "2013-07-26", False),
    ("GT3_10",   "TAB",  "Galaxy Tab 3 10.1",    "갤럭시 탭 3 10.1",   "2013-06-23", False),
    ("GT4_7",    "TAB",  "Galaxy Tab 4 7.0",     "갤럭시 탭 4 7.0",    "2014-05-01", False),
    ("GT4_8",    "TAB",  "Galaxy Tab 4 8.0",     "갤럭시 탭 4 8.0",    "2014-05-01", False),
    ("GT4_10",   "TAB",  "Galaxy Tab 4 10.1",    "갤럭시 탭 4 10.1",   "2014-05-01", False),
    ("GTS_84",   "TABS", "Galaxy Tab S 8.4",     "갤럭시 탭 S 8.4",    "2014-07-04", False),
    ("GTS_105",  "TABS", "Galaxy Tab S 10.5",    "갤럭시 탭 S 10.5",   "2014-07-04", False),
    ("GTS2",     "TABS", "Galaxy Tab S2",        "갤럭시 탭 S2",       "2015-08-04", False),
    ("GTS3",     "TABS", "Galaxy Tab S3",        "갤럭시 탭 S3",       "2017-03-24", False),
    ("GTS4",     "TABS", "Galaxy Tab S4",        "갤럭시 탭 S4",       "2018-08-10", False),
    ("GTS5E",    "TABS", "Galaxy Tab S5e",       "갤럭시 탭 S5e",      "2019-04-08", False),
    ("GTS6L",    "TABS", "Galaxy Tab S6 Lite",   "갤럭시 탭 S6 Lite",  "2020-05-22", False),
    ("GTS7F",    "TABS", "Galaxy Tab S7 FE",     "갤럭시 탭 S7 FE",    "2021-06-18", False),
    ("GTS9FP",   "TABS", "Galaxy Tab S9 FE+",    "갤럭시 탭 S9 FE+",   "2023-10-04", False),
    ("GTS10FP",  "TABS", "Galaxy Tab S10 FE+",   "갤럭시 탭 S10 FE+",  "2025-04-01", True),
    ("GTS11P",   "TABS", "Galaxy Tab S11+",      "갤럭시 탭 S11+",     "2025-09-01", True),
    ("GTP_84",   "TABS", "Galaxy TabPRO 8.4",    "갤럭시 탭 프로 8.4",  "2014-02-13", False),
    ("GTP_101",  "TABS", "Galaxy TabPRO 10.1",   "갤럭시 탭 프로 10.1", "2014-02-13", False),
    ("GTP_122",  "TABS", "Galaxy TabPRO 12.2",   "갤럭시 탭 프로 12.2", "2014-02-13", False),
    ("GTA7_15",  "TABA", "Galaxy Tab A 7.0 2015","갤럭시 탭 A 7.0 (2015)", "2015-08-31", False),
    ("GTA8_15",  "TABA", "Galaxy Tab A 8.0 2015","갤럭시 탭 A 8.0 (2015)", "2015-05-13", False),
    ("GTA97",    "TABA", "Galaxy Tab A 9.7",     "갤럭시 탭 A 9.7",    "2015-05-13", False),
    ("GTA10_16", "TABA", "Galaxy Tab A 10.1 2016","갤럭시 탭 A 10.1 (2016)", "2016-05-26", False),
    ("GTA8_17",  "TABA", "Galaxy Tab A 8.0 2017","갤럭시 탭 A 8.0 (2017)", "2017-04-26", False),
    ("GTA8_19",  "TABA", "Galaxy Tab A 8.0 2019","갤럭시 탭 A 8.0 (2019)", "2019-04-15", False),
    ("GTA10_19", "TABA", "Galaxy Tab A 10.1 2019","갤럭시 탭 A 10.1 (2019)", "2019-02-28", False),
    ("GTA7",     "TABA", "Galaxy Tab A7",        "갤럭시 탭 A7",       "2020-09-25", False),
    ("GTA7L",    "TABA", "Galaxy Tab A7 Lite",   "갤럭시 탭 A7 Lite",  "2021-06-04", False),
    ("GTACT1",   "TABA", "Galaxy Tab Active",    "갤럭시 탭 액티브",    "2014-11-13", False),
    ("GTACT2",   "TABA", "Galaxy Tab Active 2",  "갤럭시 탭 액티브 2",  "2017-10-27", False),
    ("GTACT3",   "TABA", "Galaxy Tab Active 3",  "갤럭시 탭 액티브 3",  "2020-09-15", False),
    ("GTACT4P",  "TABA", "Galaxy Tab Active 4 Pro","갤럭시 탭 액티브 4 Pro", "2022-10-12", False),

    # ═══════════════════════════════════════════════════════════════════
    # XCover 구형 — series=GXC
    # ═══════════════════════════════════════════════════════════════════
    ("GXC1",     "GXC",  "Galaxy XCover",        "갤럭시 엑스커버",     "2011-09-01", False),
    ("GXC2",     "GXC",  "Galaxy XCover2",       "갤럭시 엑스커버2",    "2013-02-13", False),
    ("GXC3",     "GXC",  "Galaxy XCover3",       "갤럭시 엑스커버3",    "2015-05-15", False),
    ("GXC4S",    "GXC",  "Galaxy XCover4s",      "갤럭시 엑스커버4s",   "2019-07-09", False),
    # GXC5/GXC6 는 0010 에 보유 — XCover Pro 단독 모델만 추가
    ("GXCPRO",   "GXC",  "Galaxy XCover Pro",    "갤럭시 엑스커버 프로", "2020-01-29", False),

    # ═══════════════════════════════════════════════════════════════════
    # Watch / Gear / Fit — series=GW
    # ═══════════════════════════════════════════════════════════════════
    ("GGEAR1",   "GW",   "Galaxy Gear",          "갤럭시 기어",        "2013-09-25", False),
    ("GGEAR2",   "GW",   "Galaxy Gear 2",        "갤럭시 기어 2",      "2014-04-11", False),
    ("GGEAR2N",  "GW",   "Galaxy Gear 2 Neo",    "갤럭시 기어 2 네오",  "2014-04-11", False),
    ("GGS",      "GW",   "Samsung Gear S",       "삼성 기어 S",        "2014-10-31", False),
    ("GGS2",     "GW",   "Samsung Gear S2",      "삼성 기어 S2",       "2015-10-02", False),
    ("GGS3",     "GW",   "Samsung Gear S3",      "삼성 기어 S3",       "2016-11-18", False),
    ("GGSPORT",  "GW",   "Gear Sport",           "기어 스포트",        "2017-10-06", False),
    ("GGEARFIT", "GW",   "Gear Fit",             "기어 핏",            "2014-04-11", False),
    ("GGFIT2",   "GW",   "Gear Fit 2",           "기어 핏 2",          "2016-06-10", False),
    ("GFIT",     "GW",   "Galaxy Fit",           "갤럭시 핏",          "2019-04-29", False),
    ("GFITE",    "GW",   "Galaxy Fit e",         "갤럭시 핏 e",        "2019-04-29", False),
    ("GFIT2",    "GW",   "Galaxy Fit2",          "갤럭시 핏2",         "2020-09-23", False),
    ("GFIT3",    "GW",   "Galaxy Fit3",          "갤럭시 핏3",         "2024-02-21", True),
    ("GWA3",     "GW",   "Galaxy Watch Active3", "갤럭시 워치 액티브3", "2020-08-06", False),
    ("GW4C",     "GW",   "Galaxy Watch4 Classic","갤럭시 워치4 클래식", "2021-08-27", False),
    ("GW6C",     "GW",   "Galaxy Watch6 Classic","갤럭시 워치6 클래식", "2023-08-11", False),
    ("GWFE",     "GW",   "Galaxy Watch FE",      "갤럭시 워치 FE",     "2024-06-26", True),
    ("GW8C",     "GW",   "Galaxy Watch8 Classic","갤럭시 워치8 클래식", "2025-07-25", True),

    # ═══════════════════════════════════════════════════════════════════
    # Buds / IconX — series=GB
    # ═══════════════════════════════════════════════════════════════════
    ("GICX",     "GB",   "Gear IconX",           "기어 아이콘X",        "2016-08-31", False),
    ("GICX2",    "GB",   "Gear IconX 2018",      "기어 아이콘X 2018",   "2017-10-25", False),
    ("GBPLUS",   "GB",   "Galaxy Buds+",         "갤럭시 버즈+",        "2020-02-14", False),
    ("GBFE",     "GB",   "Galaxy Buds FE",       "갤럭시 버즈 FE",     "2023-10-04", False),

    # ═══════════════════════════════════════════════════════════════════
    # Ring — series=GR
    # ═══════════════════════════════════════════════════════════════════
    ("GR1",      "GR",   "Galaxy Ring",          "갤럭시 링",          "2024-07-24", True),

    # ═══════════════════════════════════════════════════════════════════
    # 옛 폰 (Old phones / 특수 변형) — series=GOLD
    # ═══════════════════════════════════════════════════════════════════
    ("GI7500",   "GOLD", "Galaxy i7500",         "갤럭시 (i7500)",      "2009-06-29", False),
    ("GSPLUS",   "GS",   "Galaxy S Plus",        "갤럭시 S 플러스",     "2011-05-09", False),
    ("GBEAM",    "GOLD", "Galaxy Beam",          "갤럭시 빔",          "2012-07-12", False),
    ("GMEGA58",  "GOLD", "Galaxy Mega 5.8",      "갤럭시 메가 5.8",    "2013-05-23", False),
    ("GMEGA63",  "GOLD", "Galaxy Mega 6.3",      "갤럭시 메가 6.3",    "2013-05-23", False),
    ("GGRAND",   "GOLD", "Galaxy Grand",         "갤럭시 그랜드",       "2013-01-02", False),
    ("GGRAND2",  "GOLD", "Galaxy Grand 2",       "갤럭시 그랜드 2",     "2014-01-15", False),
    ("GGRPRM",   "GOLD", "Galaxy Grand Prime",   "갤럭시 그랜드 프라임", "2014-10-17", False),
    ("GGRPRMP",  "GOLD", "Galaxy Grand Prime+",  "갤럭시 그랜드 프라임+","2016-12-23", False),
    ("GCORE",    "GOLD", "Galaxy Core",          "갤럭시 코어",         "2013-05-29", False),
    ("GCORE2",   "GOLD", "Galaxy Core 2",        "갤럭시 코어 2",       "2014-07-04", False),
    ("GCOREPRM", "GOLD", "Galaxy Core Prime",    "갤럭시 코어 프라임",  "2014-11-12", False),
    ("GSTAR",    "GOLD", "Galaxy Star",          "갤럭시 스타",         "2013-06-25", False),
    ("GSTAR2",   "GOLD", "Galaxy Star 2",        "갤럭시 스타 2",       "2014-04-08", False),
    ("GWIN",     "GOLD", "Galaxy Win",           "갤럭시 윈",          "2013-05-15", False),
    ("GWINPRO",  "GOLD", "Galaxy Win Pro",       "갤럭시 윈 프로",      "2013-10-31", False),
    ("GTREND",   "GOLD", "Galaxy Trend",         "갤럭시 트렌드",       "2013-01-02", False),
    ("GTRENDL",  "GOLD", "Galaxy Trend Lite",    "갤럭시 트렌드 라이트", "2013-05-15", False),
    ("GACE",     "GOLD", "Galaxy Ace",           "갤럭시 에이스",       "2011-02-13", False),
    ("GACE2",    "GOLD", "Galaxy Ace 2",         "갤럭시 에이스 2",     "2012-04-12", False),
    ("GACE3",    "GOLD", "Galaxy Ace 3",         "갤럭시 에이스 3",     "2013-06-12", False),
    ("GACE4",    "GOLD", "Galaxy Ace 4",         "갤럭시 에이스 4",     "2014-07-29", False),
    ("GON5",     "GOLD", "Galaxy On5",           "갤럭시 온5",         "2015-10-22", False),
    ("GON7",     "GOLD", "Galaxy On7",           "갤럭시 온7",         "2015-10-22", False),
    ("GPOCKET",  "GOLD", "Galaxy Pocket",        "갤럭시 포켓",         "2012-03-17", False),
    ("GPOCKET2", "GOLD", "Galaxy Pocket 2",      "갤럭시 포켓 2",       "2014-07-15", False),
    ("GY",       "GOLD", "Galaxy Y",             "갤럭시 Y",           "2011-10-04", False),
    ("GYDUOS",   "GOLD", "Galaxy Y Duos",        "갤럭시 Y 듀오스",     "2012-03-12", False),
    ("GMINI",    "GOLD", "Galaxy Mini",          "갤럭시 미니",         "2011-02-15", False),
    ("GMINI2",   "GOLD", "Galaxy Mini 2",        "갤럭시 미니 2",       "2012-03-12", False),
    ("GFAME",    "GOLD", "Galaxy Fame",          "갤럭시 페임",         "2013-03-22", False),
    ("GMUSIC",   "GOLD", "Galaxy Music",         "갤럭시 뮤직",         "2012-12-13", False),
    ("GEXPRESS", "GOLD", "Galaxy Express",       "갤럭시 익스프레스",   "2013-01-22", False),
    ("GEXPR2",   "GOLD", "Galaxy Express 2",     "갤럭시 익스프레스 2", "2013-10-08", False),
    ("GNEDGE",   "GN",   "Galaxy Note Edge",     "갤럭시 노트 엣지",    "2014-09-26", False),
    ("GS3MINI",  "GS",   "Galaxy S3 mini",       "갤럭시 S3 미니",     "2012-11-01", False),
    ("GS4MINI",  "GS",   "Galaxy S4 mini",       "갤럭시 S4 미니",     "2013-07-26", False),
    ("GS5MINI",  "GS",   "Galaxy S5 mini",       "갤럭시 S5 미니",     "2014-07-04", False),
    ("GS6EP",    "GS",   "Galaxy S6 Edge+",      "갤럭시 S6 엣지+",    "2015-08-21", False),
    ("GS8A",     "GS",   "Galaxy S8 Active",     "갤럭시 S8 액티브",   "2017-08-08", False),
    ("GS9A",     "GS",   "Galaxy S9 Active",     "갤럭시 S9 액티브",   "2018-09-21", False),
    ("GS10L",    "GS",   "Galaxy S10 Lite",      "갤럭시 S10 라이트",  "2020-01-23", False),
    ("GN10L",    "GN",   "Galaxy Note 10 Lite",  "갤럭시 노트 10 라이트","2020-01-23", False),
    ("GNFE",     "GN",   "Galaxy Note Fan Ed.",  "갤럭시 노트 FE",     "2017-07-07", False),
    ("GZFL1_5G", "GZ",   "Galaxy Z Flip 5G",     "갤럭시 Z 플립 5G",   "2020-08-06", False),
    ("GZF1_5G",  "GZ",   "Galaxy Fold 5G",       "갤럭시 폴드 5G",     "2019-09-06", False),
]


def upgrade() -> None:
    for code, series, name_en, name_ko, released_at, is_active in NEW_PRODUCTS:
        # 길이 가드 (10/4) — 마이그레이션 시점 검증
        assert len(code) <= 10, f"code 길이 초과: {code} ({len(code)})"
        assert len(series) <= 4, f"series_code 길이 초과: {series} ({len(series)})"
        date_clause = f"DATE '{released_at}'" if released_at else "NULL"
        # name_ko / name_en 안전한 작은따옴표 이스케이프
        ko_esc = name_ko.replace("'", "''")
        en_esc = name_en.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO products
                (code, series_code, name_en, name_ko, released_at, is_active)
            VALUES
                ('{code}', '{series}', '{en_esc}', '{ko_esc}',
                 {date_clause}, {str(is_active).upper()})
            ON CONFLICT (code) DO UPDATE SET
                released_at = COALESCE(products.released_at, EXCLUDED.released_at),
                name_ko     = COALESCE(products.name_ko,     EXCLUDED.name_ko)
            """
        )


def downgrade() -> None:
    codes = ",".join(f"'{r[0]}'" for r in NEW_PRODUCTS)
    op.execute(f"DELETE FROM products WHERE code IN ({codes})")
