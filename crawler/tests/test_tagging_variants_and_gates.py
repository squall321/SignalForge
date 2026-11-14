# 2026-11-07 태깅 보강을 실제 글의 꼴로 고정한다 — 변형 삼킴(Classic·Lite·FE·Edge)·줄임말·붙여쓰기·맨 번호 A·M 가드·구형 표기 목록
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base import model_codes as M  # noqa: E402
from base import product_match as PM  # noqa: E402
from base.product_match import infer_all_product_codes  # noqa: E402


def _codes(text):
    return [c for c, _ in infer_all_product_codes(text)]


@pytest.mark.parametrize("text, code", [
    # 변형 삼킴 — 라이브의 기본형이 앞부분을 먼저 잡아 변형이 사라졌다
    ("Joined the club. GW6 Classic 47mm BT. Purchased for $170", "GW6C"),
    ("Went to the store to try the Watch 6 Classic", "GW6C"),
    ("Galaxy Watch8 Classic strap", "GW8C"),
    ("Hey, I'm planning to buy the tab S10 lite 8gb variant", "GTS10L"),
    ("Samsung Galaxy S25 Edge battery", "GS25E"),
    ("One UI 9.0 update reaches Galaxy Z Flip 7 FE", "GZFL7FE"),
    ("I just got the Samsung buds 3 fe about two months ago", "GB3FE"),
    ("Grab the Samsung Galaxy Tab A11+ Right Now", "GTABA11P"),
    ("Galaxy XCover 7 Pro rugged phone", "GXC7P"),
    ("If I charge my galaxy watch 7 ultra with a 67 watt charger", "GWU"),
    # 줄임말 · 'tab' 을 뺀 탭 · 붙여 쓴 꼴
    ("Got a GW4 Classic recently. Any tips and tricks?", "GW4C"),
    ("Keeps disconnecting while in the range I just bought my gw7", "GW7"),
    ("Should I sideload reddit on my GW5?", "GW5"),
    ("Randomly stopped getting notifications on gw8c", "GW8C"),
    ("I just bought a Samsung S9 FE tablet", "GTABS9F"),
    ("I have a Samsung S9 FE + tablet and the cases are too small", "GTS9FP"),
    ("Is the S10 FE capable of video out?", "GTABS10F"),
    ("Is the S6 lite good for japanese writing practice?", "GTS6L"),
    ("Samsung S7 FE Tab Stylus Nib replacement?", "GTS7F"),
    ("[S20FE] On some apps, notifications repeat", "GFE20"),
    ("so ive had my s21fe for a year now", "GFE21"),
    ("¿Si tuvieras un #GalaxyZFold8 por 24 horas?", "GZF8"),
    ("Sharing my own #GalaxyZFold8Ultra review", "GZF8U"),
    # 카탈로그 0047 신설
    ("Galaxy Tab S12 Ultra launch", "GTABS12U"), ("Tab S12+ keyboard cover", "GTS12P"),
    ("Galaxy S26 FE price in India", "GFE26"), ("Galaxy A18 review", "GA18"), ("Galaxy A08 battery", "GA08"),
    ("Galaxy A06 launch", "GA06"), ("galaxy a05s battery", "GA05S"), ("Samsung Galaxy M35 5G camera", "GM35"),
    ("M47 5G heating", "GM47"), ("Galaxy F70 Pro price", "GF70P"), ("Galaxy F70e launched", "GF70E"),
    ("Galaxy F70 5G", "GF70"), ("Galaxy Fit 4 leak", "GFIT4"),
    # 변형 코드가 못 받던 꼴 — 예전엔 기본형이 삼켜 틀린 태그였다(dry-run 에서 기본형을 떼고 아무것도 못 붙인 114행)
    ("갤럭시s25+ 얇은 케이스 추천좀여", "GS25P"), ("[쿠팡] 갤럭시s26+ 512 탔습니다", "GS26P"),
    ("dont the s26 ultras get the green line", "GS26U"), ("사용하는 기기는 버즈3 pro임", "GB3P"),
    ("삼성탭s10 plus 사용중인데", "GTABS10P"), ("노트9, 탭s10fe+는 고속충전", "GTS10FP"),
    ("notification on both my watch 6 classics last night", "GW6C"),
    # 기본형을 막는 꼴은 변형이 받는 꼴과 같아야 한다 — 낱말이 이어지면 기본형이다
    ("the S25 ultrasonic fingerprint is fast", "GS25"), ("the S25 edges are sharp", "GS25"),
])
def test_named_product_is_the_primary(text, code):
    got = _codes(text)
    assert got and got[0] == code, (text, got)


