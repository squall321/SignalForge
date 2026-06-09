"""R20 트랙 B — crisis_platform_backfill 단위 테스트.

검증:
  1. KR_QUERIES_V3 — 5 위기 코드 모두 커버, v2 root 키워드 포함, GS22U/GZFL3
     보강 카운트 ≥ 8
  2. _mlbpark_build_search_url — keyword URL-encode + page 1-base → 30 step
     offset 변환 (p=1 → p_offset=1, p=2 → p_offset=31, ...)

외부 네트워크/DB 호출 없음.

실행:
  cd crawler && python -m pytest tests/test_crisis_platform.py -v
  cd crawler && python tests/test_crisis_platform.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.crisis_platform_backfill import (  # noqa: E402
    KR_QUERIES_V3,
    _mlbpark_build_search_url,
)


# ----- 1) KR_QUERIES_V3 매트릭스 -----
def test_kr_queries_v3_covers_all_5_codes():
    expected = {"GN7", "GZF1", "GS22U", "GZFL3", "GS20"}
    assert set(KR_QUERIES_V3.keys()) == expected, (
        f"코드 불일치: {set(KR_QUERIES_V3)}"
    )

    # v2 root 키워드 포함 (regression guard)
    v2_roots = {
        "GN7":   "노트7 발화",
        "GZF1":  "폴드1 액정",
        "GS22U": "게옵스",
        "GZFL3": "플립3 힌지",
        "GS20":  "S20 가격",
    }
    for code, root in v2_roots.items():
        assert root in KR_QUERIES_V3[code], (
            f"{code} v2 root '{root}' 누락: {KR_QUERIES_V3[code]}"
        )

    # GS22U, GZFL3 강화 — 각각 8건 이상
    assert len(KR_QUERIES_V3["GS22U"]) >= 8, (
        f"GS22U 키워드 부족 (현재 {len(KR_QUERIES_V3['GS22U'])}건): "
        f"{KR_QUERIES_V3['GS22U']}"
    )
    assert len(KR_QUERIES_V3["GZFL3"]) >= 8, (
        f"GZFL3 키워드 부족 (현재 {len(KR_QUERIES_V3['GZFL3'])}건): "
        f"{KR_QUERIES_V3['GZFL3']}"
    )

    # 비어있는 키워드 없음
    for code, queries in KR_QUERIES_V3.items():
        assert all(isinstance(q, str) and q.strip() for q in queries), (
            f"{code} 빈 키워드 포함: {queries}"
        )

    total = sum(len(v) for v in KR_QUERIES_V3.values())
    print(f"  [PASS] KR_QUERIES_V3: 5 코드 / {total} 검색어 — v2 root 보존 + GS22U/GZFL3 ≥ 8")


# ----- 2) MLB Park search URL builder -----
def test_mlbpark_search_url_page_offset_and_encode():
    # page=1 → offset 1
    u1 = _mlbpark_build_search_url("게옵스", 1)
    assert "p=1" in u1.split("&p=")[1] or "&p=1" in u1, u1
    assert u1.startswith("https://mlbpark.donga.com/mp/b.php"), u1
    assert "b=bullpen" in u1
    assert "select=sct" in u1
    assert "m=search" in u1
    # URL-encode 검증 (UTF-8 percent-encoding)
    assert "%EA%B2%8C%EC%98%B5%EC%8A%A4" in u1, f"한글 인코딩 누락: {u1}"

    # page=2 → offset 31
    u2 = _mlbpark_build_search_url("S22", 2)
    assert "p=31" in u2, u2
    # page=3 → offset 61
    u3 = _mlbpark_build_search_url("plip3", 3)
    assert "p=61" in u3, u3

    # query parameter 직접 검증 — ASCII keyword
    u_ascii = _mlbpark_build_search_url("Fold", 1)
    assert "query=Fold" in u_ascii, u_ascii

    print("  [PASS] mlbpark URL: page 1→1, 2→31, 3→61 + 한글 URL-encode")


# ----- 3) Site runner registry -----
def test_site_runners_registered():
    from scripts.crisis_platform_backfill import SITE_RUNNERS
    expected = {"dcinside", "ppomppu", "bobaedream", "ruliweb", "fmkorea", "mlbpark"}
    assert set(SITE_RUNNERS.keys()) == expected, (
        f"SITE_RUNNERS 등록 불일치: {set(SITE_RUNNERS)}"
    )
    # 모두 async callable
    import asyncio as _aio
    for site, fn in SITE_RUNNERS.items():
        assert _aio.iscoroutinefunction(fn), f"{site} runner 가 coroutine 이 아님"
    print(f"  [PASS] SITE_RUNNERS: {len(SITE_RUNNERS)}개 등록 (mlbpark 신규 포함)")


if __name__ == "__main__":
    tests = [
        test_kr_queries_v3_covers_all_5_codes,
        test_mlbpark_search_url_page_offset_and_encode,
        test_site_runners_registered,
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
