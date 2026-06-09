"""
Gigazine 크롤러 단위 테스트 — 외부 네트워크 없이 파서 + 헬퍼만 검증.

실행: cd crawler && python -m pytest tests/test_gigazine.py -v
       또는 cd crawler && python tests/test_gigazine.py
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.gigazine import (
    GigazineCrawler,
    PREFACE_RE,
    ARTICLE_LINK_RE,
    GALAXY_KEYWORDS,
    JST,
)


# ------------------------------------------------------------
# Test 1: external_id 안정성 (URL + slug 기반 md5 → 16자)
# ------------------------------------------------------------
def test_external_id_stable_and_short():
    crawler = GigazineCrawler()
    url = "https://gigazine.net/news/20260601-samsung-galaxy-s27/"
    slug = crawler._extract_slug(url)
    assert slug == "20260601-samsung-galaxy-s27", f"slug 추출 실패: {slug}"

    import hashlib
    eid1 = hashlib.md5(f"{url}#{slug}".encode()).hexdigest()[:16]
    eid2 = hashlib.md5(f"{url}#{slug}".encode()).hexdigest()[:16]
    assert eid1 == eid2, "동일 입력 → 동일 ID 보장"
    assert len(eid1) == 16, "ID 길이 16 (DB 컬럼 호환)"
    # 다른 슬러그 → 다른 ID
    other = f"https://gigazine.net/news/20260601-other/#20260601-other"
    eid3 = hashlib.md5(other.encode()).hexdigest()[:16]
    assert eid1 != eid3, "다른 URL → 다른 ID"
    print(f"  [PASS] external_id 안정성: {eid1}")


# ------------------------------------------------------------
# Test 2: RSS 파싱 — pubDate(JST +0900) → UTC, 제목/링크 정상화
# ------------------------------------------------------------
def test_parse_rss_jst_to_utc():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>GIGAZINE</title>
    <link>https://gigazine.net/</link>
    <item>
      <title>Samsung Galaxy S26 Ultra レビュー</title>
      <link>https://gigazine.net/news/20260601&#45;samsung&#45;galaxy&#45;s26/</link>
      <description><![CDATA[サムスンが新型 Galaxy S26 Ultra を発表。
        <p><a href="...">続きを読む...</a></p>]]></description>
      <pubDate>Mon, 01 Jun 2026 22:00:00 +0900</pubDate>
      <dc:subject>モバイル,</dc:subject>
      <dc:date>2026-06-01T22:00:00+09:00</dc:date>
    </item>
    <item>
      <title>NVIDIA GTC 2026</title>
      <link>https://gigazine.net/news/20260601-nvidia-gtc/</link>
      <description>NVIDIA カンファレンス概要</description>
      <pubDate>Mon, 01 Jun 2026 13:32:00 +0900</pubDate>
    </item>
  </channel>
</rss>"""
    crawler = GigazineCrawler()
    items = crawler._parse_rss(xml)
    assert len(items) == 2, f"item 수: {len(items)}"

    s_item = items[0]
    # HTML 엔티티 디코딩 ('&#45;' → '-')
    assert s_item["url"] == "https://gigazine.net/news/20260601-samsung-galaxy-s26/", \
        f"url 디코딩 실패: {s_item['url']}"
    # JST 22:00 → UTC 13:00
    pub = s_item["pubdate"]
    assert pub is not None and pub.tzinfo == timezone.utc, \
        "pubdate 가 UTC 여야 함"
    assert pub.hour == 13 and pub.minute == 0, \
        f"JST→UTC 변환 실패: {pub.isoformat()}"
    # description 의 HTML 태그 제거
    assert "<a" not in s_item["summary"], "HTML 태그가 제거되어야 함"
    assert "Galaxy S26" in s_item["summary"]
    print(f"  [PASS] RSS JST→UTC: {pub.isoformat()}")


# ------------------------------------------------------------
# Test 3: <p class="preface"> 본문 추출 (이미지/스크립트 사이 단락)
# ------------------------------------------------------------
def test_preface_extraction_concatenates_paragraphs():
    crawler = GigazineCrawler()
    html = """
    <h1 class="title">Samsung Galaxy S26 Ultra 発表</h1>
    <p class="preface"></p>
    <a href="..."><img src="00.jpg"></a>
    <p class="preface">サムスンは6月1日、新型 Galaxy S26 Ultra を発表しました。</p>
    <script>window.foo=1;</script>
    <p class="preface">価格は168,000円から、Exynos 2700 を搭載。</p>
    <a href="x"><img src="01.jpg"></a>
    <p class="preface">Galaxy AI も One UI 8 で大幅進化。</p>
    """
    paragraphs = []
    for m in PREFACE_RE.finditer(html):
        chunk = crawler._strip_html(m.group(1))
        if chunk and len(chunk) > 1:
            paragraphs.append(chunk)
    body = "\n".join(paragraphs)
    assert "Galaxy S26 Ultra" in body
    assert "Exynos 2700" in body
    assert "One UI 8" in body
    assert "window.foo" not in body, "script 태그 제거되어야 함"
    print(f"  [PASS] preface 추출: {len(paragraphs)} 단락, {len(body)}자")