@pytest.mark.parametrize("text, codes", [
    ("Watch 8 vs Watch 8 Classic which one", {"GW8", "GW8C"}),
    ("S25 vs S25 Edge camera", {"GS25", "GS25E"}),
    ("カメラアイランドがGalaxy Z Fold 7やS25 Edgeっぽくなった", {"GZF7", "GS25E"}),   # 일본어 붙여 씀
    ("s26 fe vs s25 fe", {"GFE26", "GFE25"}),
    ("S9 FE+ vs S10 FE for drawing", {"GTS9FP", "GTABS10F"}),
])
def test_comparisons_keep_both(text, codes):
    assert codes <= set(_codes(text)), (text, _codes(text))


@pytest.mark.parametrize("text, codes", [
    # 기본형의 첫 매칭이 변형 안에 떨어져 따로 쓴 기본형까지 사라지던 꼴(dry-run 896행의 실제 글)
    ("Fold 7 и Flip 7 претерпели редизайн. Информации о преемнике Galaxy Z Flip 7 FE пока нет", {"GZFL7FE", "GZFL7"}),
    ("Galaxy S26 FE design looks like the regular Galaxy S26", {"GFE26", "GS26"}),
    ("the more premium variant of the Galaxy Tab A11+, alongside the standard Galaxy Tab A11", {"GTABA11P", "GTABA11"}),
    ("Galaxy Buds 3 FE。 昨年発売されたGalaxy Buds 3とGalaxy Buds 3 Proの廉価版", {"GB3FE", "GB3"}),
    ("s25엣지 1주일 써봄 s23 s24 s25썻었고", {"GS25E", "GS25"}),
    ("Galaxy Watch 8 Classic is heavier than the Galaxy Watch 8", {"GW8C", "GW8"}),
    ("gw6 classic or gw6?", {"GW6C", "GW6"}),
    ("Watch 7 Ultra vs Watch 7", {"GWU", "GW7"}), ("XCover 7 Pro vs XCover 7", {"GXC7P", "GXC7"}),
    ("Galaxy F70 Pro and Galaxy F70 5G", {"GF70P", "GF70"}), ("Upgraded from GW5 Pro to GW5", {"GW5P", "GW5"}),
    ("S9 FE+ vs S9 FE", {"GTS9FP", "GTABS9F"}), ("S10 FE+ or S10 FE", {"GTS10FP", "GTABS10F"}),
    # FE 만 막으면 첫 매칭이 다음 변형 안으로 옮겨 갔다 — 변형을 모두 막는다
    ("Galaxy S26 FE vs Galaxy S26 Ultra, Galaxy S26 Plus, maupun Galaxy S26 reguler", {"GFE26", "GS26U", "GS26P", "GS26"}),
    ("Galaxy S25 Edge or S25 Ultra 256gb for 770, S25 128gb for 550", {"GS25E", "GS25U", "GS25"}),
    ("Galaxy Buds3 Pro 와 Galaxy Buds3 FE, 그리고 Galaxy Buds3", {"GB3P", "GB3FE", "GB3"}),
    ("Galaxy Tab S10 Lite, Galaxy Tab S10 Ultra 5G, Galaxy Tab S10 5G", {"GTS10L", "GTABS10U", "GTABS10"}),
])
def test_base_named_beside_its_variant_is_kept(text, codes):
    assert codes <= set(_codes(text)), (text, _codes(text))


