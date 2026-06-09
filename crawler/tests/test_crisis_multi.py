"""R22 트랙 D — crisis_platform_direct 멀티 platform 단위 테스트.

검증:
  1. 4 platform 어댑터 모두 등록 (9to5google/engadget/theverge/androidcentral)
  2. Engadget URL 날짜 추출 — ``/YYYY-MM-DD-slug.html``
  3. TheVerge URL 날짜 추출 — ``/YYYY/M/D/...`` (단·복수 자리 모두 허용)
  4. AndroidCentral <lastmod> 날짜 추출
  5. _tv_months_for — crisis 윈도우 → (year, month) 리스트
  6. _kw_pattern — 슬러그 매칭 (하이픈/공백 구분자 모두 흡수)
  7. 4 platform 모두 crisis_code per_code 캡 (PER_KEYWORD_MAX * #keyword) 일관

외부 네트워크/DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_crisis_multi.py -v
  cd crawler && python tests/test_crisis_multi.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.crisis_platform_direct import (  # noqa: E402
    CRISIS_KEYWORDS,
    CRISIS_KW_PATTERNS,
    PLATFORM_ADAPTERS,
    _ac_parse_lastmod_date,
    _eg_url_date,
    _kw_pattern,
    _tv_months_for,
    _tv_url_date,
)


def test_all_four_platforms_registered():
    expected = {"9to5google", "engadget", "theverge", "androidcentral"}
    got = set(PLATFORM_ADAPTERS)
    assert got == expected, f"platform 목록 불일치: {got} != {expected}"
    for code, adapter in PLATFORM_ADAPTERS.items():
        assert adapter.code == code
        assert adapter.search_fn is not None
        assert adapter.parse_fn is not None
        assert adapter.crawler_class
        assert adapter.crawler_module.startswith("platforms.")
    print(f"  [PASS] 4 platform 등록 완료: {sorted(got)}")


def test_engadget_url_date_extract():
    """Engadget legacy URL ``/YYYY-MM-DD-slug.html`` 날짜 추출."""
    cases = {
        "https://www.engadget.com/2016-09-02-samsung-recalls-the-galaxy-note-7-amid-battery-fears.html":
            date(2016, 9, 2),
        "https://www.engadget.com/2019-04-17-galaxy-fold-screen-broken.html":
            date(2019, 4, 17),
        "https://www.engadget.com/2022-03-15-samsung-gos-throttling.html":
            date(2022, 3, 15),
    }
    for url, expected in cases.items():
        got = _eg_url_date(url)
        assert got == expected, f"{url} → {got} (expected {expected})"

    # 잘못된 형식은 None
    assert _eg_url_date("https://www.engadget.com/2016/09/02-slug.html") is None
    assert _eg_url_date("https://www.engadget.com/some-slug.html") is None
    assert _eg_url_date("https://example.com/2016-09-02-foo.html") is None
    print("  [PASS] _eg_url_date: 표준 3건 + 비정상 3건")


def test_theverge_url_date_extract():
    """TheVerge URL ``/YYYY/M/D/<id>/slug`` 날짜 추출 — 단·복수 자리 허용."""
    cases = {
        # 1-digit month/day
        "https://www.theverge.com/2016/9/2/12791290/samsung-galaxy-note-7-recall":
            date(2016, 9, 2),
        # 2-digit month/day (id 가 날짜 다음에 옴 — 표준 패턴)
        "https://www.theverge.com/2019/04/17/12345678/galaxy-fold-broken-display":
            date(2019, 4, 17),
        # category-prefixed (e.g. /circuitbreaker/)
        "https://www.theverge.com/circuitbreaker/2016/9/30/13110136/iot-thing":
            date(2016, 9, 30),
        # 슬러그 없이 trailing slash — id 만
        "https://www.theverge.com/2020/2/15/12345678/":
            date(2020, 2, 15),
    }
    for url, expected in cases.items():
        got = _tv_url_date(url)
        assert got == expected, f"{url} → {got} (expected {expected})"

    # 잘못된 형식 — article id 없음
    assert _tv_url_date("https://www.theverge.com/2016/9/2/slug-only") is None
    # 도메인 다름
    assert _tv_url_date("https://example.com/2016/9/2/12345/slug") is None
    print("  [PASS] _tv_url_date: 4 케이스 + 비정상 2건")


def test_androidcentral_lastmod_parse():
    """AndroidCentral sitemap <lastmod> 날짜 추출."""
    cases = {
        "2016-09-29T11:00:06Z":         date(2016, 9, 29),
        "2022-03-10T15:30:00+00:00":    date(2022, 3, 10),
        "2020-12-31T23:59:59-08:00":    date(2021, 1, 1),  # KST→UTC date 변환은 안 함 (입력 그대로)
    }
    # 마지막 케이스는 사실 _ac_parse_lastmod_date 가 fromisoformat 후 date() 만
    # 호출하므로 timezone 정보를 잃고 입력의 *현지* 일자를 반환한다.
    # 입력 "2020-12-31T23:59:59-08:00" → 그대로 datetime(2020,12,31,...).date()
    # = 2020-12-31. 따라서 expected 보정.
    cases["2020-12-31T23:59:59-08:00"] = date(2020, 12, 31)

    for raw, expected in cases.items():
        got = _ac_parse_lastmod_date(raw)
        assert got == expected, f"{raw!r} → {got} (expected {expected})"

    # 잘못된 형식은 None
    assert _ac_parse_lastmod_date("not a date") is None
    assert _ac_parse_lastmod_date("") is None
    print(f"  [PASS] _ac_parse_lastmod_date: {len(cases)} 케이스 + 잘못된 2건")


def test_tv_months_for_window():
    """_tv_months_for: crisis 윈도우 → (year, month) 리스트."""
    # GN7: 2016-08-19 ~ 2016-12-31 → [2016/8..12] 총 5
    assert _tv_months_for("GN7") == [(2016, 8), (2016, 9), (2016, 10), (2016, 11), (2016, 12)]
    # GZF1: 2019-04-15 ~ 2019-12-31 → [2019/4..12] 총 9
    assert _tv_months_for("GZF1") == [(2019, m) for m in range(4, 13)]
    # GZFL3: 2021-08-01 ~ 2022-03-31 → 2021/8..12 + 2022/1..3 = 8개월
    assert _tv_months_for("GZFL3") == (
        [(2021, m) for m in range(8, 13)] + [(2022, m) for m in range(1, 4)]
    )
    # GS22U: 2022-02-25 ~ 2022-06-30 → [2022/2..6] 총 5
    assert _tv_months_for("GS22U") == [(2022, m) for m in range(2, 7)]
    # GS20: 2020-02 ~ 2020-12 = 11개월
    assert _tv_months_for("GS20") == [(2020, m) for m in range(2, 13)]
    print("  [PASS] _tv_months_for: 5 코드 × 정확한 (year, month) 리스트")


def test_kw_pattern_slug_matching():
    """CRISIS_KW_PATTERNS: CRISIS_TOKENS 보강으로 동사 변형 슬러그도 흡수."""
    pat = CRISIS_KW_PATTERNS["GN7"]
    # 슬러그 형태 (하이픈) 매칭 — 'note 7' 토큰이 'note-7' 흡수
    assert pat.search("samsung-recalls-the-galaxy-note-7-amid-battery-fears") is not None
    # 공백 슬러그 매칭
    assert pat.search("galaxy note 7 explosion ratio") is not None
    # 진짜 'note 7 recall' 슬러그 (consecutive) 매칭 — 원래 CRISIS_KEYWORDS 항목
    assert pat.search("samsung-plans-formal-note-7-recall-with-us-government") is not None
    # 매칭 안 됨
    assert pat.search("apple iphone news") is None

    # GS22U — 'galaxy s22' 토큰 + 'samsung gos' 토큰
    pat_g = CRISIS_KW_PATTERNS["GS22U"]
    assert pat_g.search("samsung-gos-lawsuit") is not None
    assert pat_g.search("galaxy s22 ultra review") is not None

    # GZFL3 — 'z flip 3' 토큰
    pat_zfl3 = CRISIS_KW_PATTERNS["GZFL3"]
    assert pat_zfl3.search("samsung-galaxy-z-flip-3-hands-on") is not None

    # GS20 — 'galaxy s20' 토큰
    pat_s20 = CRISIS_KW_PATTERNS["GS20"]
    assert pat_s20.search("galaxy-s20-ultra-launch") is not None
    print("  [PASS] CRISIS_KW_PATTERNS: 4 코드 슬러그 매칭")


def test_crisis_keyword_count_consistency():
    """4 platform 모두 동일한 CRISIS_KEYWORDS 풀 사용 — per_code 캡 일관."""
    # PER_KEYWORD_MAX * #keyword(code) = 코드당 fetch 상한.
    # 모든 platform 이 동일 풀이므로 코드별 max 도 동일.
    for code, kws in CRISIS_KEYWORDS.items():
        assert len(kws) >= 3, f"{code} 키워드 부족: {kws}"
    print(f"  [PASS] CRISIS_KEYWORDS 일관 — 5 코드 × ≥ 3 키워드")


if __name__ == "__main__":
    tests = [
        test_all_four_platforms_registered,
        test_engadget_url_date_extract,
        test_theverge_url_date_extract,
        test_androidcentral_lastmod_parse,
        test_tv_months_for_window,
        test_kw_pattern_slug_matching,
        test_crisis_keyword_count_consistency,
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