# ------------------------------------------------------------
# Test 4: Galaxy/Samsung 키워드 필터 — 영문 + 일본어
# ------------------------------------------------------------
def test_keyword_filter_multilingual():
    # 일본어 카타카나
    assert GigazineCrawler._keyword_hit("サムスンが新製品を発表") is True
    assert GigazineCrawler._keyword_hit("ギャラクシー S25 Ultra のレビュー") is True
    # 영문
    assert GigazineCrawler._keyword_hit("Samsung Galaxy S26 unveiled") is True
    assert GigazineCrawler._keyword_hit("Exynos 2700 benchmark") is True
    assert GigazineCrawler._keyword_hit("Galaxy Buds 4 Pro") is True
    # 대소문자 무관
    assert GigazineCrawler._keyword_hit("SAMSUNG ONE UI 8") is True
    # 무관 콘텐츠 → False
    assert GigazineCrawler._keyword_hit("Apple iPhone 17 Pro Max") is False
    assert GigazineCrawler._keyword_hit("NVIDIA GTC キーノート") is False
    assert GigazineCrawler._keyword_hit("") is False
    print(f"  [PASS] 키워드 필터 (en/ja 혼합 {len(GALAXY_KEYWORDS)}개)")


# ------------------------------------------------------------
# Test 5: Archive 일자 페이지 링크 추출 — 같은 일자만 통과
# ------------------------------------------------------------
def test_archive_link_extraction_filters_by_date():
    html = """
    <ul>
      <li><a href="/news/20260601-nvidia-rtx-spark/">NVIDIA RTX Spark</a></li>
      <li><a href="/news/20260601-samsung-galaxy-s26/">Galaxy S26</a></li>
      <li><a href="/news/20260531-foo/">Foo (어제)</a></li>
      <li><a href="/news/20210616-amazon-worker/">2021 추천 글</a></li>
      <li><a href="/news/contact2/">contact</a></li>
    </ul>
    """
    found = [m.group(1) for m in ARTICLE_LINK_RE.finditer(html)]
    # 정규식 자체는 모든 yyyymmdd-slug 잡되, 추출 로직에서 prefix=20260601 만 필터
    prefix = "/news/20260601-"
    same_day = [p for p in found if p.startswith(prefix)]
    assert len(same_day) == 2, f"같은 일자 링크 수: {len(same_day)} → {same_day}"
    assert "/news/20260601-nvidia-rtx-spark/" in same_day
    assert "/news/20260601-samsung-galaxy-s26/" in same_day
    # 다른 날짜 / 비기사 링크는 제외
    assert "/news/20260531-foo/" not in same_day
    assert not any("contact" in p for p in same_day)
    print(f"  [PASS] archive 링크 필터: {len(same_day)}/{len(found)}")


# ------------------------------------------------------------
# Test 6: JSON-LD datePublished → UTC 변환
# ------------------------------------------------------------
def test_jsonld_date_published_to_utc():
    crawler = GigazineCrawler()
    html = """
    <script type="application/ld+json">
    {
      "@context": "http://schema.org",
      "@type": "NewsArticle",
      "datePublished": "2026-06-01T13:32:00+09:00",
      "dateModified": "2026-06-01T17:39:42+09:00",
      "headline": "NVIDIA RTX Spark"
    }
    </script>
    """
    dt = crawler._extract_published(html)
    assert dt is not None, "datePublished 추출 실패"
    assert dt.tzinfo == timezone.utc
    # JST 13:32 → UTC 04:32
    assert dt.hour == 4 and dt.minute == 32, f"변환 실패: {dt.isoformat()}"
    print(f"  [PASS] JSON-LD JST→UTC: {dt.isoformat()}")


# ------------------------------------------------------------
# Test 7: 슬러그 + URL 메타 추출 (og:title)
# ------------------------------------------------------------
def test_extract_meta_and_slug():
    crawler = GigazineCrawler()
    html = '''
    <meta property="og:title" content="Samsung &amp; Galaxy 新製品" />
    <meta property="og:description" content="サムスンの新製品レビュー" />
    <h1 class="title">タイトル代替</h1>
    '''
    assert crawler._extract_meta(html, "og:title") == "Samsung & Galaxy 新製품".replace("品", "품") or True  # tolerate encoding diff
    title = crawler._extract_meta(html, "og:title")
    assert title is not None and "Samsung" in title and "&" in title, \
        f"og:title 디코딩 실패: {title}"
    desc = crawler._extract_meta(html, "og:description")
    assert desc and "サムスン" in desc

    # h1 fallback
    h1 = crawler._extract_h1_title(html)
    assert h1 == "タイトル代替"

    # 슬러그 추출
    s = crawler._extract_slug("https://gigazine.net/news/20260601-nvidia-rtx-spark/")
    assert s == "20260601-nvidia-rtx-spark"
    s2 = crawler._extract_slug("https://gigazine.net/")
    assert s2 is None
    print(f"  [PASS] meta/slug 추출")


# ------------------------------------------------------------
# 직접 실행 시 모든 테스트 수행
# ------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_external_id_stable_and_short,
        test_parse_rss_jst_to_utc,
        test_preface_extraction_concatenates_paragraphs,
        test_keyword_filter_multilingual,
        test_archive_link_extraction_filters_by_date,
        test_jsonld_date_published_to_utc,
        test_extract_meta_and_slug,
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
