# 인접 브랜드 가드가 사전에 없는 토큰을 만나 크롤 전체를 죽이지 않는지 고정한다.
"""막는 고장(2026-09-18~09-21, 나흘 연속 Reddit 역사 수집 0).

`_rival_adjacent` 가 `_WORD_BRAND[token.lower()]` 로 **직접 인덱싱**했다.
re.IGNORECASE 는 터키어 대문자 İ(U+0130)를 `i` 에 매칭시키므로 'Pİxel' 이
pixel 패턴에 걸리는데, 'Pİxel'.lower() 는 'pi̇xel'(i + U+0307 결합 점)이라
사전에 없다 → KeyError → 크롤 프로세스 사망.

실패 신호는 러너 로그에 한 줄도 오르지 않았다(자식 스크립트가 rc 를 삼켰다).
그래서 '수집이 왜 줄었나'가 나흘간 미궁이었다.

두 축으로 막는다.
  1. 정규식 대안 ↔ 사전 키 동기화 — 조회가 실패할 여지를 없앤다.
  2. 그래도 못 찾으면 예외가 아니라 보수적 판정(경쟁 브랜드로 본다).
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.product_match import (  # noqa: E402
    _ADJ_RIVAL_RE,
    _WORD_BRAND,
    _norm_brand_key,
    _rival_adjacent,
    infer_all_product_codes,
)


def _regex_alternatives():
    """_ADJ_RIVAL_RE 의 브랜드 대안 토큰들."""
    import re
    src = _ADJ_RIVAL_RE.pattern
    body = src.split(r"\b(", 1)[1].split(")", 1)[0]
    return [a for a in body.split("|") if a and re.fullmatch(r"[a-z]+", a)]


def test_every_regex_alternative_is_in_word_brand():
    """정규식이 잡을 수 있는 토큰은 전부 사전에 있어야 한다.

    없으면 그 토큰을 만나는 순간 조회가 실패한다 — 예전엔 곧 KeyError 였다.
    """
    alts = _regex_alternatives()
    assert len(alts) >= 20, f"대안을 제대로 파싱하지 못했다: {alts}"
    missing = [a for a in alts if _norm_brand_key(a) not in _WORD_BRAND]
    assert not missing, f"정규식에는 있고 _WORD_BRAND 에는 없는 토큰: {missing}"


@pytest.mark.parametrize("raw,expected", [
    ("Pİxel", "pixel"),      # 터키어 대문자 I — 실제 사고 입력
    ("PİXEL", "pixel"),
    ("pixel", "pixel"),
    ("PIXEL", "pixel"),
    ("İphone", "iphone"),
    ("ıphone", "ıphone"),    # 터키어 점없는 ı 는 i 가 아니다 — 억지로 합치지 않는다
])
def test_brand_key_normalization(raw, expected):
    assert _norm_brand_key(raw) == expected


def test_unknown_token_does_not_raise():
    """사전에 없는 토큰이 와도 예외로 죽지 않는다."""
    import re
    from base import product_match as pm
    saved = pm._WORD_BRAND.copy()
    try:
        pm._WORD_BRAND.pop("pixel", None)      # 동기화가 깨진 상태를 만든다
        # 예외 없이 bool 을 돌려줘야 한다
        got = _rival_adjacent("Pixel Galaxy S25", "GS25", len("Pixel "))
        assert isinstance(got, bool)
    finally:
        pm._WORD_BRAND.clear(); pm._WORD_BRAND.update(saved)


def test_unknown_token_is_treated_as_rival():
    """못 찾으면 '경쟁 브랜드'로 본다 — 이 가드의 목적은 오귀속 방지다."""
    from base import product_match as pm
    saved = pm._WORD_BRAND.copy()
    try:
        pm._WORD_BRAND.pop("pixel", None)
        assert _rival_adjacent("Pixel Galaxy S25", "GS25", len("Pixel ")) is True
    finally:
        pm._WORD_BRAND.clear(); pm._WORD_BRAND.update(saved)


@pytest.mark.parametrize("text", [
    "Pİxel 9 vs Galaxy S25",
    "PİXEL 9 Galaxy S25 karşılaştırma",
    "İphone 17 Galaxy S26 Ultra",
    "Galaxy S25 vs Pİxel 9 kamera",
])
def test_turkish_input_does_not_crash_inference(text):
    """실제 사고 입력 형태 — 추론 전체가 예외 없이 끝나야 한다."""
    got = infer_all_product_codes(text)
    assert isinstance(got, list)


def test_turkish_pixel_still_attributed_to_google():
    """죽지 않는 것만으로는 부족하다 — 오귀속도 없어야 한다."""
    got = dict(infer_all_product_codes("Pİxel 9 vs Galaxy S25"))
    assert "GS25" in got, got
    assert any(c.startswith("PX") for c in got), got


def test_guard_only_fires_when_brand_is_adjacent():
    """가드는 브랜드가 매칭 구간에 바로 붙어 있을 때만 발동한다.

    'Pixel 9 Galaxy S25' 처럼 사이에 다른 토큰이 있으면 발동하지 않는다 —
    이 경계를 모르면 위 테스트들이 엉뚱한 오프셋을 써서 아무것도 검증하지 못한다.
    """
    assert _rival_adjacent("Pixel Galaxy S25", "GS25", len("Pixel ")) is True
    assert _rival_adjacent("Pixel 9 Galaxy S25", "GS25", len("Pixel 9 ")) is False
