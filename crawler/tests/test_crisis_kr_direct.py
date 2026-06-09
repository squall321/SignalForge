"""R24 트랙 D — crisis_kr_direct 단위 테스트.

검증 (네트워크/DB 미사용):
  1. _clien_search_url       — page=0 / page>=1 분기, URL-encode
  2. _dc_search_url           — page=1 / page>=2 분기, URL-encode
  3. _clien_extract_post_urls — (board, id) 기준 unique, fragment 제거
  4. _dc_extract_post_urls    — Mustache 템플릿 행 제외, (id, no) unique
  5. CRISIS_KR_KEYWORDS       — 5 코드 모두 커버, 코드당 >=3 키워드
  6. _filter_in_window         — body published_at 기준 윈도우 필터

실행:
  cd crawler && python -m pytest tests/test_crisis_kr_direct.py -v
  cd crawler && python tests/test_crisis_kr_direct.py
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from scripts.crisis_kr_direct import (  # noqa: E402
    _clien_search_url,
    _dc_search_url,
    _clien_extract_post_urls,
    _dc_extract_post_urls,
    _filter_in_window,
    CRISIS_KR_KEYWORDS,
)


# ──────────────── 1) Clien 검색 URL ────────────────
def test_clien_search_url_page0_omits_p():
    """page<=0 일 때 ``&p=`` 누락 (Discovery 패턴과 일치)."""
    url = _clien_search_url("노트7 발화", 0)
    assert url.startswith("https://www.clien.net/service/search?")
    assert "q=%EB%85%B8%ED%8A%B87" in url
    assert "sort=recency" in url
    assert "isBoard=false" in url
    assert "&p=" not in url
    print(f"  [PASS] clien p0: {url[:80]}...")


def test_clien_search_url_page1_appends_p():
    url = _clien_search_url("Z 플립3 힌지", 2)
    assert "&p=2" in url
    # 공백은 ``+`` 인코딩
    assert "Z+%ED%94%8C%EB%A6%BD3" in url
    print(f"  [PASS] clien p2: {url[:90]}...")


# ──────────────── 2) DCInside 검색 URL ────────────────
def test_dc_search_url_page1_omits_p():
    url = _dc_search_url("GoS 사태", 1)
    assert url.startswith("https://search.dcinside.com/post/q/")
    assert "?p=" not in url
    # path segment 는 ``%20`` 인코딩 (DC search 가 ``+`` 거부 → 400)
    assert "%20" in url
    assert "+" not in url
    print(f"  [PASS] dc p1: {url}")


def test_dc_search_url_page2_appends_p():
    url = _dc_search_url("GoS 사태", 3)
    assert url.startswith("https://search.dcinside.com/post/q/")
    assert "?p=3" in url
    assert "%20" in url
    print(f"  [PASS] dc p3: {url}")


# ──────────────── 3) Clien post URL 추출 ────────────────
SAMPLE_CLIEN_HTML = """
<a href="/service/board/lecture/19198632?combine=true&q=%EB%85%B8%ED%8A%B87&p=0">제목</a>
<a href="/service/board/lecture/19198632?combine=true&q=%EB%85%B8%ED%8A%B87&p=0#comment-point">댓글</a>
<a href="/service/board/park/19197880?combine=true&q=%EB%85%B8%ED%8A%B87">A</a>
<a href="/service/board/park/19197880?combine=true&q=%EB%85%B8%ED%8A%B87#comment-point">A</a>
<a href="/service/board/cm_stock/19198000?combine=true">B</a>
<a href="/service/search?q=다른쿼리">검색링크</a>
<a href="/service/board/park">board nav</a>
"""


def test_clien_extract_dedupes_and_normalizes():
    urls = _clien_extract_post_urls(SAMPLE_CLIEN_HTML)
    # 기대: (lecture, 19198632), (park, 19197880), (cm_stock, 19198000) — 3건
    assert len(urls) == 3, f"기대 3, 실제 {len(urls)}: {urls}"
    joined = " ".join(urls)
    assert "/lecture/19198632" in joined
    assert "/park/19197880" in joined
    assert "/cm_stock/19198000" in joined
    # 쿼리/fragment 제거된 canonical
    for u in urls:
        assert u.startswith("https://www.clien.net/service/board/")
        assert "?" not in u
        assert "#" not in u
    # board nav (id 없음) / 검색링크 제외
    assert all("/service/board/park" != u for u in urls), \
        "id 없는 board nav 가 포함됨"
    print(f"  [PASS] clien URL extract: {len(urls)}건, dedupe + normalize 검증")


def test_clien_extract_empty():
    assert _clien_extract_post_urls("") == []
    assert _clien_extract_post_urls("<html></html>") == []
    print("  [PASS] clien 빈 HTML → 빈 리스트")


# ──────────────── 4) DCInside post URL 추출 ────────────────
SAMPLE_DC_HTML = """
<a href="https://gall.dcinside.com/mgallery/board/view/?id=enban&no=1050453">A</a>
<a href="https://gall.dcinside.com/mgallery/board/view/?id=enban&no=1050453&search_pos=-123">중복 다른 search_pos</a>
<a href="https://gall.dcinside.com/board/view/?id=smartphone&no=987654">B</a>
<a href="https://gall.dcinside.com/{{if type == 'MI'}}mini/{{/if}}${code_id}">템플릿</a>
<a href="https://gall.dcinside.com/board/lists/?id=dclottery">갤러리 list (포스트 아님)</a>
<a href="https://gall.dcinside.com/m">nav</a>
"""


def test_dc_extract_dedupes_and_skips_templates():
    urls = _dc_extract_post_urls(SAMPLE_DC_HTML)
    # 기대: (enban, 1050453), (smartphone, 987654) — 2건
    assert len(urls) == 2, f"기대 2, 실제 {len(urls)}: {urls}"
    joined = " ".join(urls)
    assert "id=enban&no=1050453" in joined
    assert "id=smartphone&no=987654" in joined
    # 템플릿 잡음 제외
    assert "{{" not in joined and "}}" not in joined
    print(f"  [PASS] dc URL extract: {len(urls)}건, dedupe + template-skip 검증")


def test_dc_extract_empty():
    assert _dc_extract_post_urls("") == []
    assert _dc_extract_post_urls("<html></html>") == []
    print("  [PASS] dc 빈 HTML → 빈 리스트")


# ──────────────── 5) CRISIS_KR_KEYWORDS 매트릭스 ────────────────
def test_crisis_kr_keywords_covers_5_codes():
    expected = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}
    actual = set(CRISIS_KR_KEYWORDS.keys())
    assert expected == actual, f"누락/추가 코드: 기대={expected}, 실제={actual}"
    for code, kws in CRISIS_KR_KEYWORDS.items():
        assert len(kws) >= 3, f"{code} 키워드 부족 (<3): {kws}"
        assert all(isinstance(k, str) and k.strip() for k in kws), \
            f"{code} 빈 키워드 포함"
    avg = sum(len(v) for v in CRISIS_KR_KEYWORDS.values()) / 5
    print(f"  [PASS] CRISIS_KR_KEYWORDS: 5 코드 × 평균 {avg:.1f} 키워드")


# ──────────────── 6) 윈도우 필터 ────────────────
def _make_body(url: str, pub: datetime) -> RawVOC:
    return RawVOC(
        external_id=hashlib.md5(url.encode()).hexdigest()[:16],
        content="body",
        source_url=url,
        author_name="A",
        published_at=pub,
        country_code="KR",
    )


def _make_comment(url: str, idx: int) -> RawVOC:
    return RawVOC(
        external_id=hashlib.md5(f"{url}#c{idx}".encode()).hexdigest()[:16],
        content="comment",
        source_url=url,
        author_name="B",
        published_at=None,
        country_code="KR",
    )


def test_filter_in_window_keeps_in_and_drops_out():
    """body 가 윈도우 안이면 댓글까지 유지, 밖이면 댓글까지 drop."""
    url_in = "https://x/post/1"
    url_out = "https://x/post/2"
    url_unknown = "https://x/post/3"

    # GN7 윈도우 (2016-08-19 ~ 2016-12-31)
    in_pub = datetime(2016, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    out_pub = datetime(2017, 5, 1, 0, 0, 0, tzinfo=timezone.utc)

    vocs = [
        _make_body(url_in, in_pub),
        _make_comment(url_in, 1),
        _make_comment(url_in, 2),
        _make_body(url_out, out_pub),
        _make_comment(url_out, 1),
        _make_body(url_unknown, None),  # 날짜 미상 → drop
    ]

    kept = _filter_in_window(vocs, "GN7")
    kept_urls = {v.source_url for v in kept}
    assert kept_urls == {url_in}, f"기대 {{{url_in}}}, 실제 {kept_urls}"
    # body+댓글 2 = 3건 유지
    assert len(kept) == 3, f"기대 3, 실제 {len(kept)}"
    print(f"  [PASS] _filter_in_window: GN7 윈도우 유지 {len(kept)}/총 6")


if __name__ == "__main__":
    tests = [
        test_clien_search_url_page0_omits_p,
        test_clien_search_url_page1_appends_p,
        test_dc_search_url_page1_omits_p,
        test_dc_search_url_page2_appends_p,
        test_clien_extract_dedupes_and_normalizes,
        test_clien_extract_empty,
        test_dc_extract_dedupes_and_skips_templates,
        test_dc_extract_empty,
        test_crisis_kr_keywords_covers_5_codes,
        test_filter_in_window_keeps_in_and_drops_out,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n결과: {len(tests) - failed}/{len(tests)} 통과")
    sys.exit(0 if failed == 0 else 1)
