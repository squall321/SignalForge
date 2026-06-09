"""R8 트랙 C — Samsung SM-XXX SKU 사전 단위 테스트.

scripts/relink_products.match_product_code 가 Samsung 공식 SKU 코드를
정확한 product code 로 매칭하는지 검증한다.

검증 범위:
  1. Galaxy S 시리즈 (S22~S25 + 옛 S6~S10)
  2. Galaxy Note 시리즈 (Note 4~Note 20U)
  3. Galaxy A 시리즈 (A12~A57)
  4. Galaxy Z Fold/Flip (전 세대)
  5. SM 하이픈 없는 변형 (SMS921, SM-S921N, SMS921U 등)
  6. 갤럭시 컨텍스트 없는 SM 코드도 매칭 (가장 명시적)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from relink_products import match_product_code  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 1. Galaxy S 시리즈 SKU
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SM-S921N 사용기", "GS24"),
    ("Samsung SM-S928U review", "GS24U"),
    ("sm-s926u 후기", "GS24P"),
    ("SM-S931 Galaxy S25 unboxing", "GS25"),
    ("SM-S938 Ultra leaked", "GS25U"),
    ("Got my SM-S908N S22 Ultra", "GS22U"),
    ("SM-S741U S24 FE photo test", "GFE24"),
    ("SM-G998B S21 Ultra display issue", "GS21U"),
    ("SM-G973F S10 still going", "GS10"),
    ("SM-G930S Galaxy S7 batt", "GS7"),
])
def test_galaxy_s_skus(text, expected):
    assert match_product_code(text) == expected


# ════════════════════════════════════════════════════════════════════
# 2. Galaxy Note 시리즈 SKU
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SM-N930F Note 7 explosion", "GN7"),
    ("SM-N986B Note 20 Ultra", "GN20U"),
    ("SM-N975F Note 10+ battery", "GN10P"),
    ("SM-N960F Note 9", "GN9"),
    ("SM-N910F still works", "GN4"),
])
def test_galaxy_note_skus(text, expected):
    assert match_product_code(text) == expected


# ════════════════════════════════════════════════════════════════════
# 3. Galaxy A 시리즈 SKU
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SM-A566S 갤럭시 A56", "GA56"),
    ("SM-A556B A55 review", "GA55"),
    ("SM-A546B A54 unboxing", "GA54"),
    ("SM-A536B A53 5G", "GA53"),
    ("SM-A526B A52 5G photo", "GA52"),
    ("SM-A525F A52 4G", "GA52"),
    ("SM-A325F A32 4G batt drain", "GA32"),
    ("SM-A156 A15 5G", "GA15"),
    ("SM-A576B A57 leaked specs", "GA57"),
])
def test_galaxy_a_skus(text, expected):
    assert match_product_code(text) == expected


# ════════════════════════════════════════════════════════════════════
# 4. Galaxy Z Fold/Flip SKU
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SM-F966B Z Fold 7", "GZF7"),
    ("SM-F956B Fold 6", "GZF6"),
    ("SM-F946B Fold 5 crease", "GZF5"),
    ("SM-F900F original Fold 1", "GZF1"),
    ("SM-F761B Flip 7", "GZFL7"),
    ("SM-F741B Flip 6", "GZFL6"),
    ("SM-F700N first Flip", "GZFL1"),
])
def test_galaxy_z_skus(text, expected):
    assert match_product_code(text) == expected


# ════════════════════════════════════════════════════════════════════
# 5. 하이픈 변형 — SMS921, SM S921, SM-S921N 모두 매칭
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SMS921 unboxing", "GS24"),       # 하이픈 없음
    ("sm-s921 leak", "GS24"),          # lowercase
    ("SM-S921N Korean variant", "GS24"),  # 리전 접미사
    ("SM-S921U US variant", "GS24"),   # US 변형
    ("SM-S921B EU variant", "GS24"),
])
def test_sm_hyphen_variants(text, expected):
    assert match_product_code(text) == expected


# ════════════════════════════════════════════════════════════════════
# 6. SKU 우선순위 — SM 코드는 갤럭시 컨텍스트 없이도 매칭
# ════════════════════════════════════════════════════════════════════
def test_sku_overrides_without_galaxy_context():
    """SM-XXX 는 가장 명시적이므로 갤럭시/삼성 단어 없이도 매칭."""
    # 'sm-s921' 만 있고 galaxy/samsung 컨텍스트 없음 — 그래도 매칭.
    assert match_product_code("anyone tried SM-S921?") == "GS24"


def test_sku_overrides_substring_map():
    """SM 코드가 substring MODEL_MAP 보다 우선되어야 함.

    'galaxy s10' substring 이 있어도 SM-S921 매칭이 먼저 일어나야 한다.
    (정규식 패턴이 substring 매칭보다 먼저 실행됨)
    """
    assert match_product_code("galaxy s10 vs SM-S921 comparison") == "GS24"


# ════════════════════════════════════════════════════════════════════
# 7. M/F/XCover SKU
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("SM-M156B M15 5G", "GM14"),
    ("SM-M476B M44 review", "GM34"),
    ("SM-M566B M55 5G", "GM55"),
    ("SM-G715FN XCover Pro", "GXC5"),
])
def test_m_f_xcover_skus(text, expected):
    assert match_product_code(text) == expected
