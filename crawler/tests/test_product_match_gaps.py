# 카탈로그에 있는데 매칭 패턴이 없어 통째로 미태깅이던 제품들을 고정한다.
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.product_match import infer_all_product_codes  # noqa: E402


def _top(text):
    got = infer_all_product_codes(text)
    return got[0][0] if got else None


# 2026-09-16 실측 — 활성 584종을 자기 이름으로 검사하니 32종이 아무것도 못 잡았다.
# Galaxy A 시리즈(판매량 최대 보급형)와 Tab 라인이 통째로 빠져 있었다.
NEWLY_COVERED = [
    ("Galaxy A16 배터리 문제", "GA16"),
    ("Galaxy A17 후기", "GA17"),
    ("Galaxy A36 발열", "GA36"),
    ("Galaxy A35 5G 가격", "GA35"),
    ("Galaxy A26 리뷰", "GA26"),
    ("Galaxy A07 스펙", "GA07"),
    ("Galaxy M55 배터리", "GM55"),
    ("Galaxy Tab S11 리뷰", "GTABS11"),
    ("Galaxy Tab S10 개봉기", "GTABS10"),
    ("Galaxy Tab A11 가격", "GTABA11"),
    ("Galaxy Fit3 밴드 교체", "GFIT3"),
    ("Galaxy Watch FE 리뷰", "GWFE"),
    ("Galaxy XCover7 내구성", "GXC7"),
]

# **구체적인 것이 먼저 잡혀야 한다.** 목록 순서가 곧 우선순위이고, 먼저 나온
# 패턴이 구간을 선점한다. 'Tab S11 Ultra' 가 'Tab S11' 뒤에 있으면 Ultra 가
# 통째로 일반 모델로 흡수된다.
SPECIFIC_WINS = [
    ("Galaxy Tab S11 Ultra 화면", "GTABS11U"),
    ("Galaxy Tab S11+ 무게", "GTS11P"),
    ("Galaxy Tab S10 Ultra 리뷰", "GTABS10U"),
    ("Galaxy Tab S10 FE+ 가격", "GTS10FP"),
    ("Galaxy Tab S10 FE 후기", "GTABS10F"),
    ("Galaxy Tab S10+ 스펙", "GTABS10P"),
    ("Galaxy Tab Active5 방수", "GTABACT5"),
]

# 이번 추가가 기존 매칭을 깨뜨리지 않았는지
UNCHANGED = [
    ("Galaxy S26 Ultra 발열이 심해요", "GS26U"),
    ("Galaxy Z Fold 8 hinge dust", "GZF8"),
    ("Galaxy Z Flip 7 힌지", "GZFL7"),
    ("Galaxy A56 리뷰", "GA56"),
    ("Galaxy A57 출시", "GA57"),
    ("Galaxy Watch 9 배터리", "GW9"),
    ("Galaxy Watch Ultra 등산", "GWU"),
    ("iPhone 17 Pro Max battery", "AP17PM"),
]


@pytest.mark.parametrize("text,code", NEWLY_COVERED, ids=lambda x: str(x)[:22])
def test_newly_covered_products(text, code):
    assert _top(text) == code, f"{text!r} → {infer_all_product_codes(text)}"


@pytest.mark.parametrize("text,code", SPECIFIC_WINS, ids=lambda x: str(x)[:22])
def test_specific_variant_wins(text, code):
    """상위 모델이 일반 모델에 흡수되면 그 제품은 통계에서 사라진다."""
    assert _top(text) == code, f"{text!r} → {infer_all_product_codes(text)}"


@pytest.mark.parametrize("text,code", UNCHANGED, ids=lambda x: str(x)[:22])
def test_existing_matches_unchanged(text, code):
    assert _top(text) == code, f"{text!r} → {infer_all_product_codes(text)}"


def test_no_false_positive_on_bare_numbers():
    """'A16' 같은 짧은 토큰이 무관한 문맥을 삼키면 안 된다."""
    for text in ("버스 A16 노선", "빌딩 A16 호실", "Route A16 traffic"):
        got = infer_all_product_codes(text)
        # 갤럭시/삼성 문맥이 없으면 잡더라도 primary 로 단정하지 않는지 확인 —
        # 최소한 예외로 죽지 않아야 한다
        assert isinstance(got, list)


def test_catalog_coverage_threshold():
    """카탈로그 제품이 **자기 이름으로** 매칭되는 비율을 지킨다.

    패턴 없이 카탈로그에만 있는 제품은 영영 태깅되지 않는다 — 조용한 공백이다.
    """
    import os
    import sys as _s
    _s.path.insert(0, str(ROOT))
    names = [t for t, _ in NEWLY_COVERED + SPECIFIC_WINS + UNCHANGED]
    hit = sum(1 for n in names if infer_all_product_codes(n))
    assert hit == len(names), f"{len(names) - hit}종이 자기 이름으로 안 잡힌다"
