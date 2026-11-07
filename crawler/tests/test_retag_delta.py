# 매처 고침 반영(retag_delta)의 고침 규칙 — 옛 매처와 새 매처가 갈리는 코드만, 다른 대표·링크는 지킨다
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base import product_match as PM  # noqa: E402
from scripts.retag_delta import legacy_kept, plan  # noqa: E402


def test_same_verdict_is_left_alone():
    assert plan("GW7", "GW7:primary", [("GW7", "primary")], [("GW7", "primary")]) is None


def test_untagged_row_gets_the_newly_found_product():
    assert plan(None, "", [], [("GW7", "primary")]) == ("GW7", [("GW7", "primary")], [], ["GW7"])


def test_swallowed_variant_moves_to_its_own_code():
    """'Watch 6 Classic' 이 GW6 로 갔던 글 — GW6 을 떼고 GW6C 를 대표로."""
    got = plan("GW6", "GW6:primary", [("GW6", "primary")], [("GW6C", "primary")])
    assert got == ("GW6C", [("GW6C", "primary")], ["GW6"], ["GW6C"])


def test_false_positive_is_removed_and_the_real_product_takes_over():
    """'iPad (A16)' — GA16 을 떼면 새 매처가 찾은 아이패드가 대표가 된다. 아무것도 없으면 대표가 빈다."""
    old = [("GA16", "primary"), ("IPAD11", "mentioned")]
    got = plan("GA16", "GA16:primary;IPAD11:mentioned", old, [("IPAD11", "primary")])
    assert got == ("IPAD11", [("IPAD11", "primary")], ["GA16"], [])
    assert plan("GA16", "GA16:primary", [("GA16", "primary")], []) == (None, [], ["GA16"], [])


def test_other_primary_is_kept_and_only_the_changed_links_move():
    got = plan("IPADMINI", "GA17:mentioned;IPADMINI:primary",
               [("IPADMINI", "primary"), ("GA17", "mentioned")], [("IPADMINI", "primary")])
    assert got == ("IPADMINI", [("IPADMINI", "primary")], ["GA17"], [])


def test_old_matchers_own_primary_follows_the_new_pick():
    """'Galaxy S26 FE … like the Galaxy S26' — 저장된 대표 GS26 은 옛 매처의 첫째였다. 새 매처가 GS26 을 보면서도
    GFE26 을 첫째로 두면 따른다(새 글은 수집 때 그렇게 태깅된다). GS26 은 언급으로 남는다."""
    got = plan("GS26", "GS26:primary", [("GS26", "primary")], [("GFE26", "primary"), ("GS26", "mentioned")])
    assert got == ("GFE26", [("GFE26", "primary"), ("GS26", "mentioned")], [], ["GFE26"])


def test_primary_from_another_source_is_kept_even_if_the_new_pick_differs():
    """저장된 대표가 옛 매처의 첫째가 아니면(명시 매핑 · 예전 판) 지킨다 — 이번 차이는 링크로만 들어간다."""
    got = plan("GS24", "GS24:primary;GW8:mentioned", [("GW8", "primary")], [("GW8C", "primary"), ("GW8", "mentioned")])
    assert got == ("GS24", [("GS24", "primary"), ("GW8", "mentioned"), ("GW8C", "mentioned")], [], ["GW8C"])


def test_links_unrelated_to_the_change_are_untouched():
    """저장된 링크가 옛 매처 판정과 다른 행(명시 매핑 · 예전 판) — 이번 차이 밖의 링크는 그대로다."""
    got = plan("GS24", "GS24:primary;GZF6:mentioned", [("GS24", "primary")],
               [("GS24", "primary"), ("GW7", "mentioned")])
    assert got == ("GS24", [("GS24", "primary"), ("GW7", "mentioned"), ("GZF6", "mentioned")], [], ["GW7"])


def test_older_model_elsewhere_in_the_text_is_kept():
    """레거시 폴백은 라이브가 빈손일 때만 돈다 — 새로 알아본 A05s 가 같은 글의 A54 태그를 지우면 안 된다.
    제목 줄에 새 제품만 있으면 대표는 새 제품으로, A54 는 언급으로 남는다."""
    text = "Can I free up RAM on my mom's Galaxy A05s?\nRecently in my A54, I disabled ram plus"
    old, new = [("GA54", "primary")], PM.infer_all_product_codes(text)
    assert new == [("GA05S", "primary")]
    kept, title_new = legacy_kept(PM, text, old, new)
    assert kept == {"GA54"} and title_new
    assert plan("GA54", "GA54:primary", old, new, kept, title_new) == (
        "GA05S", [("GA05S", "primary"), ("GA54", "mentioned")], [], ["GA05S"])


def test_kept_older_model_stays_primary_when_the_title_names_both():
    text = "Samsung s21 fe or the a54?\nI am able to get them for the same price"
    old, new = [("GA54", "primary")], PM.infer_all_product_codes(text)
    kept, title_new = legacy_kept(PM, text, old, new)
    assert kept == {"GA54"} and not title_new
    assert plan("GA54", "GA54:primary", old, new, kept, title_new)[0] == "GA54"


def test_older_code_whose_evidence_is_the_new_match_is_removed():
    """'Galaxy S20 FE' 를 GS20 으로 적던 것 — 새 매처의 GFE20 구간을 지우면 S20 근거가 없다."""
    text = "Galaxy S20 FE battery drain after update"
    new = PM.infer_all_product_codes(text)
    assert new[0][0] == "GFE20"
    assert legacy_kept(PM, text, [("GS20", "primary")], new) == (set(), False)


def test_phone_s_number_is_not_kept_in_a_tablet_thread():
    """'S10 Fe plus or S10 plus?' — 레거시의 GS10P(2019 폰)는 'tab' 을 뺀 탭 S10+ 였다. 새 매처가 탭을 찾으면 남기지 않는다."""
    text = "S10 Fe plus or S10 plus?\nHello I'm planning to get a tablet for notes"
    new = PM.infer_all_product_codes(text)
    assert new[0][0] == "GTS10FP"
    assert legacy_kept(PM, text, [("GS10P", "primary")], new) == (set(), False)
    assert plan("GS10P", "GS10P:primary", [("GS10P", "primary")], new)[:3] == (
        "GTS10FP", [("GTS10FP", "primary")], ["GS10P"])
