"""test_xda_mapping_v2 — Harvest 7 Track X1: xda NULL 매핑 강화 검증.

배경:
  Harvest 6 진단에서 xda NULL 85/149 (57%) 확인. 키워드 분석에서 Galaxy Buds+,
  Galaxy Watch 4, Galaxy Z Flip 같은 *기존 패턴이 이미 매칭하는* 항목이 다수.
  즉 신규 패턴이 필요한 진짜 빈자리는 다음 1종이었음:

    - 'Samsung Galaxy Tab A (2019)' / '8\" Samsung Galaxy Tab A (2019)'
      → 옛 모델 GTA10_19 / GTA8_19 (카탈로그 보유)

  나머지 미매칭 라인은 'Samsung Galaxy Watch' (세대 미지정) / 'Samsung TVs'
  같이 제품 미특정·카테고리성이라 의도적으로 매핑 금지 — 노이즈 회피.

이 테스트는:
  1) 신규 Tab A (2019) 패턴이 8\" 명시·일반형 모두 정확히 매칭함을 확인.
  2) 기존 Tab A8 / Tab A7 / Tab A11 등 회귀가 없음을 확인.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import match_product_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Positive — 신규 Tab A (2019) 패턴
# ─────────────────────────────────────────────────────────────────────
def test_tab_a_2019_8inch_explicit_real_sample():
    """xda 실제 NULL 샘플: '8\" Samsung Galaxy Tab A (2019)' → GTA8_19.

    더 구체적인 8" 인치 명시가 더 일반적인 2019 패턴보다 먼저 매칭되어야 함.
    """
    txt = (
        '[Update 2: Available in India] The 8" Samsung Galaxy Tab A (2019) '
        'is coming with the Qualcomm Snapdragon 429'
    )
    assert match_product_code(txt) == "GTA8_19"


def test_tab_a_2019_generic_falls_back_to_10inch():
    """인치 명시 없는 'Galaxy Tab A (2019)' / 'Galaxy Tab A 2019' → GTA10_19.

    카탈로그상 10.1" 모델이 더 흔하므로 기본값으로 통합.
    """
    assert match_product_code("Samsung Galaxy Tab A (2019) review") == "GTA10_19"
    assert match_product_code("Galaxy Tab A 2019 update") == "GTA10_19"


# ─────────────────────────────────────────────────────────────────────
# Regression — 기존 Tab A 매칭 유지
# ─────────────────────────────────────────────────────────────────────
def test_tab_a8_a7_a11_regression():
    """연식 미표기 Tab A8 / A7 / A11 은 기존 패턴이 그대로 매칭."""
    assert match_product_code("Galaxy Tab A8 review") == "GTABA8"
    assert match_product_code("Samsung Galaxy Tab A7 deal") == "GTA7"
    assert match_product_code("Galaxy Tab A11 announced") == "GTABA11"


def test_existing_xda_samples_still_match():
    """xda NULL 샘플 중 기존 패턴이 이미 잡는 라인은 회귀 없이 그대로 매칭.

    Harvest 7 X1 분석에서 36/85 라인이 *기존 matcher* 만으로 회수 가능하다고
    확인됨 (PRESERVE relink 시 자연스럽게 채워짐). 대표 5건을 회귀 케이스로 고정.
    """
    assert match_product_code(
        "Samsung Galaxy Buds+ go on sale in a new Aura Blue color"
    ) == "GBPLUS"
    assert match_product_code(
        "This Samsung Galaxy Watch 4 deal is too damn good to miss"
    ) == "GW4"
    assert match_product_code("Galaxy S2, 15 years later") == "GS2"
    assert match_product_code(
        "Samsung Galaxy A21s launched with 48MP quad rear camera"
    ) == "GA21S"
    assert match_product_code(
        "iPad Pro (M4, 2024) vs Samsung Galaxy Tab S9+: Which should you buy?"
    ) == "GTABS9P"
