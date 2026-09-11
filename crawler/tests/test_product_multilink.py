# 다대다 제품 추출(infer_all_product_codes) 단위 테스트 — span 겹침 억제·역할 판정·primary 정합
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.product_match import (  # noqa: E402
    infer_all_product_codes,
    infer_product_code,
)


def codes(text):
    return [c for c, _ in infer_all_product_codes(text)]


def roles(text):
    return dict(infer_all_product_codes(text))


# ── span 겹침 억제 — 이 함수의 핵심 위험 ──────────────────────────────
@pytest.mark.parametrize("text,expected", [
    # 'Galaxy S26 Ultra' 는 GS26U(7,16) 와 GS26(0,10) 을 동시 매칭 → GS26 억제
    ("Galaxy S26 Ultra camera issue", ["GS26U"]),
    ("갤럭시 S26 울트라 배터리", ["GS26U"]),
    ("Galaxy Z Fold8 hinge dust", ["GZF8"]),
    ("Galaxy S25 Ultra 화면", ["GS25U"]),
])
def test_overlap_suppressed(text, expected):
    assert codes(text) == expected


# ── 겹치지 않는 다중 제품은 모두 보존 (비교글 신호) ────────────────────
def test_comparison_keeps_both():
    got = codes("S26 Ultra vs Fold8 어느 게 나은가")
    assert "GS26U" in got and "GZF8" in got


def test_three_products():
    got = codes("Galaxy S26 Ultra, Galaxy Z Fold8, iPhone 16 Pro 비교")
    assert {"GS26U", "GZF8", "AP16P"} <= set(got)


# ── 역할 판정 ────────────────────────────────────────────────────────
def test_primary_is_first():
    out = infer_all_product_codes("Galaxy S26 Ultra vs Galaxy Z Fold8")
    assert out[0][1] == "primary"
    assert sum(1 for _, r in out if r == "primary") == 1


def test_compared_role_when_marker():
    r = roles("Galaxy S26 Ultra vs Galaxy Z Fold8")
    assert r["GZF8"] == "compared"


def test_mentioned_role_without_marker():
    r = roles("Galaxy S26 Ultra 쓰다가 Galaxy Z Fold8 샀다")
    assert r["GZF8"] == "mentioned"


# ── primary 는 기존 infer_product_code 와 항상 일치해야 함 (product_id 정합) ──
@pytest.mark.parametrize("text", [
    "Galaxy S26 Ultra vs Fold8",
    "갤럭시 Z 폴드8 힌지에 먼지",
    "iPhone 16 Pro overheating after update",
    "갤럭시 워치9 페어링 안 됨",
    "Galaxy A57 카메라",
    "아무 제품도 없는 잡담",
])
def test_primary_matches_single_infer(text):
    out = infer_all_product_codes(text)
    single = infer_product_code(text)
    if single is None:
        assert out == []
    else:
        assert out[0][0] == single
        assert out[0][1] == "primary"


# ── 회귀 — 매치 없음/빈 입력 ─────────────────────────────────────────
@pytest.mark.parametrize("text", ["", None, "그냥 일반 잡담입니다", "Galaxy Watch"])
def test_no_match(text):
    assert infer_all_product_codes(text) == []


# ── 중복 코드가 나오지 않아야 함 (링크 테이블 PK 충돌 방지) ───────────
def test_no_duplicate_codes():
    got = codes("Galaxy S26 Ultra 좋다. S26 Ultra 정말 좋다. Fold8 도 괜찮다.")
    assert len(got) == len(set(got))


# ── primary 는 선언 순서가 아니라 문서 주제로 고른다 ───────────────────
def test_primary_follows_title_not_pattern_order():
    """제목이 S25+ 화재인 기사가 본문의 S26 언급 때문에 GS26U 로 가면 안 된다."""
    text = ("Another Galaxy S25+ caught fire while charging\n"
            "Samsung denies compensation. The Galaxy S26 Ultra launch is unaffected.")
    out = infer_all_product_codes(text)
    assert out[0] == ("GS25P", "primary")
    assert dict(out)["GS26U"] != "primary"


def test_primary_unchanged_without_title():
    """제목 줄이 없는 짧은 글은 재랭킹하지 않고 현행 순서를 유지한다."""
    text = "S26 Ultra 좋다는데 Fold8 도 궁금"
    assert infer_all_product_codes(text)[0] == ("GS26U", "primary")


