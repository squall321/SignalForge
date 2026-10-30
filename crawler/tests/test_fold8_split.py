# 8세대 폴드 둘(Fold8 Ultra · 넓은 폼팩터 Fold8)을 매처가 가르는지 — 비교글은 둘 다, 타사 'Fold8 Ultra' 는 안 잡는다
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.product_match import infer_all_product_codes  # noqa: E402


def _codes(text):
    return [c for c, _ in infer_all_product_codes(text, legacy=False)]


@pytest.mark.parametrize("text", [
    "Galaxy Z Fold8 Ultra hinge creaks after a month",
    "z fold 8 ultra battery drain",
    "Fold8 Ultra vs iPhone Duo",
    "갤럭시 Z 폴드8 울트라 발열",
    "폴드 8 울트라 액정 들뜸",
    "폴8 울트라 샀어요",
])
def test_ultra_names_go_to_the_ultra_code(text):
    got = _codes(text)
    assert got and got[0] == "GZF8U" and "GZF8" not in got, (text, got)


@pytest.mark.parametrize("text", [
    "Galaxy Z Fold8 hinge dust",
    "z fold 8 wide screen is great",
    "폴드8 배터리",
    "폴드 와이드 화면 비율",
    "와이드폴드 케이스 추천",
])
def test_plain_and_wide_fold8_stay_on_gzf8(text):
    got = _codes(text)
    assert got and got[0] == "GZF8" and "GZF8U" not in got, (text, got)


def test_comparison_keeps_both_models():
    """GZF8 의 첫 매칭이 'Fold8 Ultra' 안에 걸리면 비교글에서 GZF8 이 사라졌다 — 뒤의 'Fold8' 을 잡아야 한다."""
    for text in ("Fold8 Ultra vs Fold8 — which one?", "폴드8 울트라랑 폴드8 고민 중", "I have both the Fold 8 and the Fold 8 Ultra"):
        got = _codes(text)
        assert {"GZF8", "GZF8U"} <= set(got), (text, got)


def test_rival_fold8_ultra_is_not_samsung():
    assert "GZF8U" not in _codes("vivo X Fold8 Ultra launched in China")


def test_generation_and_series_include_the_ultra():
    for p in ("/shared", str(ROOT.parent / "shared")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import re
    import ontology as O
    assert O.family_key("GZF8U") == "GZF8" == O.family_key("GZF8")
    assert re.match(O.SERIES["zfold"]["code_regex"], "GZF8U") and not re.match(O.SERIES["zfold"]["code_regex"], "GZFL8")


def test_model_codes_split_the_two_fold8s():
    """F976 계열은 Fold8 Ultra, F971 계열은 넓은 폼팩터 Fold8(2026-10-31 확정 — 그 전에는 F976 이 GZF8, F971 은 공백)."""
    assert _codes("SM-F976U teardown") == ["GZF8U"] and _codes("SM-F9760 hinge") == ["GZF8U"]
    assert _codes("SM-F971B screen protector") == ["GZF8"]
    got = _codes("Galaxy Z Fold8 review — I tested the SM-F976B too")
    assert got[0] == "GZF8" and "GZF8U" in got, "이름이 대표, 모델코드로만 나온 제품은 함께 언급"
