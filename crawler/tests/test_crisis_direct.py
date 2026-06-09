"""R21 트랙 D — crisis_platform_direct 단위 테스트.

검증:
  1. URL 날짜 추출 — 9to5G 표준 ``/YYYY/MM/DD/slug/`` 패턴
  2. crisis 윈도우 필터 — 5 코드 모두 한 케이스 inside / outside
  3. CRISIS_KEYWORDS 매트릭스 — 5 코드 모두 ≥ 3 키워드

외부 네트워크/DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_crisis_direct.py -v
  cd crawler && python tests/test_crisis_direct.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.crisis_platform_direct import (  # noqa: E402
    CRISIS_KEYWORDS,
    CRISIS_WINDOWS,
    _extract_date,
    _in_window,
)


def test_extract_date_standard_urls():
    """9to5G 표준 URL 에서 (Y, M, D) 정확히 추출."""
    cases = {
        "https://9to5google.com/2016/09/05/samsung-out-as-much-as-1-billion/": date(2016, 9, 5),
        "https://9to5google.com/2019/04/17/galaxy-fold-broken-display/":      date(2019, 4, 17),
        "https://9to5google.com/2022/03/10/samsung-gos-update-coming/":        date(2022, 3, 10),
        "https://9to5google.com/2021/08/11/galaxy-z-fold-3-hands-on/":          date(2021, 8, 11),
    }
    for url, expected in cases.items():
        got = _extract_date(url)
        assert got == expected, f"{url} → {got} (expected {expected})"

    # 잘못된 형식은 None
    assert _extract_date("https://9to5google.com/guides/samsung/") is None
    assert _extract_date("https://9to5google.com/page/2/") is None
    assert _extract_date("https://example.com/2022/03/10/foo/") is None  # 다른 도메인
    print("  [PASS] _extract_date: 표준 4건 + 비정상 3건")


def test_window_filter_5_codes():
    """5 crisis 코드 모두 inside / outside 한 건씩 검증."""
    # 각 코드: (inside, outside_before, outside_after)
    cases = {
        "GN7":   (date(2016, 9, 5),  date(2016, 8, 18), date(2017, 1, 1)),
        "GZF1":  (date(2019, 4, 17), date(2019, 4, 14), date(2020, 1, 1)),
        "GS22U": (date(2022, 3, 10), date(2022, 2, 24), date(2022, 7, 1)),
        "GZFL3": (date(2021, 8, 11), date(2021, 7, 31), date(2022, 4, 1)),
        "GS20":  (date(2020, 3, 1),  date(2020, 1, 31), date(2021, 1, 1)),
    }
    for code, (inside, before, after) in cases.items():
        assert _in_window(inside, code), f"{code} inside {inside} 실패"
        assert not _in_window(before, code), f"{code} before {before} 통과 (안 됨)"
        assert not _in_window(after, code),  f"{code} after  {after}  통과 (안 됨)"

    # 윈도우 경계 — 시작/끝 포함
    for code, (s, e) in CRISIS_WINDOWS.items():
        assert _in_window(s, code), f"{code} 시작일 {s} 미포함"
        assert _in_window(e, code), f"{code} 종료일 {e} 미포함"
    print(f"  [PASS] _in_window: 5 코드 × (in / before / after) + 경계")


def test_crisis_keywords_matrix():
    """모든 crisis 코드 마다 ≥ 3 키워드, 빈 문자열 없음."""
    expected = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}
    assert set(CRISIS_KEYWORDS) == expected, f"코드 불일치: {set(CRISIS_KEYWORDS)}"
    for code, kws in CRISIS_KEYWORDS.items():
        assert len(kws) >= 3, f"{code} 키워드 부족: {kws}"
        assert all(isinstance(k, str) and k.strip() for k in kws), (
            f"{code} 빈/잘못된 키워드: {kws}"
        )
    total = sum(len(v) for v in CRISIS_KEYWORDS.values())
    print(f"  [PASS] CRISIS_KEYWORDS: 5 코드 / {total} 키워드 (각 ≥ 3)")


if __name__ == "__main__":
    tests = [
        test_extract_date_standard_urls,
        test_window_filter_5_codes,
        test_crisis_keywords_matrix,
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