def test_single_candidate_identical():
    """후보가 하나면 제목 유무와 무관하게 결과가 이전과 같다."""
    for t in ("Galaxy S26 Ultra 카메라 문제\n어제부터 초점이 안 맞는다",
              "Galaxy S26 Ultra 카메라 문제"):
        assert infer_all_product_codes(t) == [("GS26U", "primary")]


# ── 타 브랜드 인접 가드 — 브랜드 한정자 없는 패턴이 타사 기기를 삼키던 문제 ──
@pytest.mark.parametrize("text,gone", [
    ("The Apple Watch Ultra 4 may pair the sensor upgrades", "GWU"),
    ("Xiaomi Redmi Watch 6 Lite Leaked: Features and Prices", "GW6"),
    ("vivo introduced vivo X Fold6 in the domestic market", "GZF6"),
    ("bought my partner the Apple Watch 7 last year", "GW7"),
    ("JBL Flip 7 speaker deal, regularly $1099.99", "GZFL7"),
    ("Oneplus buds 4 리뷰 기다리는 중", "GB4"),
    ("Moto Buds 2 with ANC at an excellent price", "GB2"),
])
def test_rival_brand_adjacent_suppressed(text, gone):
    assert gone not in codes(text)


@pytest.mark.parametrize("text,kept", [
    ("Galaxy Watch Ultra 배터리가 하루도 안 간다", "GWU"),
    ("Samsung Galaxy Z Fold 6 힌지 유격", "GZF6"),
    ("갤럭시 워치6 페어링 실패", "GW6"),
])
def test_own_brand_not_suppressed(text, kept):
    assert kept in codes(text)


def test_comparison_prose_not_suppressed():
    """사이에 다른 말이 끼면 진짜 비교문이므로 살려야 한다 — 비교글 신호 보존."""
    got = codes("compare the iPhone 15 with the S24 and see which wins")
    assert "GS24" in got and "AP15" in got


def test_later_clean_occurrence_rescued():
    """앞 출현만 타사인 글에서 뒤의 진짜 매칭은 살아야 한다."""
    got = codes("Apple Watch Ultra 2 vs Galaxy Watch Ultra 성능 비교")
    assert "GWU" in got


def test_candidate_set_and_roles_preserved():
    """재선정은 후보 집합과 compared/mentioned 판정을 바꾸지 않는다."""
    text = ("Galaxy Z Fold 8 vs Galaxy S26 Ultra 비교\n"
            "Fold 8 이 더 낫다. Fold 8 배터리도 좋다.")
    out = infer_all_product_codes(text)
    assert {c for c, _ in out} == {"GZF8", "GS26U"}
    assert out[0] == ("GZF8", "primary")
    assert dict(out)["GS26U"] == "compared"


# ── 경쟁사 카탈로그 (폰·웨어러블 380종) ───────────────────────────────
@pytest.mark.parametrize("text,want", [
    ("Apple Watch Ultra 3 battery drains overnight", "AWU3"),
    ("iPhone 17 Pro Max 발열이 심해요", "AP17PM"),
    ("Pixel 10 Pro XL screen flicker after update", "PX10PXL"),
    ("Vivo X Fold6 hinge creaking", "VVXF6"),
    ("Redmi Note 14 Pro 충전 안 됨", "RMN14P"),
    ("OnePlus 13 alert slider broken", "OP13"),
    ("Huawei Mate 60 Pro signal drop", "HWM60"),
    ("Garmin fenix 8 GPS drift", "GMNFX8"),
    ("Sony WH-1000XM6 left cup rattling", "SNAWH6"),
    ("Nothing Phone (3) glyph dead", "NTP3"),
    ("Motorola Edge 70 curved display crack", "MTEDGE70"),
    ("AirPods Pro 3 case not charging", "ABP3"),
])
def test_competitor_catalog_tagged(text, want):
    got = infer_all_product_codes(text)
    assert got and got[0][0] == want, got


def test_competitor_does_not_steal_samsung():
    """경쟁사 추가가 삼성 매칭을 가로채면 안 된다."""
    for t, want in [("갤럭시 Z 폴드8 힌지 먼지", "GZF8"),
                    ("Galaxy Watch Ultra 2 스트랩", "GWU"),
                    ("Galaxy Buds 4 Pro ANC", "GB4P")]:
        assert infer_all_product_codes(t)[0][0] == want


def test_multi_brand_comparison_all_kept():
    got = codes("Best smartwatch 2026: Galaxy Watch 9 vs Apple Watch Series 12 "
                "vs Pixel Watch 5 vs Garmin fenix 8")
    assert {"GW9", "AWS12", "PW5", "GMNFX8"} <= set(got)