def test_variant_alone_does_not_bring_its_base():
    for text, base in (("Galaxy S26 FE price", "GS26"), ("Galaxy Watch 8 Classic strap", "GW8"),
                       ("Galaxy Z Flip 7 FE review", "GZFL7"), ("buds 3 fe eartips", "GB3"),
                       ("Galaxy S26 Ultra review", "GS26"), ("Galaxy Buds3 Pro case", "GB3"), ("Tab S10 Ultra keyboard", "GTABS10")):
        assert base not in _codes(text), (text, _codes(text))
    assert _codes("S26 features are great") == ["GS26"]          # 'fe' 로 시작하는 낱말은 막지 않는다
    # S 시리즈 줄임말 · 풀어 쓴 FE — 변형이 받으니 기본형은 비켜 간다
    for text, base, variant in (("S26울트라 화이트 배송\nS26울 512 구매 예정인데", "GS26", "GS26U"),
                                ("Ponsel Samsung Galaxy S25 Fan Edition (FE) bukan cuma", "GS25", "GFE25")):
        got = _codes(text)
        assert base not in got and got[0] == variant, (text, got)
    # 주소 꼴의 변형 — 기본형이 끼어들지 않는다
    for text, base in (("https://tecnoblog.net/achados/galaxy-s25-fe-256-gb", "GS25"),
                       ("moisture-forming-inside-galaxy-s26-ultra-camera-lenses", "GS26")):
        assert base not in _codes(text), (text, _codes(text))


@pytest.mark.parametrize("text, banned", [
    # 맨 번호 A·M — 애플 칩 · 아이패드 · 안드로이드 버전 · 노트북 · ARM 코어(활성 링크 실측의 꼴)
    ("Samsung Galaxy Tab S9 FE+ and iPad (A16/11th Gen)", "GA16"),
    ("the Ipad uses the A16 Bionic Chip", "GA16"),
    ("How can I exit A16 beta? I am on Android 16 beta", "GA16"),
    ("Battery health 80% limiter doesnt work on A16 QPR1", "GA16"),
    # 꼬리 붙은 버전이 한 번이라도 나오면 그 글의 다른 맨 A14~A17 도 버전이다(레거시가 A15 를 집던 dry-run 3행)
    ("So I updated to A16 beta 4 from A15 and the A16 build is buggy", "GA15"),
    ("So I updated to A16 beta 4 from A15 and the A16 build is buggy", "GA16"),
    ("an Asus Zenbook A16 with OLED screen and 48 GB of RAM", "GA16"),
    ("Chipset: A17 Pro chip (very fast, great for gaming", "GA17"),
    ("iPad mini A17 Pro or nah?", "GA17"),
    ("Examples include cortex-a57 vs a53", "GA57"),
    ("It has a dual-core Arm Cortex-M55 CPU", "GM55"),
    ("Even that's close to the A18 and M4 — the A18 Pro chip", "GA18"),
    # 맨 'S10 Lite' 는 탭 문맥이 없으면 탭이 아니다(2020 폰 S10 Lite 일 수 있다)
    ("My poor S10 Lite is dying. The battery port is loose", "GTS10L"),
    # 기본형 없는 Tab S12 · 영어 낱말 'fit 4' · 타사 'Fold8 Ultra'
    ("galaxy tab s12 rumors", "GTABS12U"), ("it will fit 4 people", "GFIT4"),
    ("the vivo X Fold8 Ultra launched", "GZF8U"),
])
def test_lookalikes_are_not_tagged(text, banned):
    assert banned not in _codes(text), (text, _codes(text))


def test_brand_adjacent_bare_numbers_still_tag():
    """가드는 생김새가 분명한 것만 막는다 — 브랜드가 붙거나 문맥이 평범한 맨 번호는 그대로다."""
    for text, code in (("Samsung A16 battery drain", "GA16"), ("my A16 battery drains fast", "GA16"),
                       ("Upgrading from A54 to A57 Well", "GA57"), ("After updating to Android 16, my Samsung A16 phone", "GA16"),
                       ("My Galaxy A15 got the A16 beta", "GA15")):
        assert code in _codes(text), (text, _codes(text))


def test_older_codes_stay_out_of_live_authority():
    """구형 표기 목록의 코드는 _LIVE_CODES 가 아니다 — 레거시 사전이 풀네임을 계속 낸다('라이브 권위' 함정)."""
    older = {c for c, _ in PM.OLDER_PATTERNS}
    assert not older & PM._LIVE_CODES
    assert PM.legacy_fallback_code("Galaxy Watch 4 Classic strap broke") == "GW4C"
    assert _codes("galaxy watch 5 pro gps drift") == ["GW5P"]


