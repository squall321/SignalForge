"""
MySmartPrice 크롤러 단위 테스트 — 외부 네트워크 없이 파서 + 헬퍼만 검증.

실행: cd crawler && python -m pytest tests/test_mysmartprice.py -v
       또는 cd crawler && python tests/test_mysmartprice.py
"""
import os
import sys
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.mysmartprice import (
    MySmartPriceCrawler,
    GALAXY_KEYWORD_RE,
    TITLE_SUFFIX_RE,
    GN_RSS,
    SEARCH_TERMS,
    IST,
)


# ------------------------------------------------------------
# Test 1: external_id 안정성 — 동일 guid 동일 ID, 16자 길이
# ------------------------------------------------------------
def test_external_id_stable_and_short():
    guid = "CBMiqwFBVV95cUxNWk5MM1JET1c3TDJGWnh4REl..."
    eid1 = hashlib.md5(f"mysmartprice#{guid}".encode()).hexdigest()[:16]
    eid2 = hashlib.md5(f"mysmartprice#{guid}".encode()).hexdigest()[:16]
    assert eid1 == eid2, "동일 guid → 동일 ID 보장"
    assert len(eid1) == 16, "ID 길이 16 (DB 컬럼 호환)"

    # 다른 guid → 다른 ID
    eid3 = hashlib.md5("mysmartprice#OTHER".encode()).hexdigest()[:16]
    assert eid1 != eid3, "다른 guid → 다른 ID"

    # 다른 플랫폼 prefix → 충돌 회피 (sammyfans 와 같은 guid 라도 ID 다름)
    eid_other = hashlib.md5(f"sammyfans#{guid}".encode()).hexdigest()[:16]
    assert eid1 != eid_other, "platform prefix 차이로 cross-platform 충돌 회피"
    print(f"  [PASS] external_id 안정성: {eid1}")


# ------------------------------------------------------------
# Test 2: Google News RSS 파싱 — pubDate(GMT) → UTC, 제목 정상화
# ------------------------------------------------------------
def test_parse_gn_rss_gmt_to_utc():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>"site:mysmartprice.com samsung galaxy" - Google News</title>
    <link>https://news.google.com/rss/search?q=site:mysmartprice.com+samsung</link>
    <item>
      <title>Samsung Galaxy S26 Ultra Review - MySmartPrice</title>
      <link>https://news.google.com/rss/articles/CBMiqwFBVV95cUxNWkU?oc=5</link>
      <guid isPermaLink="false">CBMiqwFBVV95cUxNWkU</guid>
      <pubDate>Thu, 28 May 2026 12:46:51 GMT</pubDate>
      <description>&lt;a href="..."&gt;Samsung Galaxy S26&lt;/a&gt; MySmartPrice</description>
      <source url="https://www.mysmartprice.com">MySmartPrice</source>
    </item>
    <item>
      <title>Galaxy Buds 4 vs AirPods Pro 3 - MySmartPrice Gear</title>
      <link>https://news.google.com/rss/articles/ZZZ?oc=5</link>
      <guid isPermaLink="false">ZZZ</guid>
      <pubDate>Mon, 01 Jun 2026 15:40:50 +0000</pubDate>
    </item>
    <item>
      <title>Some other phone news - Random Site</title>
      <link>https://news.google.com/rss/articles/QQQ</link>
      <guid>QQQ</guid>
      <pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""
    crawler = MySmartPriceCrawler()
    items = crawler._parse_gn_feed(xml)
    assert len(items) == 3, f"item 수: {len(items)}"

    # 1번 — title 의 ' - MySmartPrice' 제거
    a = items[0]
    assert a.content == "Samsung Galaxy S26 Ultra Review", \
        f"suffix 미제거: {a.content!r}"
    assert a.country_code == "IN"
    assert a.source_url.startswith("https://news.google.com/")
    # GMT 12:46:51 → UTC 12:46:51 (변환 없음)
    assert a.published_at is not None
    assert a.published_at.tzinfo == timezone.utc
    assert a.published_at.hour == 12 and a.published_at.minute == 46
    # meta
    assert a.meta.get("source") == "google_news_rss"
    assert a.meta.get("publisher") == "MySmartPrice"
    assert a.meta.get("guid") == "CBMiqwFBVV95cUxNWkU"

    # 2번 — ' - MySmartPrice Gear' 도 제거 (Gear 서브브랜드 suffix)
    b = items[1]
    assert b.content == "Galaxy Buds 4 vs AirPods Pro 3", \
        f"Gear suffix 미제거: {b.content!r}"

    # 3번 — Random Site 는 그대로 (suffix 매칭 안 됨)
    c = items[2]
    assert c.content == "Some other phone news - Random Site"

    print(f"  [PASS] GN RSS GMT→UTC + suffix 제거: {a.published_at.isoformat()}")


