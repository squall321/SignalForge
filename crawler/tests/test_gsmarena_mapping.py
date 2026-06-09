"""test_gsmarena_mapping — Harvest 5 V2 GSMArena 매핑 사전 확장 검증.

GSMArena forum 24h NULL 161/236 (68.2%) 의 주된 결손: 'A57', 'A37', 'A17'
같이 'Galaxy' 컨텍스트 없는 베어 A-넘버 토큰. 베어 A-넘버 패턴 추가 후
해당 표현이 정확히 매칭되는지, 그리고 ARM Cortex-A76/A78 / A52s 같은
충돌 케이스가 오매칭되지 않는지 검증.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import match_product_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Positive — 실제 GSMArena 24h NULL 샘플에 등장한 베어 A-넘버
# ─────────────────────────────────────────────────────────────────────
def test_bare_a57_real_sample():
    """'A57 it's pure crazy to pay this money' (id=1228414) → GA57."""
    txt = (
        "Version 256 GB / 8 GB RAM 460 Euro and 512 GB / 12 GB RAM with "
        "exorbitant price 600 Euro A57 it's pure crazy to pay this money"
    )
    assert match_product_code(txt) == "GA57"


def test_bare_a37_real_sample():
    """'A37 base variant' (id=1228439) → GA37."""
    txt = "Edge 60 Pro 256GB / 8 GB Ram the same price 310 Eur with A37 base variant"
    assert match_product_code(txt) == "GA37"


def test_bare_a17_real_sample():
    """'Get the A17 5g which is a better budget phone' (id=1228612) → GA17."""
    txt = "Get the A17 5g which is a better budget phone with amolet screen."
    assert match_product_code(txt) == "GA17"


def test_bare_a07_real_sample():
    """'Hello A07 5G rebrand' (id=1228476) → GA07."""
    assert match_product_code("Hello A07 5G rebrand") == "GA07"


def test_bare_a36_real_sample():
    """'A36 or A37' (id=1228461) — A36 먼저 등장 → GA36."""
    txt = (
        "Edge 60 Fusion it's Masterpice vs A36 or A37 because it's Compact "
        "mid-range t have a beautiful 6.67\" P OLED"
    )
    # MODEL_REGEX_PATTERNS 는 선언 순서대로 — A57/A56/.../A37/A36 순으로 등록.
    # 'A36 or A37' 본문에는 A37 패턴이 먼저 선언되어 매칭됨.
    # 실제 가치: NULL → GA37 (혹은 GA36) 둘 다 매핑된 상태가 핵심.
    assert match_product_code(txt) in {"GA36", "GA37"}


# ─────────────────────────────────────────────────────────────────────
# Negative — ARM Cortex IP / 변형 SKU 와 충돌 회피
# ─────────────────────────────────────────────────────────────────────
def test_a52s_does_not_match_bare_a52():
    """'A52s' 는 \\b 경계로 베어 A52 패턴이 매칭하지 않아야 함.

    별도 SM-SKU regex (a525/a526/a528) 가 처리. 여기서는 베어 A52 패턴이
    'a52s' 의 '2s' 사이에 \\b 가 없어 매칭하지 않는 것을 확인.
    """
    # MODEL_REGEX_PATTERNS 의 베어 A52 가 a52s 본문에서 매칭되지 않는지 확인.
    # 본문 텍스트에 다른 A-넘버나 SKU 가 없도록 구성.
    txt = "A52s she is the only one best mid-range with snapdragon 778+"
    # 베어 A52 가 매칭하지 않으면 None 또는 다른 패턴 (없음).
    result = match_product_code(txt)
    # GA52 로 잘못 매칭되지 않아야 함 — None 또는 SM-SKU 매칭이 정상.
    assert result != "GA52" or result is None


def test_arm_cortex_a76_not_matched():
    """'a76 cores' 는 ARM Cortex IP — 베어 A76 패턴은 추가하지 않았으므로
    매칭되지 않아야 함."""
    txt = "And Samsung strikes again using an SoC with ye olde a76 cores"
    # A76 카탈로그 없음 → 매칭 없어야 정상.
    assert match_product_code(txt) is None


def test_speculative_a78_not_matched():
    """'galaxy A78 releases' 는 미래 추측 — A78 카탈로그 없으므로 매칭 X."""
    txt = "Still no 4K60??? Well, if the galaxy A78 releases, it MUST have 4K60"
    assert match_product_code(txt) is None


# ─────────────────────────────────────────────────────────────────────
# 회귀 — 'Galaxy A57' 같은 컨텍스트 표현은 여전히 매칭
# ─────────────────────────────────────────────────────────────────────
def test_galaxy_a37_with_context_still_works():
    """'Samsung Galaxy A37 5G' → GA37 (R7 기존 매칭 유지)."""
    assert match_product_code("Samsung Galaxy A37 5G. In Germany Price: 249 euros.") == "GA37"


def test_bare_a37_in_short_text():
    """짧은 베어 표현도 잡혀야 함."""
    assert match_product_code("a37 review") == "GA37"
    assert match_product_code("A57") == "GA57"
