"""test_relink_v3 — R8 Galaxy 전 세대 매핑 사전 확장 검증.

Track A R8 (2026-06-04) — 0011 alembic + relink_products R8 패턴이
A 구형(연식 표기) / J / M / F / Tab 구형 / XCover 구형 / Watch Classic /
Gear / Fit / Buds+ / Ring / 옛 폰 (Mega/Grand/Core/Ace/On/Pocket 등) 매칭.

R7 회귀 (v2) 가 깨지지 않는지도 별도 케이스로 검증.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import (  # noqa: E402
    match_product_code,
    MODEL_MAP,
    MODEL_REGEX_PATTERNS,
)


# ─────────────────────────────────────────────────────────────────────
# A 구형 연식 표기 — Galaxy A3 (2015) ~ A9 (2018)
# ─────────────────────────────────────────────────────────────────────
def test_a_old_with_year_suffix():
    """Galaxy A3/A5/A7/A9 + 연식 표기 매칭."""
    assert match_product_code("Galaxy A3 (2015) review") == "GA3_15"
    assert match_product_code("Galaxy A5 (2017) 사용기") == "GA5_17"
    assert match_product_code("Samsung Galaxy A7 2018 카메라") == "GA7_18"
    assert match_product_code("Galaxy A9 Pro (2016) flash") == "GA9P_16"
    assert match_product_code("Galaxy A8+ (2018) 4가지 색") == "GA8P_18"


def test_a_0x_1x_2x_variants():
    """A01/A02s/A03 core, A10e/A10s, A20s, A30s, A21s 등 변형."""
    assert match_product_code("Galaxy A03 Core entry-level") == "GA03C"
    assert match_product_code("Galaxy A03s 4GB") == "GA03S"
    assert match_product_code("Galaxy A02s firmware") == "GA02S"
    assert match_product_code("Galaxy A10e Cricket") == "GA10E"
    assert match_product_code("Samsung Galaxy A10s") == "GA10S"
    assert match_product_code("Galaxy A20s Mexico") == "GA20S"
    assert match_product_code("Galaxy A30s spec") == "GA30S"
    assert match_product_code("Galaxy A21s price") == "GA21S"
    assert match_product_code("Galaxy A11") == "GA11"
    assert match_product_code("Galaxy A71 5G") == "GA71"


# ─────────────────────────────────────────────────────────────────────
# J 시리즈 — 2015~2018 entry-level (Galaxy 컨텍스트 필수)
# ─────────────────────────────────────────────────────────────────────
def test_j_series():
    """Galaxy J7 / J5 Prime / J2 Pro / J1 mini 등."""
    assert match_product_code("Galaxy J7 Pro launched 2017") == "GJ7PRO"
    assert match_product_code("Galaxy J5 Prime cheap") == "GJ5PRM"
    assert match_product_code("Galaxy J2 Pro 키패드") == "GJ2PRO"
    assert match_product_code("Galaxy J1 mini India") == "GJ1M"
    assert match_product_code("Samsung Galaxy J7 (2017)") == "GJ7_17"
    assert match_product_code("Galaxy J5 2016 model") == "GJ5_16"
    assert match_product_code("Galaxy J8 8GB") == "GJ8"


# ─────────────────────────────────────────────────────────────────────
# M 시리즈 확장 — M01~M53 (Galaxy 컨텍스트 필수)
# ─────────────────────────────────────────────────────────────────────
def test_m_series_extended():
    """Galaxy M51 / M31s / M30 등 R7 미커버 모델."""
    assert match_product_code("Galaxy M51 7000mAh battery") == "GM51"
    assert match_product_code("Galaxy M31s 사용 후기") == "GM31S"
    assert match_product_code("Galaxy M30 India") == "GM30"
    assert match_product_code("Galaxy M52 5G 빠른") == "GM52"
    assert match_product_code("Galaxy M01 entry phone") == "GM01"


# ─────────────────────────────────────────────────────────────────────
# F 시리즈 확장
# ─────────────────────────────────────────────────────────────────────
def test_f_series_extended():
    """Galaxy F62/F41/F52 etc."""
    assert match_product_code("Galaxy F62 battery king") == "GF62"
    assert match_product_code("Galaxy F41 6000mAh") == "GF41"
    assert match_product_code("Galaxy F52 5G Vietnam") == "GF52"
    assert match_product_code("Galaxy F02s entry") == "GF02S"


# ─────────────────────────────────────────────────────────────────────
# Tab 구형 (1~4, S/Pro/A 구형, Active 1~4)
# ─────────────────────────────────────────────────────────────────────
def test_tab_old_variants():
    """Galaxy Tab 2 7.0 / Tab S 10.5 / TabPRO 12.2 / Tab Active 4 Pro."""
    assert match_product_code("Galaxy Tab S 10.5 OLED") == "GTS_105"
    assert match_product_code("Galaxy TabPRO 12.2 K Edition") == "GTP_122"
    assert match_product_code("Galaxy Tab S5e wifi") == "GTS5E"
    assert match_product_code("Galaxy Tab S6 Lite 2024") == "GTS6L"
    assert match_product_code("Galaxy Tab S7 FE 5G") == "GTS7F"
    assert match_product_code("Galaxy Tab S9 FE+ 256GB") == "GTS9FP"
    assert match_product_code("Galaxy Tab Active 4 Pro 산업용") == "GTACT4P"
    assert match_product_code("탭 액티브 3 사용") == "GTACT3"
    assert match_product_code("Galaxy Tab A7 Lite 32GB") == "GTA7L"
    assert match_product_code("Galaxy Tab A 9.7 2015") == "GTA97"
    assert match_product_code("Galaxy Tab 2 7.0 P3100 root") == "GT2_7"


# ─────────────────────────────────────────────────────────────────────
# XCover 구형 (1~4s + Pro)
# ─────────────────────────────────────────────────────────────────────
def test_xcover_old_variants():
    """XCover4s / XCover3 / XCover Pro 단독."""
    assert match_product_code("Samsung XCover4s G398FN") == "GXC4S"
    assert match_product_code("Samsung Galaxy XCover3") == "GXC3"
    assert match_product_code("Samsung XCover2 industrial") == "GXC2"
    # XCover Pro 단독 (XCover6 Pro 와 다름 — XCover6 는 0010 보유)
    assert match_product_code("Samsung Galaxy XCover Pro G715FN") == "GXCPRO"


# ─────────────────────────────────────────────────────────────────────
# Watch Classic / FE / Active 3, Gear, Fit
# ─────────────────────────────────────────────────────────────────────
def test_watch_gear_fit_variants():
    """Watch 4/6/8 Classic, Watch FE, Active3, Gear Sport, Fit3."""
    assert match_product_code("Galaxy Watch8 Classic 47mm") == "GW8C"
    assert match_product_code("Galaxy Watch6 Classic 모션") == "GW6C"
    assert match_product_code("Galaxy Watch4 Classic 46mm") == "GW4C"
    assert match_product_code("Galaxy Watch FE 가격") == "GWFE"
    assert match_product_code("Galaxy Watch Active 3 (rumor)") == "GWA3"
    assert match_product_code("Gear Sport 출시") == "GGSPORT"
    assert match_product_code("Samsung Gear S3 frontier") == "GGS3"
    assert match_product_code("Gear Fit 2 review") == "GGFIT2"
    assert match_product_code("Galaxy Fit3 ECG") == "GFIT3"
    assert match_product_code("Galaxy Fit e 후기") == "GFITE"


# ─────────────────────────────────────────────────────────────────────
# Buds+ / Buds FE / IconX
# ─────────────────────────────────────────────────────────────────────
def test_buds_iconx_variants():
    """Galaxy Buds+ / Buds FE / Gear IconX 2018."""
    assert match_product_code("Galaxy Buds+ 첫인상") == "GBPLUS"
    assert match_product_code("Galaxy Buds Plus 화이트") == "GBPLUS"
    assert match_product_code("Galaxy Buds FE 노이즈") == "GBFE"
    assert match_product_code("Gear IconX 2018 firmware") == "GICX2"
    assert match_product_code("Gear IconX battery") == "GICX"


# ─────────────────────────────────────────────────────────────────────
# Ring
# ─────────────────────────────────────────────────────────────────────
def test_ring():
    """Galaxy Ring."""
    assert match_product_code("Galaxy Ring 사이즈 측정") == "GR1"
    assert match_product_code("갤럭시 링 가격 인하") == "GR1"


# ─────────────────────────────────────────────────────────────────────
# 옛 폰 — Mega/Grand/Core/Ace/On/Pocket/Mini/Star/Win/Y/Trend/Note Edge
# ─────────────────────────────────────────────────────────────────────
def test_old_phones():
    """Galaxy Mega 6.3 / Grand Prime+ / Core Prime / Note Edge 등."""
    assert match_product_code("Galaxy Mega 6.3 large screen") == "GMEGA63"
    assert match_product_code("Galaxy Grand Prime+ 4G") == "GGRPRMP"
    assert match_product_code("Galaxy Grand Prime root") == "GGRPRM"
    assert match_product_code("Galaxy Grand 2 hands-on") == "GGRAND2"
    assert match_product_code("Galaxy Core Prime entry") == "GCOREPRM"
    assert match_product_code("Galaxy Note Edge SM-N915") == "GNEDGE"
    assert match_product_code("Galaxy Note FE refurb") == "GNFE"
    assert match_product_code("Galaxy Note 10 Lite 카메라") == "GN10L"
    assert match_product_code("Galaxy S10 Lite review") == "GS10L"
    assert match_product_code("Galaxy S8 Active rugged") == "GS8A"
    assert match_product_code("Galaxy S6 Edge+ 32GB") == "GS6EP"
    assert match_product_code("Galaxy S3 mini i8190") == "GS3MINI"
    assert match_product_code("Galaxy Ace 3 GT-S7270") == "GACE3"
    assert match_product_code("Galaxy On7 출시") == "GON7"
    assert match_product_code("Galaxy Pocket 2 budget") == "GPOCKET2"
    assert match_product_code("Galaxy Star 2 cheap") == "GSTAR2"
    assert match_product_code("Galaxy Win Pro G3812") == "GWINPRO"
    assert match_product_code("Galaxy Y Duos") == "GYDUOS"
    assert match_product_code("Galaxy Trend Lite") == "GTRENDL"
    assert match_product_code("Galaxy Fame review") == "GFAME"
    assert match_product_code("Galaxy Express 2") == "GEXPR2"
    assert match_product_code("Galaxy Fold 5G 한국 출시") == "GZF1_5G"
    assert match_product_code("Galaxy Z Flip 5G 가격") == "GZFL1_5G"


def test_old_phones_korean():
    """한국어 변형."""
    assert match_product_code("갤럭시 노트 엣지 사용기") == "GNEDGE"
    assert match_product_code("갤럭시 노트 FE 리퍼") == "GNFE"
    assert match_product_code("갤럭시 그랜드 프라임+ 후기") == "GGRPRMP"
    assert match_product_code("갤럭시 메가 6.3 큰 화면") == "GMEGA63"
    assert match_product_code("갤럭시 에이스 3 SK") == "GACE3"
    assert match_product_code("갤럭시 온7 가성비") == "GON7"


# ─────────────────────────────────────────────────────────────────────
# R7 회귀 — 기존 (Tab S11/Watch9/A57/Wide8/Jump4/Note 12.2/SM-S938) 유지
# ─────────────────────────────────────────────────────────────────────
def test_r7_regression():
    """R7 (test_relink_v2) 의 핵심 케이스가 깨지지 않는다."""
    assert match_product_code("Galaxy Tab S11 Ultra keyboard pro") == "GTABS11U"
    assert match_product_code("갤럭시워치9 가격인상") == "GW9"
    assert match_product_code("Galaxy A57 is not released") == "GA57"
    assert match_product_code("갤럭시와이드8 굿락") == "GWIDE8"
    assert match_product_code("갤럭시 점프 4. 25만원 입니다") == "GJUMP4"
    assert match_product_code("Galaxy Note 12.2 still works") == "GNT122"
    assert match_product_code("https://doc.samsungmobile.com/SM-S938N/KOO/doc.html") == "GS25U"
    # 노이즈 차단
    assert match_product_code("I love my Xiaomi Note 12 LTE") is None
    assert match_product_code("Wait for S27 next year") is None


# ─────────────────────────────────────────────────────────────────────
# 사전 규모 검증 — R7 대비 대폭 확장
# ─────────────────────────────────────────────────────────────────────
def test_dict_and_regex_size_r8():
    """MODEL_MAP / REGEX 모두 R7 대비 충분히 확장 (≥320 / ≥240)."""
    assert len(MODEL_MAP) >= 230
    assert len(MODEL_REGEX_PATTERNS) >= 240
