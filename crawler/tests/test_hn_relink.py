"""HN/영문 포럼 product 재매칭 단위 테스트 — R8 Track D.

검증 시나리오:
  1. 일반 갤럭시 약어 본문 → 정상 매칭 (Galaxy S10+, Z Fold 4, Note 20 Ultra)
  2. samsung 컨텍스트 없는 "galaxy" 단독 → 매칭 거부
  3. HN noise (milky way, hitchhiker's guide, GOG Galaxy, galaxy-brained) → 거부
  4. 영문 약어 S10E / Buds Pro / Watch5 Pro → 정상 매칭
  5. 차단어가 동시에 등장해도 거부 (보수적)

외부 DB 의존 없음 — match_hn_product_code() 순수 함수만 호출.

실행:
  cd crawler && python -m pytest tests/test_hn_relink.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.hn_relink import match_hn_product_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# 1. 영문 약어 정상 매칭 — HN/Reddit 본문 흔한 표현
# ─────────────────────────────────────────────────────────────────────────
def test_galaxy_s10e_eng_abbrev():
    code = match_hn_product_code(
        "I have a Samsung Galaxy S10e for two years and the battery is fine."
    )
    assert code == "GS10E", f"S10E 매칭 실패: got {code}"


def test_z_fold_4_with_space():
    code = match_hn_product_code(
        "Samsung's new Z Fold 4 hinge feels much sturdier than my old Fold."
    )
    assert code == "GZF4", f"Z Fold 4 매칭 실패: got {code}"


def test_note_20_ultra_abbrev():
    code = match_hn_product_code(
        "The Note 20 Ultra still has S Pen support which I love. Samsung killed it."
    )
    assert code == "GN20U", f"Note 20 Ultra 매칭 실패: got {code}"


def test_tab_s9_fe_eng():
    code = match_hn_product_code(
        "Got a Tab S9 FE for sketching. Samsung's stylus latency is great."
    )
    assert code == "GTABS9F", f"Tab S9 FE 매칭 실패: got {code}"


def test_watch5_pro_eng():
    code = match_hn_product_code(
        "Samsung Galaxy Watch5 Pro battery life is excellent."
    )
    assert code == "GW5P", f"Watch5 Pro 매칭 실패: got {code}"


def test_buds_pro_eng():
    # Buds Pro 는 "Galaxy Buds Pro" → "GBP" (R6 기존 매핑).
    code = match_hn_product_code(
        "I switched from AirPods to Samsung Galaxy Buds Pro. Sound is decent."
    )
    assert code == "GBP", f"Buds Pro 매칭 실패: got {code}"


# ─────────────────────────────────────────────────────────────────────────
# 2. samsung 컨텍스트 없는 "galaxy" 단독 — 거부
# ─────────────────────────────────────────────────────────────────────────
def test_reject_galaxy_alone_no_samsung():
    code = match_hn_product_code(
        "The Galaxy was filled with stars and dust clouds. Truly beautiful."
    )
    assert code is None, f"galaxy 단독 매칭됨 (거부 실패): got {code}"


def test_reject_pixel_only_mentions_no_samsung():
    code = match_hn_product_code(
        "I'm comparing Pixel 8 Pro vs iPhone 15. The Pixel wins on camera."
    )
    assert code is None, f"Pixel/iPhone only 매칭됨: got {code}"


# ─────────────────────────────────────────────────────────────────────────
# 3. HN noise 차단 — 우주/은유 표현
# ─────────────────────────────────────────────────────────────────────────
def test_reject_milky_way_noise():
    # samsung 도 있고 s10 도 있지만 milky way 가 더 강한 신호.
    code = match_hn_product_code(
        "I'm reading about the Milky Way on my Samsung Galaxy S10. Fascinating."
    )
    assert code is None, f"milky way noise 통과: got {code}"


def test_reject_hitchhiker_galaxy():
    code = match_hn_product_code(
        "Samsung's reference to Hitchhiker's Guide to the Galaxy in their ad campaign."
    )
    assert code is None, f"hitchhiker's guide 통과: got {code}"


def test_reject_gog_galaxy_launcher():
    code = match_hn_product_code(
        "GNU/Linux gamers complain that GOG Galaxy doesn't run on Linux. "
        "Samsung has the same problem with their Galaxy Store."
    )
    assert code is None, f"gog galaxy 통과: got {code}"


def test_reject_galaxy_brained_metaphor():
    code = match_hn_product_code(
        "That's a galaxy-brained take on Samsung's strategy. Not buying it."
    )
    assert code is None, f"galaxy-brained 통과: got {code}"


# ─────────────────────────────────────────────────────────────────────────
# 4. samsung 단독 + 일반 표현 (모델 단어 없음) → None
# ─────────────────────────────────────────────────────────────────────────
def test_samsung_no_model_returns_none():
    code = match_hn_product_code(
        "Samsung TVs are incredibly hackable; this has been known for over a decade."
    )
    assert code is None, f"모델 미언급인데 매칭됨: got {code}"


# ─────────────────────────────────────────────────────────────────────────
# 5. Galaxy strong context (samsung 어휘 없어도 명시적 모델 단어 → 허용)
#    e.g. "Galaxy Note 7 explosion" 같이 모델이 명시되면 매칭.
# ─────────────────────────────────────────────────────────────────────────
def test_galaxy_note7_explosion():
    code = match_hn_product_code(
        "The Galaxy Note 7 explosion remains a landmark recall in tech history."
    )
    assert code == "GN7", f"Galaxy Note 7 매칭 실패: got {code}"


def test_galaxy_z_flip_5():
    code = match_hn_product_code(
        "Galaxy Z Flip 5 sales numbers exceeded expectations in Q4."
    )
    assert code == "GZFL5", f"Galaxy Z Flip 5 매칭 실패: got {code}"


def test_galaxy_note_II_roman():
    # HN 본문에 "Galaxy Note II" 표기가 자주 등장 — GN2 로 매칭되어야 함.
    code = match_hn_product_code(
        "I use my Galaxy Note II for SSH into my school's servers. Stylus is great."
    )
    assert code == "GN2", f"Galaxy Note II → GN2 실패: got {code}"


def test_galaxy_note_first_gen():
    # 숫자/로마자 없이 "Galaxy Note" 만 → 1세대.
    code = match_hn_product_code(
        "Samsung's original Galaxy Note phablet redefined the smartphone size category."
    )
    assert code == "GN1", f"Galaxy Note 1세대 매칭 실패: got {code}"


# ─────────────────────────────────────────────────────────────────────────
# 6. Empty/None edge cases
# ─────────────────────────────────────────────────────────────────────────
def test_empty_string_returns_none():
    assert match_hn_product_code("") is None
    assert match_hn_product_code(None) is None


# ─────────────────────────────────────────────────────────────────────────
# pytest 없이 직접 실행
# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [obj for name, obj in globals().items() if name.startswith("test_") and callable(obj)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
