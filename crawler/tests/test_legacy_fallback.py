# 구형 모델 태깅이 수집 길목에 연결돼 있고, 레거시 사전의 오매칭 유형이 막혀 있는지 고정한다.
"""막는 고장(2026-10-17 실측).

라이브 사전(PRODUCT_PATTERNS)은 활성 제품만 안다. 구형 311종은 레거시 사전
(scripts/relink_products)에만 있었고, 그 폴백은 reprocess Phase C 한 곳에서만 불렸다.
08-28(dcdb0b2) 번역 백로그 수정이 그 잡을 번역 전용으로 바꾸자 구형 태깅이 조용히
멈췄다 — 수집 월별 구형 태깅 비율 6월 9.2% · 8월 8.2% → 9월 1.8% · 10월 1.3%.
AI 가 "제품 태깅이 거의 안 돼 있다"고 말하던 배경의 하나다.

레거시를 그대로 다시 켜면 안 됐다. 미태깅 표본에서 레거시가 구형 코드를 준 330건의
정밀도는 86~88% 였다. 틀린 유형마다 게이트를 두고 새 홀드아웃(2만 건 → 회수 766)의
160건을 수기 판정해 98.8% 를 확인했다. 아래 문장들은 그 판정에서 나온 실제 사례다.
"""
import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import base.product_match as PM  # noqa: E402
from base.product_match import (  # noqa: E402
    infer_all_product_codes, infer_product_code, legacy_fallback_code,
)
from scripts.relink_products import (  # noqa: E402
    iter_product_matches, match_product_code,
)


def _top(text):
    got = infer_all_product_codes(text)
    return got[0][0] if got else None


# ── 연결 — 이게 끊겨서 두 달 가까이 구형 태깅이 멈췄다 ────────────────────
def test_fallback_is_wired_into_the_choke_point():
    """수집(normalize)·링크 백필·재태깅이 모두 이 함수를 지난다. 배치 잡에 걸지 않는다."""
    text = "Samsung opens Galaxy Note 9 reservations, confirms August 24 release date"
    assert infer_all_product_codes(text) == [("GN9", "primary")]
    assert infer_product_code(text) == "GN9"


def test_legacy_false_is_live_only():
    """backfill_rival_retag 용 — 레거시 코드가 '다른 제품을 찾았다'로 쓰이면 명시 매핑이 덮인다."""
    assert infer_all_product_codes("My trusty Galaxy Watch 4 still going", legacy=False) == []


def test_fallback_runs_only_when_live_is_empty():
    """라이브가 하나라도 찾으면 레거시는 끼어들지 않는다 — 원 설계 그대로."""
    got = infer_all_product_codes("Galaxy S26 Ultra after years on my old Galaxy Note 9")
    assert [c for c, _ in got] == ["GS26U"], got


# ── 회수돼야 하는 구형 언급 (실제 사례) ─────────────────────────────────
RECOVERED = [
    ("Galaxy Tab S9 Ultra for most tasks, Tab A9+ for travel", "GTABS9U"),
    ("Afib detected on Galaxy Watch 4 (1hr after running)", "GW4"),
    ("helping my GF set up her new Samsung galaxy s21 fe", "GFE21"),
    ("Carrier-locked Galaxy Z Flip 3 starts getting May 2022 security update", "GZFL3"),
    ("Wifi on a25 is garbage", "GA25"),
    ("pov: when you watch this video on a samsung a 25 5g", "GA25"),     # 브랜드가 앞에 있으면 띄어써도 된다
    ("problem with adding a face for face unlock on pixel 7", "PX7"),
    ("any tips for a new owner? upgrading from a note 9", "GN9"),
    ("갤럭시 노트5 리뷰도 보고 싶습니다", "GN5"),
    ("플립3은 그거 안된대", "GZFL3"),                                   # 한글 조사는 경계로 본다
    ("Galaxy Watch 4 Classic Bugs after Update", "GW4C"),
    ("I've been spending weeks with the HTC U11 and it blows the Galaxy S8 out of the water", "GS8"),
]


