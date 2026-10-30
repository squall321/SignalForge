# 제품 나누기 다시 태깅(retag_split)의 고침 규칙 — 본문이 새 코드를 짚는 글만, 다른 대표 제품은 지킨다
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.retag_split import plan  # noqa: E402


def test_only_rows_whose_text_names_the_new_product_change():
    assert plan("GZF8", "GZF8:primary", [("GZF8", "primary")], "GZF8", "GZF8U") is None


def test_row_owned_by_the_old_code_moves_between_the_two_codes_only():
    out = [("GZF8U", "primary")]
    assert plan("GZF8", "GZF8:primary", out, "GZF8", "GZF8U") == ("GZF8U", [("GZF8U", "primary")])
    both = [("GZF8", "primary"), ("GZF8U", "compared")]           # 둘 다 짚은 비교글 — 대표는 두고 새 코드를 잇는다
    assert plan("GZF8", "GZF8:primary", both, "GZF8", "GZF8U") == ("GZF8", [("GZF8", "primary"), ("GZF8U", "compared")])
    flip = [("GZF8U", "primary"), ("GZF8", "compared")]           # 매처가 새 코드를 대표로 — 옛 대표는 비교로 내린다
    assert plan("GZF8", "GZF8:primary", flip, "GZF8", "GZF8U") == ("GZF8U", [("GZF8", "compared"), ("GZF8U", "primary")])
    assert plan(None, "", out, "GZF8", "GZF8U") == ("GZF8U", [("GZF8U", "primary")]), "대표가 비어 있던 글"


def test_matcher_primary_on_an_unrelated_product_is_not_followed():
    """첫 시운전 — 매처가 제목 관련성으로 다른 제품(GS24)을 대표로 골라 GZF8→GS24 가 섞였다. 나누기만 한다."""
    out = [("GS24", "primary"), ("GZF8U", "mentioned")]
    assert plan("GZF8", "GZF8:primary", out, "GZF8", "GZF8U") == ("GZF8U", [("GZF8U", "primary")])
    keep = plan("GZF8", "GS24:mentioned;GZF8:primary", out, "GZF8", "GZF8U")
    assert keep == ("GZF8U", [("GS24", "mentioned"), ("GZF8U", "primary")]), "다른 제품 링크는 그대로"


def test_other_primary_product_is_kept_and_only_links_change():
    """대표가 다른 제품(GS26U)인 글 — 대표는 그대로, 본문이 더는 안 짚는 옛 코드 링크는 떼고 새 코드를 잇는다."""
    out = [("GS26U", "primary"), ("GZF8U", "compared")]
    got = plan("GS26U", "GS26U:primary;GZF8:compared", out, "GZF8", "GZF8U")
    assert got == ("GS26U", [("GS26U", "primary"), ("GZF8U", "compared")])
    out2 = [("GZF8U", "primary"), ("GS26U", "mentioned")]          # 매처는 새 코드를 대표로 봐도 대표 자리는 지킨다
    got2 = plan("GS26U", "GS26U:primary;GZF8:mentioned", out2, "GZF8", "GZF8U")
    assert got2 == ("GS26U", [("GS26U", "primary"), ("GZF8U", "mentioned")])


def test_already_split_rows_are_left_alone():
    out = [("GZF8U", "primary"), ("GZF8", "compared")]
    assert plan("GZF8U", "GZF8:compared;GZF8U:primary", out, "GZF8", "GZF8U") is None, "다시 돌려도 바뀌지 않는다"
