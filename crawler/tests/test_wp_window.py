# WP REST 기간 창 공용 모듈과 그 배선을 검증한다.
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.wp_window import (  # noqa: E402
    describe, read_window, warn_ignored, window_params, window_respected,
)

# 이 공용 모듈을 쓰기로 한 크롤러
WIRED = ["hipertextual", "mobile_review", "jagatreview"]


def _p(date):
    return {"date_gmt": f"{date}T00:00:00"}


# ── 창 읽기 ────────────────────────────────────────────────────────────
def test_prefix_env_wins(monkeypatch):
    monkeypatch.setenv("HIPERTEXTUAL_AFTER", "2022-01-01T00:00:00")
    monkeypatch.setenv("WP_BACKFILL_AFTER", "2019-01-01T00:00:00")
    after, _ = read_window("hipertextual")
    assert after.startswith("2022"), "접두 env 가 공통값을 이겨야 한다"


def test_common_env_is_fallback(monkeypatch):
    monkeypatch.delenv("JAGATREVIEW_AFTER", raising=False)
    monkeypatch.setenv("WP_BACKFILL_AFTER", "2019-01-01T00:00:00")
    after, _ = read_window("jagatreview")
    assert after.startswith("2019")


def test_no_env_means_no_window(monkeypatch):
    for k in ("X_AFTER", "X_BEFORE", "WP_BACKFILL_AFTER", "WP_BACKFILL_BEFORE"):
        monkeypatch.delenv(k, raising=False)
    assert read_window("x") == ("", "")
    assert window_params("", "") == {}, "창이 없으면 평소대로 최신을 긁어야 한다"


def test_params_only_include_given_bounds():
    assert window_params("2022-01-01T00:00:00", "") == {"after": "2022-01-01T00:00:00"}
    assert window_params("", "2022-07-01T00:00:00") == {"before": "2022-07-01T00:00:00"}


# ── 필터 무시 감지 ─────────────────────────────────────────────────────
def test_respected_when_inside():
    assert window_respected([_p("2022-03-18")], "2022-01-01", "2022-06-30")


def test_ignored_is_detected():
    """200 과 데이터를 주면서 after/before 를 무시하는 사이트가 있다(MobileSyrup)."""
    assert not window_respected([_p("2026-09-11"), _p("2026-09-10")],
                                "2022-01-01", "2022-06-30")


def test_boundary_item_passes():
    assert window_respected([_p("2026-09-11"), _p("2022-06-29")],
                            "2022-01-01", "2022-06-30")


def test_no_window_always_passes():
    assert window_respected([_p("2026-09-11")], "", "")


def test_undated_is_not_judged():
    """날짜를 못 읽으면 판단 보류 — 멀쩡한 매체를 끊으면 안 된다."""
    assert window_respected([{"title": "x"}], "2022-01-01", "")


def test_non_dict_items_are_skipped():
    assert window_respected(["garbage", 3], "2022-01-01", "")


def test_describe_is_readable():
    assert describe("", "") == "전체"
    assert "2022" in describe("2022-01-01T00:00:00", "")


# ── 배선 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", WIRED)
def test_crawler_applies_window(name):
    src = (ROOT / "platforms" / f"{name}.py").read_text()
    assert "window_params" in src, f"{name}: 기간 창을 얹지 않는다"
    assert "read_window" in src


@pytest.mark.parametrize("name", WIRED)
def test_crawler_checks_ignored_filter(name):
    """창을 얹기만 하고 무시 여부를 안 보면 조용히 최신만 긁는다."""
    src = (ROOT / "platforms" / f"{name}.py").read_text()
    assert "window_respected" in src, f"{name}: 필터 무시를 검사하지 않는다"
    assert "warn_ignored" in src, f"{name}: 무시를 알리지 않는다"


def test_hipertextual_retry_path_also_windowed():
    """403 재시도에도 창을 얹어야 한다 — 빠뜨리면 그 요청만 최신을 긁는다."""
    src = (ROOT / "platforms" / "hipertextual.py").read_text()
    assert src.count("**_win,") >= 2, "본 요청과 재시도 양쪽에 창이 있어야 한다"


