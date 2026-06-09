"""Stage 5C T1 — NL/CA/CN 공백 보강 3개 신규 크롤러 단위 테스트.

cover:
- nu.nl (NL)
- iphoneincanada.ca (CA)
- sspai (CN)

직접 RSS XML 샘플 파싱 + Galaxy/Samsung 키워드 필터 + external_id 안정성.
네트워크 호출 없음.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.nu_nl import NuNLCrawler
from platforms.iphoneincanada import IPhoneInCanadaCrawler
from platforms.sspai import SspaiCrawler


# --------------------------- nu.nl --------------------------------

NU_NL_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>NU - Tech</title>
  <item>
    <title>Samsung Galaxy S26 review uitgelekt</title>
    <link>https://www.nu.nl/tech/123/abc.html</link>
    <description>De nieuwste Galaxy review.</description>
    <pubDate>Sun, 08 Jun 2026 06:34:46 +0200</pubDate>
    <guid isPermaLink="false">article-123</guid>
  </item>
  <item>
    <title>KPN abonnement nieuws</title>
    <link>https://www.nu.nl/tech/124/kpn.html</link>
    <description>Telecom nieuws zonder relevante merken.</description>
    <pubDate>Sun, 08 Jun 2026 06:00:00 +0200</pubDate>
    <guid isPermaLink="false">article-124</guid>
  </item>
</channel></rss>
"""


def test_nu_nl_parse_extracts_items():
    c = NuNLCrawler()
    items = c._parse(NU_NL_SAMPLE)
    assert len(items) == 2
    titles = [it.content for it in items]
    assert any("Samsung Galaxy S26" in t for t in titles)
    assert items[0].country_code == "NL"
    assert items[0].meta["source"] == "nu_nl_rss"


def test_nu_nl_keyword_filter_keeps_galaxy():
    c = NuNLCrawler()
    items = c._parse(NU_NL_SAMPLE)
    kept = [v for v in items if c._is_galaxy_related(v)]
    assert len(kept) == 1
    assert "Galaxy" in kept[0].content


def test_nu_nl_external_id_stable():
    c = NuNLCrawler()
    items1 = c._parse(NU_NL_SAMPLE)
    items2 = c._parse(NU_NL_SAMPLE)
    assert sorted(i.external_id for i in items1) == sorted(i.external_id for i in items2)
    assert all(len(i.external_id) == 16 for i in items1)


def test_nu_nl_date_parsed_utc():
    c = NuNLCrawler()
    items = c._parse(NU_NL_SAMPLE)
    dt = items[0].published_at
    assert dt is not None
    # +0200 → UTC 04:34:46
    assert dt == datetime(2026, 6, 8, 4, 34, 46, tzinfo=timezone.utc)


# --------------------------- iphoneincanada --------------------------------

IPC_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
  <title>iPhone in Canada</title>
  <item>
    <title>Samsung Galaxy S26 Ultra hands-on Canada</title>
    <link>https://www.iphoneincanada.ca/2026/06/07/galaxy-s26-hands-on/</link>
    <description>Quick look at the new Galaxy.</description>
    <content:encoded><![CDATA[<p>Galaxy S26 Ultra brings new <b>One UI</b>.</p>]]></content:encoded>
    <dc:creator>Gary Ng</dc:creator>
    <pubDate>Sat, 07 Jun 2026 19:39:21 +0000</pubDate>
    <guid isPermaLink="false">https://www.iphoneincanada.ca/?p=999</guid>
  </item>
  <item>
    <title>Apple iPhone 18 leaks</title>
    <link>https://www.iphoneincanada.ca/2026/06/07/iphone-18-leak/</link>
    <description>Pure Apple coverage.</description>
    <dc:creator>Editor</dc:creator>
    <pubDate>Sat, 07 Jun 2026 18:00:00 +0000</pubDate>
    <guid isPermaLink="false">https://www.iphoneincanada.ca/?p=998</guid>
  </item>