@pytest.mark.parametrize("text,code", RECOVERED)
def test_old_models_are_recovered(text, code):
    assert _top(text) == code, f"{text!r} → {_top(text)} (기대 {code})"


def test_rejected_candidate_does_not_stop_the_search():
    """첫 후보 'galaxy fold'(1세대)가 거부돼도 다음 후보 'galaxy fold 3' 에 닿아야 한다."""
    assert _top("my phone is a galaxy fold 3, theirs is an iphone") == "GZF3"


# ── 막아야 하는 오매칭 (330건 판정에서 나온 유형별 실제 사례) ───────────
REJECTED = [
    # 시리즈 일반명 → 1세대 (오류의 절반)
    "can someone explain the galaxy fold to me? is the outer screen necessary",
    "the galaxy buds controls have now migrated to settings",
    "alleged z flip case leaked by ice universe",
    "1세대만에 갤럭시 폴드를 이겼다는 거네",
    # 이어지는 문자 — 더 긴 이름의 일부
    "no travel charger for the original galaxy watch 46mm",
    "switching from a note 23 ultra to the fold",
    "my phone has 32gb of ram and a 25,500mah battery",
    # 번호 붙은 후속·변형 접미
    "looking for a galaxy watch active 2 refurbished",
    "are galaxy buds fe 3 worth it?",
    "usb controlled by connected device on samsung galaxy s9 fe+",
    "keyboard case for the s10 plus tablet",
    "samsung galaxy a7 tab lite (sm-t225) oem unlock",
    # 맨 'a NN'
    "la prima equivale a 12 días de salario por cada año",
    "my age settings are for a 13 year old",
    "six cortex-a55 cores up to 1.8ghz",
    # 목록 번호
    "&restrict_sr=on&t=all&sort=new). note 2. join our irc and telegram chat-rooms!",
    # 'tab' 을 뺀 탭 S6~S10
    "Looking for a tablet mainly for media, stuck between the S9 and S10 plus",
    "I recently bought S10 plus. Unfortunately I have misplaced my S- Pen",
]


@pytest.mark.parametrize("text", REJECTED)
def test_known_mismatch_types_are_rejected(text):
    assert _top(text) is None, f"{text!r} → {_top(text)} (기대 None)"


def test_live_codes_never_come_from_fallback():
    """라이브가 '아니다'라고 한 것을 레거시가 뒤집으면 틀린다('ipad a16' → GA16)."""
    for text in ["Ipad a16 or air m3 for students?", "getting a 17 pro soon",
                 "Wifi on a25 is garbage", "Afib detected on Galaxy Watch 4"]:
        code = legacy_fallback_code(text)
        assert code is None or code not in PM._LIVE_CODES, (text, code)


# ── 레거시 사전 자체의 동작은 그대로 ─────────────────────────────────────
@pytest.mark.parametrize("text", [
    "점프2 A33 가격", "갤럭시 와이드5 후기", "Galaxy S25 Ultra", "xiaomi redmi note 9 pro",
    "galaxy fold", "Samsung Galaxy A32 won't start up", "", "Gear S2 sleep tracking",
])
def test_match_product_code_is_the_first_candidate(text):
    """후보 생성기로 바꾸며 match_product_code 의 판정이 달라지면 안 된다."""
    first = next(iter(iter_product_matches(text)), None)
    assert match_product_code(text) == (first[0] if first else None)


# ── 폴백이 사라지면 시끄러워야 한다 ──────────────────────────────────────
def test_import_failure_is_loud_once(monkeypatch, caplog):
    """예전 폴백은 import 실패 시 조용히 None 을 냈다 — 꺼져도 아무도 몰랐다."""
    monkeypatch.setattr(PM, "_legacy_iter", None)
    monkeypatch.setitem(sys.modules, "scripts.relink_products", None)   # import 를 실패시킨다
    with caplog.at_level(logging.ERROR, logger=PM.__name__):
        assert legacy_fallback_code("Galaxy Note 9") is None
        assert legacy_fallback_code("Galaxy Note 9") is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, f"실패를 {len(errors)}번 알렸다 — 한 번이어야 한다(행마다 찍으면 로그 폭주)"