def test_no_duplicated_window_logic():
    """각자 복제하면 반드시 갈라진다 — 공용 모듈만 쓴다."""
    for name in WIRED:
        src = (ROOT / "platforms" / f"{name}.py").read_text()
        assert "def _window_respected" not in src, f"{name}: 자체 구현이 있다"


# ── arXiv (WP 는 아니지만 같은 기간 창 규약) ──────────────────────────
def test_arxiv_uses_https():
    """http 는 301 로 리다이렉트된다 — 리다이렉트를 안 따르면 빈 본문이 와서
    XML 파싱이 터진다(실측 2026-09-15)."""
    from platforms.arxiv import ARXIV_API
    assert ARXIV_API.startswith("https://"), ARXIV_API


def test_arxiv_window_format(monkeypatch):
    """arXiv 문법은 YYYYMMDDHHMM 이다."""
    import importlib
    monkeypatch.setenv("ARXIV_AFTER", "2022-01-01")
    monkeypatch.setenv("ARXIV_BEFORE", "2022-07-01T00:00:00")
    import platforms.arxiv as a
    importlib.reload(a)
    after, before = a._date_window()
    assert after == "202201010000", after
    assert before == "202207010000", before


def test_arxiv_no_window_by_default(monkeypatch):
    """창이 없으면 평소대로 최신을 긁어야 한다."""
    import importlib
    monkeypatch.delenv("ARXIV_AFTER", raising=False)
    monkeypatch.delenv("ARXIV_BEFORE", raising=False)
    import platforms.arxiv as a
    importlib.reload(a)
    assert a._date_window() == ("", "")


def test_arxiv_garbage_window_is_ignored(monkeypatch):
    """이상한 값에 죽지 않고 창 없음으로 떨어져야 한다."""
    import importlib
    monkeypatch.setenv("ARXIV_AFTER", "어제")
    import platforms.arxiv as a
    importlib.reload(a)
    assert a._date_window()[0] == ""


# ── xataka_mx 태그 페이지네이션 ────────────────────────────────────────
def test_xataka_defaults_keep_current_behaviour(monkeypatch):
    """기본은 첫 페이지 하나 — 실시간 수집을 건드리면 안 된다."""
    import importlib
    for k in ("XATAKA_MX_TAG_PAGES", "XATAKA_MX_TAG_START", "XATAKA_MX_MAX_OFFSET"):
        monkeypatch.delenv(k, raising=False)
    import platforms.xataka_mx as x
    importlib.reload(x)
    assert x.TAG_PAGES == 1
    assert x.TAG_START == 0


def test_xataka_stops_at_site_limit(monkeypatch):
    """사이트가 offset 200 부터 410 Gone 을 준다(실측). 더 요청하면 낭비다."""
    import importlib
    monkeypatch.delenv("XATAKA_MX_MAX_OFFSET", raising=False)
    import platforms.xataka_mx as x
    importlib.reload(x)
    assert x.MAX_OFFSET <= 180, f"한계가 {x.MAX_OFFSET} — 410 구간을 요청한다"
    assert x.RECORDS_PER_PAGE == 20


def test_xataka_treats_410_as_end():
    """404 만 보다가 410 을 놓치면 그 태그가 통째로 0건이 된다."""
    src = (ROOT / "platforms" / "xataka_mx.py").read_text()
    assert "(404, 410)" in src, "410 Gone 을 종료 신호로 처리하지 않는다"


def test_xataka_pagination_url_shape():
    """`/pagina/N` 같은 흔한 패턴은 404 다 — 실측한 `/record/<offset>` 만 쓴다.

    주석에는 그 사실을 적어두므로 **URL 을 조립하는 코드 줄만** 본다.
    """
    src = (ROOT / "platforms" / "xataka_mx.py").read_text()
    code = [ln for ln in src.splitlines()
            if "tag/" in ln and "f\"" in ln and not ln.strip().startswith("#")]
    assert code, "태그 URL 조립 줄을 못 찾았다"
    joined = " ".join(code)
    assert "/record/" in joined, joined
    assert "/pagina/" not in joined, joined
