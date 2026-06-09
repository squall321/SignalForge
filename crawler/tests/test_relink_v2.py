"""test_relink_v2 — R7 매핑 사전 확장 검증.

Track A R7 (2026-06-04) — Tab/Watch9/A07~57/M/F/XCover/Wide/Jump/Note Pro 등
신규 catalog/regex 패턴 매칭 정확성 + 노이즈 차단 검증.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import (  # noqa: E402
    match_product_code,
    MODEL_MAP,
    MODEL_REGEX_PATTERNS,
    NOISE_PATTERNS,
)


# ─────────────────────────────────────────────────────────────────────
# S22~S26 — Discovery 가 미커버라 잡은 핵심 케이스
# ─────────────────────────────────────────────────────────────────────
def test_s25_variants():
    """S25 / S25+ / S25 Ultra 매칭."""
    assert match_product_code("I just got an S25 and love it") == "GS25"
    assert match_product_code("S25 Ultra 카메라 비교") == "GS25U"
    assert match_product_code("Galaxy S25+ 사용기") == "GS25P"
    assert match_product_code("갤s25") == "GS25"


def test_s22_to_s24_variants():
    """S22 / S23 / S24 정상 매칭 + Ultra/Plus/FE 분기."""
    assert match_product_code("S22 폰을 4년째 쓰고있는데") == "GS22"
    assert match_product_code("Galaxy S22 Ultra 256GB") == "GS22U"
    assert match_product_code("S23 Ultra 카메라 짱") == "GS23U"
    assert match_product_code("Galaxy S24 review") == "GS24"
    assert match_product_code("s24 fe 가성비") == "GFE24"


# ─────────────────────────────────────────────────────────────────────
# Fold/Flip 5~8 — R7 catalog 확장 분
# ─────────────────────────────────────────────────────────────────────
def test_fold5_to_7_and_flip5_to_7():
    """Fold/Flip 5~7 한국어/영문 매칭."""
    assert match_product_code("갤럭시 Z 폴드7 출시일") == "GZF7"
    assert match_product_code("Galaxy Z Fold6 hinge durability") == "GZF6"
    assert match_product_code("폴드5 무게가 줄어서 좋네") == "GZF5"
    assert match_product_code("Z Flip7 cover screen") == "GZFL7"
    assert match_product_code("플립6 가격") == "GZFL6"


# ─────────────────────────────────────────────────────────────────────
# Watch 6~9 + Ultra
# ─────────────────────────────────────────────────────────────────────
def test_watch_new_variants():
    """Watch6/7/8/9 + Ultra 매칭."""
    assert match_product_code("갤럭시워치9 가격인상") == "GW9"
    assert match_product_code("Galaxy Watch9 spec") == "GW9"
    assert match_product_code("Galaxy Watch Ultra review") == "GWU"
    assert match_product_code("갤워치8 배터리") == "GW8"
    assert match_product_code("Watch7 battery life") == "GW7"


# ─────────────────────────────────────────────────────────────────────
# Tab 시리즈 — catalog 신규 (0010)
# ─────────────────────────────────────────────────────────────────────
def test_tab_s_series_variants():
    """Tab S11 Ultra → GTABS11U, S10 FE → GTABS10F, S9 → GTABS9 등."""
    assert match_product_code("Galaxy Tab S11 Ultra keyboard pro") == "GTABS11U"
    assert match_product_code("갤탭 S10 울트라") == "GTABS10U"
    assert match_product_code("Walmart Galaxy Tab S10 FE discount") == "GTABS10F"
    # R8 (0011) 부터 'Tab S9 FE+' 는 별도 코드 GTS9FP 로 매칭. 'FE' 단독은 GTABS9F.
    assert match_product_code("Samsung Galaxy tab s9 fe+ canada") == "GTS9FP"
    assert match_product_code("Samsung Galaxy tab s9 fe canada") == "GTABS9F"
    assert match_product_code("탭 S9 FE 업그레이드") == "GTABS9F"
    assert match_product_code("Galaxy Tab S8 Ultra 가격") == "GTABS8U"
    assert match_product_code("Galaxy Tab S7+ 256GB") == "GTABS7P"


def test_tab_active5():
    """Tab Active5 Pro 매칭."""
    assert match_product_code("탭 액티브5 프로 One UI 8.5") == "GTABACT5"
    assert match_product_code("Tab Active5 Pro 5G 업데이트") == "GTABACT5"


# ─────────────────────────────────────────────────────────────────────
# A 시리즈 신규 (A07/A16/A17/A26/A27/A36/A37/A57)
# ─────────────────────────────────────────────────────────────────────
def test_a_series_new():
    """Galaxy A 신규 라인업 매칭 (Galaxy 컨텍스트 필수)."""
    assert match_product_code("Galaxy A37, A57은 아직 안나오네요") == "GA37"
    assert match_product_code("Galaxy A36 SM-A366B spec") == "GA36"
    assert match_product_code("갤럭시 A17로 바꿨는데") == "GA17"
    assert match_product_code("Galaxy A27 baratinho FCC") == "GA27"


# ─────────────────────────────────────────────────────────────────────
# XCover / Wide / Jump — 지역/러기드
# ─────────────────────────────────────────────────────────────────────
def test_xcover_wide_jump():
    """XCover7 Pro, Wide N, Jump N 매칭."""
    assert match_product_code("갤럭시 엑스커버7 프로 One UI 8.5") == "GXC7"
    assert match_product_code("Samsung XCover6 Pro waterproof") == "GXC6"
    # R8 (0011) 부터 'XCover Pro' 단독 모델은 GXCPRO 코드로 catalog 등록 → 매칭.
    assert match_product_code("Samsung XCover Pro waterproof") == "GXCPRO"
    assert match_product_code("갤럭시와이드8 굿락") == "GWIDE8"
    assert match_product_code("와이드2 사야지") == "GWIDE2"
    assert match_product_code("갤럭시 점프 4. 25만원 입니다") == "GJUMP4"
    assert match_product_code("점프2 A33 가지고 있는데") == "GJUMP2"


# ─────────────────────────────────────────────────────────────────────
# Note Pro 12.2 — 2014 태블릿 (특수)
# ─────────────────────────────────────────────────────────────────────
def test_note_pro_122():
    """'Note 12.2' 패턴은 GNT122. 'Note 12' (Xiaomi) 와 구분."""
    assert match_product_code("Galaxy Note 12.2 still works great") == "GNT122"


# ─────────────────────────────────────────────────────────────────────
# SM-XXX SKU 코드 — 가장 명시적 신호
# ─────────────────────────────────────────────────────────────────────
def test_sm_code_skus():
    """SM-S938 → GS25U, SM-S921 → GS24, SM-A325 → GA32(미보유) 등."""
    assert match_product_code("https://doc.samsungmobile.com/SM-S938N/KOO/doc.html") == "GS25U"
    assert match_product_code("SM-S921N firmware") == "GS24"
    assert match_product_code("Galaxy S22 Ultra SM-S908B") == "GS22U"
    assert match_product_code("My A55 device SM-A556B") == "GA55"


# ─────────────────────────────────────────────────────────────────────
# 노이즈 차단 — Xiaomi/Infinix Note, S27 미래
# ─────────────────────────────────────────────────────────────────────
def test_noise_blocks_xiaomi_infinix_note():
    """'Xiaomi Note 12' 는 갤럭시 컨텍스트 없으면 None."""
    assert match_product_code("I love my Xiaomi Note 12 LTE") is None
    assert match_product_code("Infinix Note 12 review 2023") is None
    assert match_product_code("Redmi Note 13 vs Note 12") is None


def test_noise_blocks_s27_speculation():
    """S27 단독 (미래 추측) 은 None — 카탈로그 없음."""
    assert match_product_code("S27 나오면 26도 자연으로 차별") is None
    assert match_product_code("Wait for S27 next year") is None


def test_noise_galaxy_context_overrides():
    """갤럭시 컨텍스트가 있으면 노이즈 패턴 무시 — Note 12 라도 GN20 매칭 시도."""
    # 'Samsung Galaxy Xiaomi 비교' 같은 글이면 Xiaomi 차단 안 됨
    # Galaxy S22 같은 명시적 키가 있으면 그게 우선 매칭
    assert match_product_code("Samsung Galaxy S22 vs Xiaomi Note 12") == "GS22"


# ─────────────────────────────────────────────────────────────────────
# R6 회귀 — 기존 매핑이 깨지지 않았는가
# ─────────────────────────────────────────────────────────────────────
def test_r6_regression_note_and_legacy():
    """R6 의 Note 7 / Note 20 Ultra / S10 5G / Z Fold/Flip 1~4 유지."""
    assert match_product_code("My old Note 7 still works") == "GN7"
    assert match_product_code("갤럭시 노트 7 발화 사건") == "GN7"
    assert match_product_code("Galaxy Note 20 Ultra is huge") == "GN20U"
    assert match_product_code("Galaxy S10 5G was first 5G phone") == "GS105G"
    assert match_product_code("z fold 3 hinge issue") == "GZF3"
    assert match_product_code("폴드4 사용 후기") == "GZF4"


def test_no_match_returns_none():
    """매칭 키 없는 텍스트는 None."""
    assert match_product_code("Just a generic comment about phones") is None
    assert match_product_code("") is None
    assert match_product_code("배터리 광탈") is None


# ─────────────────────────────────────────────────────────────────────
# 사전 규모 검증
# ─────────────────────────────────────────────────────────────────────
def test_dict_and_regex_size():
    """MODEL_MAP 사전 + REGEX 패턴 수 검증 — R6 (45) 대비 대폭 확장."""
    assert len(MODEL_MAP) >= 150
    # SM-code + Tab + A07~57 + M/F + XCover/Watch9/Note12.2 = 70+ regex
    assert len(MODEL_REGEX_PATTERNS) >= 70
    assert len(NOISE_PATTERNS) >= 2
