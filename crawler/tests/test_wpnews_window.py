# WP REST 기간 필터를 무시하는 매체를 걸러내는지 검증한다.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from platforms.wpnews import WPNewsCrawler, _SITES


def _c(after="", before=""):
    c = WPNewsCrawler()
    c.after, c.before = after, before
    return c


def _p(date):
    return {"date_gmt": f"{date}T00:00:00", "title": {"rendered": "t"}}


def test_window_respected_when_dates_inside():
    c = _c("2022-01-01T00:00:00", "2022-06-30T00:00:00")
    assert c._window_respected([_p("2022-03-18"), _p("2022-02-09")])


def test_window_ignored_is_detected():
    """200 과 데이터를 주면서 after/before 를 무시하는 사이트가 있다(MobileSyrup).

    못 걸러내면 과거를 긁는 줄 알고 최신만 되풀이 수집한다.
    """
    c = _c("2022-01-01T00:00:00", "2022-06-30T00:00:00")
    assert not c._window_respected([_p("2026-09-11"), _p("2026-09-10")])


def test_boundary_item_counts_as_respected():
    """경계 글이 섞이는 것은 정상이다 — 하나라도 창 안이면 통과."""
    c = _c("2022-01-01T00:00:00", "2022-06-30T00:00:00")
    assert c._window_respected([_p("2026-09-11"), _p("2022-06-29")])


def test_no_window_always_passes():
    assert _c()._window_respected([_p("2026-09-11")])


def test_undated_payload_is_not_judged():
    """날짜를 못 읽으면 판단을 보류한다 — 멀쩡한 매체를 끊으면 안 된다."""
    c = _c("2022-01-01T00:00:00", "")
    assert c._window_respected([{"title": {"rendered": "t"}}])


def test_only_verified_sites_are_listed():
    """실측으로 과거가 나온 매체만 있어야 한다.

    403 이거나 기간 필터를 무시하는 매체를 넣으면 조용히 헛돈다.
    """
    names = {n for n, _ in _SITES}
    for bad in ("MobileSyrup", "Ausdroid", "PhoneArena", "SamsungFans", "XatakaMX"):
        assert bad not in names, f"{bad} 는 역사 수집에 쓸 수 없다(실측)"
    for good in ("Hipertextual", "TechCabal", "MySmartPrice"):
        assert good in names, f"{good} 가 빠졌다"


def test_sites_have_valid_bases():
    for name, base in _SITES:
        assert base.startswith("https://"), f"{name}: {base}"
        assert not base.endswith("/"), f"{name}: 끝 슬래시가 URL 을 깨뜨린다"