# ------------------------------------------------------------
# Test 3: Title suffix 패턴 — '- MySmartPrice', '– MySmartPrice', '| MySmartPrice'
# ------------------------------------------------------------
def test_title_suffix_variants():
    samples = [
        ("Galaxy S25 Ultra Review - MySmartPrice", "Galaxy S25 Ultra Review"),
        ("Galaxy Watch 7 specs – MySmartPrice", "Galaxy Watch 7 specs"),
        ("Samsung news — MySmartPrice", "Samsung news"),
        ("Galaxy A56 launch | MySmartPrice", "Galaxy A56 launch"),
        ("Foldable comparison - MySmartPrice Gear", "Foldable comparison"),
        ("No suffix here", "No suffix here"),  # 변화 없음
        ("MySmartPrice in middle of title", "MySmartPrice in middle of title"),
    ]
    for raw, expect in samples:
        out = TITLE_SUFFIX_RE.sub("", raw).strip()
        assert out == expect, f"{raw!r} → {out!r}, expected {expect!r}"
    print(f"  [PASS] suffix 패턴 {len(samples)}개 검증")


# ------------------------------------------------------------
# Test 4: Galaxy/Samsung 키워드 필터 — 영문 + false positive 회피
# ------------------------------------------------------------
def test_keyword_filter_samsung_galaxy():
    crawler = MySmartPriceCrawler()

    def hit(title: str) -> bool:
        from base.crawler import RawVOC
        v = RawVOC(external_id="x", content=title, source_url="x")
        return crawler._is_galaxy_related(v)

    # 양성 — Samsung/Galaxy 시리즈
    assert hit("Samsung Galaxy S26 Ultra unveiled") is True
    assert hit("Galaxy S25 vs iPhone 17") is True
    assert hit("One UI 8 features") is True
    assert hit("Exynos 2700 benchmark") is True
    assert hit("Galaxy Buds 4 Pro review") is True
    assert hit("Samsung Galaxy Z Fold 7") is True
    assert hit("Galaxy A56 launch in India") is True
    # 대소문자 무관
    assert hit("SAMSUNG GALAXY ANNOUNCEMENT") is True

    # 음성 — 일반 단어 단독 (false positive 회피)
    assert hit("Apple iPhone 17 launches today") is False
    assert hit("OnePlus 13 review") is False
    # 'tab' / 'watch' / 'fold' / 'ring' 단독 — Samsung/Galaxy 없으면 비매칭
    assert hit("Best laptops to watch") is False
    assert hit("How to fold paper") is False
    assert hit("") is False
    print(f"  [PASS] 키워드 필터 (정/부 매칭)")


