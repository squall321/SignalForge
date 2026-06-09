"""R19 트랙 C — crisis_backfill_v2 단위 테스트.

검증:
  1. _bobae_extract_post_urls — bobaedream view URL 추출 / 보드 필터 / 중복 제거
  2. _ruli_extract_post_urls — ruliweb 검색 결과 (community / mobile / etcs / news)
     포스트 URL 추출 / market 제외 / 중복 제거
  3. HN_QUERIES + KR_QUERIES 모두 5 위기 코드 커버

외부 네트워크/DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_crisis_v2.py -v
  cd crawler && python tests/test_crisis_v2.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.crisis_backfill_v2 import (  # noqa: E402
    _bobae_extract_post_urls,
    _ruli_extract_post_urls,
    HN_QUERIES,
    KR_QUERIES,
)


# ----- 1) bobaedream URL extractor -----
SAMPLE_BOBAE_HTML = """
<html><body>
<a href="/view?code=strange&No=6926299">노트7 발화 후기</a>
<a href="/view?code=strange&No=6926298">관련 글 1</a>
<a href="/view?code=strange&No=6926298&bm=1">중복(다른 쿼리)</a>
<a href="/view?code=freeb&No=3407997">자유게시판 글</a>
<a href="/view?code=mall&No=99999">제외(상품)</a>
<a href="/view?code=advert&No=1">제외(광고)</a>
<a href="/banner.php">광고</a>
</body></html>
"""


def test_bobae_extract_dedupes_and_filters():
    urls = _bobae_extract_post_urls(SAMPLE_BOBAE_HTML)
    # 기대: (strange,6926299), (strange,6926298), (freeb,3407997) = 3건
    assert len(urls) == 3, f"기대 3, 실제 {len(urls)}: {urls}"
    joined = " ".join(urls)
    assert "code=strange&No=6926299" in joined
    assert "code=strange&No=6926298" in joined
    assert "code=freeb&No=3407997" in joined
    # mall / advert / banner 제외
    assert "code=mall" not in joined
    assert "code=advert" not in joined
    # 절대 URL 정규화
    for u in urls:
        assert u.startswith("https://www.bobaedream.co.kr/view?"), u
    print(f"  [PASS] bobae URL extract: {len(urls)}건, dedupe + filter 검증")


def test_bobae_extract_empty():
    """검색결과 없음/빈 HTML → 빈 리스트, 예외 없음."""
    assert _bobae_extract_post_urls("<html></html>") == []
    assert _bobae_extract_post_urls("") == []
    print("  [PASS] bobae 빈 HTML → 빈 리스트")


# ----- 2) ruliweb URL extractor -----
SAMPLE_RULI_HTML = """
<html><body>
<a href="/etcs/board/300143/read/75223538">노트7 글</a>
<a href="https://bbs.ruliweb.com/community/board/300148/read/12345">커뮤글</a>
<a href="/mobile/board/300009/read/77777">모바일</a>
<a href="/news/read/85054">뉴스(짧은 패턴 제외)</a>
<a href="/market/board/1020/read/55555">제외(거래)</a>
<a href="/etcs/board/300143/read/75223538?cmt=1">중복(쿼리)</a>
<a href="/userboard/board/700286">목록(read 없음)</a>
</body></html>
"""


def test_ruli_extract_dedupes_and_filters():
    urls = _ruli_extract_post_urls(SAMPLE_RULI_HTML)
    # 기대: etcs/300143/75223538, community/300148/12345, mobile/300009/77777 = 3건
    # /news/read/85054 는 board 패턴 아님 → 제외
    # /market/... 거래 게시판 → 제외
    assert len(urls) == 3, f"기대 3, 실제 {len(urls)}: {urls}"
    joined = " ".join(urls)
    assert "etcs/board/300143/read/75223538" in joined
    assert "community/board/300148/read/12345" in joined
    assert "mobile/board/300009/read/77777" in joined
    # market 제외
    assert "/market/" not in joined
    # 뉴스 짧은 패턴 제외
    assert "/news/read/" not in joined
    # 절대 URL 정규화
    for u in urls:
        assert u.startswith("https://bbs.ruliweb.com/"), u
    print(f"  [PASS] ruli URL extract: {len(urls)}건, dedupe + filter 검증")


def test_ruli_extract_empty():
    assert _ruli_extract_post_urls("<html></html>") == []
    assert _ruli_extract_post_urls("") == []
    print("  [PASS] ruli 빈 HTML → 빈 리스트")


# ----- 3) 검색어 매트릭스 -----
def test_query_matrices_cover_all_5_codes():
    expected = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}
    assert set(HN_QUERIES.keys()) == expected, f"HN_QUERIES 코드 불일치: {set(HN_QUERIES)}"
    assert set(KR_QUERIES.keys()) == expected, f"KR_QUERIES 코드 불일치: {set(KR_QUERIES)}"
    for code, queries in HN_QUERIES.items():
        assert len(queries) >= 2, f"HN {code} 키워드 부족: {queries}"
        assert all(isinstance(q, str) and q.strip() for q in queries)
    for code, queries in KR_QUERIES.items():
        assert len(queries) >= 2, f"KR {code} 키워드 부족: {queries}"
        assert all(isinstance(q, str) and q.strip() for q in queries)
    hn_total = sum(len(v) for v in HN_QUERIES.values())
    kr_total = sum(len(v) for v in KR_QUERIES.values())
    print(f"  [PASS] HN={hn_total}개 + KR={kr_total}개 검색어 — 5 코드 모두 커버")


if __name__ == "__main__":
    tests = [
        test_bobae_extract_dedupes_and_filters,
        test_bobae_extract_empty,
        test_ruli_extract_dedupes_and_filters,
        test_ruli_extract_empty,
        test_query_matrices_cover_all_5_codes,
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
