"""R18 트랙 C — crisis_kr_backfill 단위 테스트.

검증:
  1. _ppomppu_extract_post_urls — 검색 결과 HTML 에서 (id,no) 기준 unique
     URL 만 추출하는지 (광고/regulation 제외)
  2. CRISIS_QUERIES 매트릭스 — 모든 위기 코드 키워드 매핑 존재

외부 네트워크/DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_crisis_kr.py -v
  cd crawler && python tests/test_crisis_kr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.crisis_kr_backfill import (  # noqa: E402
    _ppomppu_extract_post_urls,
    CRISIS_QUERIES,
)


# ----- 1) ppomppu URL extractor -----
SAMPLE_PPOMPPU_HTML = """
<html><body>
<a href="https://www.ppomppu.co.kr/zboard/view.php?id=phone&no=3254417&keyword=Note7">Note7 발화 후기</a>
<a href="/zboard/view.php?id=phone&no=3245681&keyword=Note7&page=1">Note7 단종 안내</a>
<a href="/zboard/view.php?id=regulation&page=1&divpage=1&no=49">규정 안내</a>
<a href="/zboard/view.php?id=phone&no=3254417&keyword=Note7&divpage=2">중복 (다른 페이지 쿼리)</a>
<a href="https://www.ppomppu.co.kr/zboard/view.php?id=freeboard&no=9933306">자유 글</a>
<a href="/zboard/view.php?id=etc_board&no=12345">제외 게시판</a>
<a href="/banner.php">광고</a>
</body></html>
"""


def test_ppomppu_extract_dedupes_and_filters():
    urls = _ppomppu_extract_post_urls(SAMPLE_PPOMPPU_HTML)
    # 기대: (phone,3254417), (phone,3245681), (freeboard,9933306) — 3개
    # regulation, etc_board 제외 / 중복 제거 / banner.php 제외
    assert len(urls) == 3, f"기대 3, 실제 {len(urls)}: {urls}"

    joined = " ".join(urls)
    assert "id=phone&no=3254417" in joined
    assert "id=phone&no=3245681" in joined
    assert "id=freeboard&no=9933306" in joined
    # regulation 제외
    assert "regulation" not in joined
    # keyword 쿼리 정리됨
    assert "keyword=" not in joined
    # 절대 URL 정규화
    for u in urls:
        assert u.startswith("https://www.ppomppu.co.kr/zboard/view.php")
    print(f"  [PASS] ppomppu URL extract: {len(urls)}건, dedupe + filter 검증")


def test_ppomppu_extract_empty_html():
    """검색결과 없음 (빈 HTML) → 빈 리스트 반환, 예외 없음."""
    assert _ppomppu_extract_post_urls("<html></html>") == []
    assert _ppomppu_extract_post_urls("") == []
    print("  [PASS] 빈 HTML → 빈 리스트 (예외 없음)")


# ----- 2) CRISIS_QUERIES 매트릭스 검증 -----
def test_crisis_queries_covers_all_5_codes():
    """deep_service.CRISIS_CATALOG 의 5 코드를 모두 커버."""
    expected_codes = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}
    actual_codes = set(CRISIS_QUERIES.keys())
    assert expected_codes == actual_codes, (
        f"누락/추가 코드: 기대={expected_codes}, 실제={actual_codes}"
    )
    # 코드별 최소 2개 키워드
    for code, queries in CRISIS_QUERIES.items():
        assert len(queries) >= 2, f"{code} 키워드 부족: {queries}"
        assert all(isinstance(q, str) and q.strip() for q in queries), f"{code} 빈 키워드"
    print(f"  [PASS] CRISIS_QUERIES: 5 코드 × 평균 "
          f"{sum(len(v) for v in CRISIS_QUERIES.values()) / 5:.1f} 키워드")


if __name__ == "__main__":
    tests = [
        test_ppomppu_extract_dedupes_and_filters,
        test_ppomppu_extract_empty_html,
        test_crisis_queries_covers_all_5_codes,
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