def test_buds_pro_reversed_alias():
    """'buds pro 4' 어순 역전형 — 없으면 타사 코드로 넘어갔다."""
    assert infer_all_product_codes("I got the buds pro 4s last month")[0][0] == "GB4P"
    assert infer_all_product_codes("OnePlus Buds Pro 3 review")[0][0] == "OPBP3"


# 삼성 코드 접두사 — _brand_of 의 기본값이 samsung 이므로, 여기 없는 접두사가
# samsung 으로 해석되면 그것은 **경쟁사 접두사를 _CODE_BRAND_PREFIX 에 등록하지
# 않은 것**이다. 삼성 라인을 새로 추가할 때는 이 목록에도 넣어야 한다.
_SAMSUNG_PREFIXES = ("GS", "GA", "GW", "GB", "GZ", "GN", "GM", "GJ", "GF",
                     "GR", "GO", "GX", "GV", "GG", "TAB", "WIDE", "JUMP")


def test_new_codes_resolve_to_own_brand():
    """_CODE_BRAND_PREFIX 누락 시 가드가 자기 브랜드 매칭을 삼킨다."""
    from base.product_match import PRODUCT_PATTERNS, _brand_of
    samsung_like = [c for c, _ in PRODUCT_PATTERNS
                    if _brand_of(c) == "samsung"
                    and not c.startswith(_SAMSUNG_PREFIXES)]
    assert samsung_like == [], f"경쟁사 접두사 미등록 의심: {samsung_like}"


# ── 레거시 사전 폴백 게이트 ──────────────────────────────────────────
@pytest.mark.parametrize("text,code", [
    ("I got a xiaomi redmi note 3 about a month ago", "GN3"),
    ("홍미노트7은 레드도 소개 이미지에 나오긴 하네요", "GN7"),
    ("Xiaomi Watch S4 系列智能手表新版本内测开启", "GW6"),
    ("Am I really the only one to think that the Apple Watch is just ugly?", "GGS"),
])
def test_legacy_gate_rejects_rival(text, code):
    """레거시 사전이 타사 기기를 삼성 코드로 준 경우 게이트가 막아야 한다."""
    from base.product_match import accept_legacy_code
    assert accept_legacy_code(text, code) is False


@pytest.mark.parametrize("text,code", [
    ("Samsung's Gear S3 and the LG Watch Sport have LTE for $350.", "GGS3"),
    ("I ended up getting an apple watch, then returning it for Gear S3.", "GGS3"),
    ("The Gear S smart watch is stand-alone and looks far better than the Apple watch", "GGS"),
    ("Galaxy Note 7 배터리 폭발로 리콜", "GN7"),
    ("Galaxy A32 카메라가 고장났어요", "GA32"),
    ("compare the Galaxy Note 9 with the Redmi Note 7", "GN9"),
])
def test_legacy_gate_keeps_samsung(text, code):
    """자사 근거가 있으면(비교글 포함) 유지해야 한다."""
    from base.product_match import accept_legacy_code
    assert accept_legacy_code(text, code) is True


@pytest.mark.parametrize("text,hit", [
    ("Gear S smart watch", True), ("Gear S3 Frontier", True), ("기어 S2", True),
    ("shifting gears slowly", False), ("gear system failure", False),
    ("the gearbox broke", False),
])
def test_legacy_gear_token(text, hit):
    """'Gear' 자체 삼성 근거 판정 — 복수형 gears·gear system 을 잡으면 안 된다."""
    from base.product_match import _LEGACY_GEAR
    assert bool(_LEGACY_GEAR.search(text)) is hit


# ── Galaxy Ring — 무경계 ring 패턴이 타사 스마트링을 삼키던 문제 ──────
@pytest.mark.parametrize("text", [
    "Luna Ring 2.0 to inteligentny pierścień, który potrafi",
    "Circular announced its new Ring 3 Series and Ring 2 with payments",
    "Телефоны VERTU вернулись в новом формате Сеть restore открыла корнер",
])
def test_ring_requires_samsung_anchor(text):
    assert "GR2" not in codes(text)


@pytest.mark.parametrize("text", [
    "Galaxy Ring 2 배터리가 하루도 안 간다",
    "Samsung's Galaxy Ring 2 launches with new sensors",
    "갤럭시 링2 사이즈 고민중",
    "The Ring 2 from Samsung finally supports gestures",
    "링2 착용감 어떤가요 갤럭시 워치랑 같이 쓰려는데",
])
def test_ring_samsung_context_kept(text):
    assert "GR2" in codes(text)