def test_tab_s10_lite_does_not_fall_back_to_tab_s10():
    """예전엔 'tab s10' 이 'tab s10 lite' 의 앞부분을 잡아 GTABS10 이 됐다(활성 383행)."""
    got = _codes("Galaxy Tab S10 Lite keyboard case")
    assert got[0] == "GTS10L" and "GTABS10" not in got


def test_new_model_codes_and_generations():
    assert M.product_of("S937") == "GS25E" and M.product_of("S741") == "GFE26" and M.product_of("F761") == "GZFL7FE"
    for p in ("/shared", str(ROOT.parent / "shared")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import ontology as O
    assert O.family_key("GZFL7FE") == "GZFL7" and O.family_key("GTS10L") == "GTABS10"
    assert O.family_key("GTABS12U") == O.family_key("GTS12P") == "GTABS12" and O.family_key("GFE26") == "GS26"
    assert re.match(O.SERIES["zflip"]["code_regex"], "GZFL7FE")


@pytest.mark.parametrize("text, want, banned", [
    # Watch Ultra2(2026-11-14) — 카탈로그에 없던 때는 'watch ultra 2' 가 1세대 GWU 로 갔다
    ("My Galaxy Watch Ultra 2 battery drains fast", {"GWU2"}, {"GWU"}),
    ("갤럭시 워치 울트라2 배터리 후기", {"GWU2"}, {"GWU"}),
    ("워치 울트라 2세대 샀어요", {"GWU2"}, {"GWU"}),
    ("Galaxy Watch Ultra 2nd gen review", {"GWU2"}, {"GWU"}),
    ("Galaxy Watch Ultra 2 is better than my old Galaxy Watch Ultra", {"GWU2", "GWU"}, set()),
    ("Galaxy Watch Ultra (2025) orange strap", {"GWU"}, {"GWU2"}),
    ("Apple Watch Ultra 2 battery is great", set(), {"GWU", "GWU2"}),
    # 단독 'Galaxy Ring' 은 팔리는 1세대 — 예전엔 출시 전 GR2 로 갔다
    ("My Galaxy Ring battery swelled", {"GR1"}, {"GR2"}),
    ("갤럭시 링 사이즈 키트 왔다", {"GR1"}, {"GR2"}),
    ("Galaxy Ring 2 rumors early 2027", {"GR2"}, {"GR1"}),
    ("갤럭시링2 언제 나오나", {"GR2"}, {"GR1"}),
    ("Waiting for Galaxy Ring 2 to replace my Galaxy Ring", {"GR2", "GR1"}, set()),
    ("Luna Ring 2.0 review", set(), {"GR1", "GR2"}),
    # 붙여 쓴 한국어 경쟁사 — '애플워치 울트라' 의 '워치 울트라' 가 GWU 로 갔다. 막지 않으면 GWU 를 '2' 앞에서 막은 뒤
    # GWU 의 첫 매칭이 애플 쪽으로 옮겨 애플 비교 언급까지 사라졌다(retag dry-run 표본, 2026-11-14)
    ("갤워치 울트라2 가격? 애플워치 울트라처럼 100정도 잡으면 될까요", {"GWU2", "AWU1"}, {"GWU"}),
    ("애플 워치 울트라 2 vs 갤럭시 워치 울트라2", {"GWU2", "AWU2"}, {"GWU"}),
])
def test_watch_ultra2_and_galaxy_ring_generations(text, want, banned):
    got = set(_codes(text))
    assert want <= got and not (banned & got), got


def test_watch_ultra2_model_code():
    assert M.product_of("L715") == "GWU2" and M.product_of("L705") == "GWU"


def test_every_matcher_code_is_in_the_catalog_seed():
    """매처가 내는 코드가 카탈로그에 없으면 그 태그는 조용히 버려진다 — 시드 파일과 맞춘다."""
    seed = ROOT.parent / "backend" / "app" / "seeds" / "catalog_products.json"
    if not seed.is_file():
        pytest.skip("backend 시드 파일이 없는 배치")
    codes = {p["code"] for p in json.loads(seed.read_text(encoding="utf-8"))}
    missing = sorted({c for c, _ in PM.PRODUCT_PATTERNS + PM.OLDER_PATTERNS} - codes)
    assert not missing, missing