</channel></rss>
"""


def test_iphoneincanada_parse_extracts_items():
    c = IPhoneInCanadaCrawler()
    items = c._parse(IPC_SAMPLE)
    assert len(items) == 2
    assert items[0].country_code == "CA"
    assert items[0].meta["source"] == "iphoneincanada_rss"
    # content:encoded merged in
    assert "One UI" in items[0].content
    assert items[0].author_name == "Gary Ng"


def test_iphoneincanada_keyword_filter():
    c = IPhoneInCanadaCrawler()
    items = c._parse(IPC_SAMPLE)
    kept = [v for v in items if c._is_galaxy_related(v)]
    assert len(kept) == 1
    assert "Galaxy" in kept[0].content


def test_iphoneincanada_external_id_stable():
    c = IPhoneInCanadaCrawler()
    items1 = c._parse(IPC_SAMPLE)
    items2 = c._parse(IPC_SAMPLE)
    assert sorted(i.external_id for i in items1) == sorted(i.external_id for i in items2)


# --------------------------- sspai --------------------------------

SSPAI_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>少数派</title>
  <item>
    <title>三星 Galaxy S26 Ultra 体验</title>
    <link>https://sspai.com/post/110001</link>
    <description>&lt;p&gt;三星新机评测。&lt;/p&gt;</description>
    <author>copperfield</author>
    <pubDate>Sun, 08 Jun 2026 15:00:00 +0800</pubDate>
  </item>
  <item>
    <title>Apple Vision Pro 2 评测</title>
    <link>https://sspai.com/post/110002</link>
    <description>苹果新品.</description>
    <author>editor</author>
    <pubDate>Sun, 08 Jun 2026 10:00:00 +0800</pubDate>
  </item>
  <item>
    <title>Galaxy Z Fold 7 拆机</title>
    <link>https://sspai.com/post/110003</link>
    <description>Fold teardown.</description>
    <author>tech</author>
    <pubDate>Sun, 08 Jun 2026 09:00:00 +0800</pubDate>
  </item>
</channel></rss>
"""


def test_sspai_parse_extracts_items():
    c = SspaiCrawler()
    items = c._parse(SSPAI_SAMPLE)
    assert len(items) == 3
    assert items[0].country_code == "CN"
    assert items[0].meta["source"] == "sspai_rss"


def test_sspai_keyword_filter_chinese_and_english():
    """三星(중국 표기) 와 Galaxy(영문) 모두 매칭되어야 함."""
    c = SspaiCrawler()
    items = c._parse(SSPAI_SAMPLE)
    kept = [v for v in items if c._is_galaxy_related(v)]
    assert len(kept) == 2  # 三星 + Galaxy Z Fold
    contents = [v.content for v in kept]
    assert any("三星" in c2 for c2 in contents)
    assert any("Galaxy Z Fold" in c2 for c2 in contents)


def test_sspai_keyword_negative():
    c = SspaiCrawler()
    v = RawVOC(external_id="x", content="Apple Vision Pro 2 评测", source_url="u")
    assert not c._is_galaxy_related(v)


def test_sspai_external_id_stable():
    c = SspaiCrawler()
    items1 = c._parse(SSPAI_SAMPLE)
    items2 = c._parse(SSPAI_SAMPLE)
    assert sorted(i.external_id for i in items1) == sorted(i.external_id for i in items2)


def test_sspai_date_parsed_to_utc():
    c = SspaiCrawler()
    items = c._parse(SSPAI_SAMPLE)
    dt = items[0].published_at
    assert dt is not None
    # +0800 → UTC = 07:00:00
    assert dt == datetime(2026, 6, 8, 7, 0, 0, tzinfo=timezone.utc)


# --------------------------- malformed XML safety --------------------------------

def test_all_three_handle_malformed_xml():
    assert NuNLCrawler()._parse("<not xml") == []
    assert IPhoneInCanadaCrawler()._parse("<not xml") == []
    assert SspaiCrawler()._parse("<not xml") == []