# ── 제품군 확장 (XR·글래스·링·오디오·노트북) ─────────────────────────
@pytest.mark.parametrize("text,want", [
    ("Meta Quest 3 controller drift after firmware", "MQ3"),
    ("Ray-Ban Meta glasses battery dies in 2 hours", "MRB"),
    ("Apple Vision Pro neck strain after 30 min", "APVP"),
    ("Galaxy XR 무게가 너무 무겁다", "GXR"),
    ("Samsung Galaxy Glasses 공개", "GGL"),
    ("Oura Ring 4 sizing issue and rash", "OURA4"),
    ("Galaxy Book4 Pro 힌지 유격", "GBK4P"),
    ("Soundcore Space One ANC hiss", "ANKSPACE"),
    ("Fairphone 6 camera module replacement", "FP6"),
    ("Sennheiser Momentum 4 pairing drops", "SENM4"),   # 더 구체적인 세대가 이긴다
])
def test_category_expansion_tagged(text, want):
    got = infer_all_product_codes(text)
    assert got and got[0][0] == want, got


@pytest.mark.parametrize("text,gone", [
    # 반증이 코퍼스에서 직접 찾은 오탐 — 전부 막혀야 한다
    ("SwitchBot Keypad Vision Pro door lock review", "APVP"),
    ("whoop-de-doo, that has been going on for decades", "WHP"),
    ("Whoop de doo. My apologies for being sloppy", "WHP"),
    ("The Henoko-Oura Bay area of Okinawa base plan", "OURA"),
    ("metadata and meta tags for SEO optimization", "MGL"),
    ("퀄컴이 누비아 인수후부터 칩을 잘 뽑는다", "NB"),
    ("Here's Samsung and Google's Rival to Ray-Ban Meta Smart Glasses", "MGL"),
])
def test_category_expansion_false_positives_blocked(text, gone):
    assert gone not in codes(text)


def test_gear_vr_and_galaxy_xr_are_samsung():
    """자사 XR 제품이 경쟁사로 새지 않아야 한다."""
    from base.product_match import _brand_of
    for c in ("GXR", "GVR", "GGL"):
        assert _brand_of(c) == "samsung"


def test_new_brand_prefixes_registered():
    """_CODE_BRAND_PREFIX 누락 시 가드가 자기 브랜드 매칭을 삼킨다."""
    from base.product_match import _brand_of
    for code, brand in (("MQ3", "meta"), ("MRB", "meta"), ("APVP", "apple"),
                        ("OURA4", "oura"), ("WHP", "whoop"), ("ANKSPACE", "anker"),
                        ("SENMOM", "sennheiser"), ("JBLTUNE", "jbl"),
                        ("MSSFP", "microsoft"), ("NB", "nubia"), ("ZTE", "zte"),
                        ("TCL", "tcl"), ("FP6", "fairphone"), ("XRL", "xreal")):
        assert _brand_of(code) == brand, (code, _brand_of(code))


# ── 차세대·구형 미등록 모델 (0041) ──────────────────────────────────
@pytest.mark.parametrize("text,want", [
    ("Galaxy S27 Ultra 유출 스펙", "GS27U"),
    ("갤럭시 S27 기대된다", "GS27"),
    ("The weight of the iPhone Fold ruined it, 254 grams", "APFOLD"),
    ("폴더블 아이폰 두께가 아쉽다", "APFOLD"),
    ("iPhone 18 Pro Max battery life", "AP18PM"),
    ("아이폰 18 프로 가격", "AP18P"),
    ("iPhone Air 2 rumor roundup", "APAIR2"),
    ("Galaxy Nexus 2011 재평가", "GNEXUS"),
])
def test_next_gen_models_tagged(text, want):
    got = infer_all_product_codes(text)
    assert got and got[0][0] == want, got


@pytest.mark.parametrize("text,want", [
    # iPhone Fold 추가가 갤럭시 폴더블을 삼키면 안 된다
    ("Galaxy Z Fold 8 hinge dust", "GZF8"),
    ("갤럭시 Z 폴드8 힌지 유격", "GZF8"),
    ("Galaxy Z Flip 7 크리스", "GZFL7"),
    ("Galaxy S26 Ultra 카메라", "GS26U"),
    ("iPhone 17 Pro overheating", "AP17P"),
])
def test_next_gen_does_not_steal(text, want):
    assert infer_all_product_codes(text)[0][0] == want