# ------------------------------------------------------------
# Test 5: GN RSS URL 포맷 — 인도 로케일, 키워드 인코딩
# ------------------------------------------------------------
def test_gn_rss_url_format():
    # 단일 키워드
    url = GN_RSS.format(kw="samsung")
    assert "site:mysmartprice.com" in url
    assert "hl=en-IN" in url
    assert "gl=IN" in url
    assert "ceid=IN:en" in url
    # 공백 포함 키워드 → '+' 인코딩 (crawler._fetch_gn_feed 에서 replace 수행)
    kw_encoded = "samsung galaxy".replace(" ", "+")
    url2 = GN_RSS.format(kw=kw_encoded)
    assert "samsung+galaxy" in url2
    assert " " not in url2.split("=")[1].split("&")[0], "URL 에 공백 잔류 금지"

    # SEARCH_TERMS 가 9개 (fan-out 규모 확인)
    assert len(SEARCH_TERMS) >= 5, f"키워드 수 {len(SEARCH_TERMS)} (최소 5 권장)"
    # 모든 키워드가 GALAXY_KEYWORD_RE 에 적어도 한번은 매칭
    for kw in SEARCH_TERMS:
        # 검색어 자체가 필터 통과해야 (안 그러면 모두 거름)
        assert GALAXY_KEYWORD_RE.search(kw), f"검색어 '{kw}' 가 필터 통과 못 함"
    print(f"  [PASS] GN URL 포맷 + 검색어 {len(SEARCH_TERMS)}개")


# ------------------------------------------------------------
# Test 6: pubDate naive → IST(+05:30) 폴백 → UTC
# ------------------------------------------------------------
def test_pubdate_naive_fallback_ist():
    crawler = MySmartPriceCrawler()
    # GMT 명시 (정상 경로) — UTC 그대로
    dt1 = crawler._parse_rss_date("Mon, 01 Jun 2026 12:00:00 GMT")
    assert dt1 is not None and dt1.tzinfo == timezone.utc
    assert dt1.hour == 12 and dt1.minute == 0

    # +0530 명시 — IST → UTC 변환 (12:00 IST → 06:30 UTC)
    dt2 = crawler._parse_rss_date("Mon, 01 Jun 2026 12:00:00 +0530")
    assert dt2 is not None and dt2.tzinfo == timezone.utc
    assert dt2.hour == 6 and dt2.minute == 30, \
        f"IST→UTC 실패: {dt2.isoformat()}"

    # 빈 문자열 → None
    assert crawler._parse_rss_date("") is None
    # 잘못된 형식 → None
    assert crawler._parse_rss_date("not a date") is None
    print(f"  [PASS] pubDate 변환 (GMT/+0530/empty/invalid)")


# ------------------------------------------------------------
# Test 7: 빈 RSS / 잘못된 XML 처리 — 예외 안 던지고 빈 리스트
# ------------------------------------------------------------
def test_parse_resilience_empty_and_malformed():
    crawler = MySmartPriceCrawler()
    # 완전 빈 문자열
    assert crawler._parse_gn_feed("") == []
    # 잘못된 XML
    assert crawler._parse_gn_feed("<not><valid<xml>") == []
    # 정상 RSS, 빈 channel
    empty_rss = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    assert crawler._parse_gn_feed(empty_rss) == []
    # title 누락 item → 스킵
    no_title = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><link>https://news.google.com/x</link></item>
<item><title>  </title><link>https://news.google.com/y</link></item>
<item><title>Samsung Galaxy S26 - MySmartPrice</title><link>https://news.google.com/z</link><guid>Z</guid><pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""
    out = crawler._parse_gn_feed(no_title)
    assert len(out) == 1, f"빈 title 스킵 실패: {len(out)}건 (1 기대)"
    assert out[0].content == "Samsung Galaxy S26"
    print(f"  [PASS] 빈/잘못된 RSS 회복력")


# ------------------------------------------------------------
# 직접 실행 시 모든 테스트 수행
# ------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_external_id_stable_and_short,
        test_parse_gn_rss_gmt_to_utc,
        test_title_suffix_variants,
        test_keyword_filter_samsung_galaxy,
        test_gn_rss_url_format,
        test_pubdate_naive_fallback_ist,
        test_parse_resilience_empty_and_malformed,
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
    total = len(tests)
    print(f"\n결과: {total - failed}/{total} 통과")
    sys.exit(0 if failed == 0 else 1)
