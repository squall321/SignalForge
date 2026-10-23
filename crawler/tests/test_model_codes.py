# 모델코드 사전(SM-S938B → GS25U)의 해석·일관성과 태깅 길목 연결을 고정한다 (DB 없이)
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base import model_codes as M  # noqa: E402
from base import product_match as pm  # noqa: E402


@pytest.mark.parametrize("text,base", [
    ("SM-S938B", "S938"), ("sm-s938b/ds", "S938"), ("SMS9380", "S938"), ("SM-S938BZKDEUB", "S938"),
    ("(SM-R800)", "R800"), ("SM-X100A_KOR_SKT", "X100"), ("SM-S928U1", "S928"),
])
def test_base_code_from_variants(text, base):
    assert M.base_of(text) == base


@pytest.mark.parametrize("text", ["xsm-s938", "SM-S93", "osm-s938b", "ism938"])
def test_not_a_model_code(text):
    assert M.base_of(text) is None


def test_exact_and_family_are_disjoint_and_unique():
    assert not set(M.EXACT) & set(M.FAMILY)
    assert all(len(v) >= 2 for v in M.FAMILY.values()), "세대 하나짜리면 EXACT 다"


def test_ultra_codes_follow_the_numbering():
    # 끝자리 8 = Ultra — S22~S26 가 한 줄로 맞아야 번호 체계로 지은 사전이 믿을 만하다
    assert [M.product_of(c) for c in ("S908", "S918", "S928", "S938", "S948")] == \
        ["GS22U", "GS23U", "GS24U", "GS25U", "GS26U"]


@pytest.mark.parametrize("code", ["S906", "S937", "S741", "L715", "F761", "F971"])
def test_catalog_gaps_are_not_mapped(code):
    """카탈로그에 없는 모델(S22+ · S25 Edge · S26 FE · Watch Ultra 2 · Flip7 FE …)을 가까운 제품에 붙이면 오귀속이다."""
    assert M.product_of(code) is None and M.family_of(code) == ()


def test_unreleased_generation_is_family_only():
    assert M.family_of("S958") == ("GS27", "GS27P", "GS27U") and M.product_of("S958") is None


def test_tagging_uses_model_code_when_names_are_absent():
    assert pm.infer_all_product_codes("My Galaxy Watch 46mm (SM-R800) won't pair") == [("GW1", "primary")]


def test_tagging_adds_code_only_products_as_mentioned():
    got = pm.infer_all_product_codes("SM-S948B and SM-S942B spotted on Geekbench")
    assert got == [("GS26U", "primary"), ("GS26", "mentioned")], got


def test_tagging_does_not_change_named_posts_or_use_family():
    assert pm.infer_all_product_codes("Galaxy S25 Ultra SM-S938B overheating") == [("GS25U", "primary")]
    assert pm.infer_all_product_codes("SM-S958U leak") == [], "출시 전 세대 코드는 본문에 붙이지 않는다"
