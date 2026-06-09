"""
MySmartPrice 크롤러 — Google News RSS 우회 (Cloudflare WAF 차단)

mysmartprice.com (인도 전자제품 가격비교/리뷰/뉴스, /gear 섹션이 IT 뉴스/리뷰)
은 Cloudflare WAF + JA3/JA4 fingerprint 차단으로 httpx/curl/WebFetch 모두 403.
Firefox UA, mobile UA, Googlebot UA, Referer 추가, /gear/feed/?post_type=post,
sitemap.xml, wp-json/wp/v2/* 모두 차단 (robots.txt 만 200).
다만 /gear/feed/ (코멘트 피드 - 빈 내용) 만 우연 통과.

해결책: Google News RSS (news.google.com/rss/search?q=site:mysmartprice.com+...)
를 데이터 소스로 사용. 다양한 Samsung/Galaxy 키워드로 fan-out 검색 → 가장 많은
신규 후보 확보. 측정상 9개 검색어 합치면 ~350+ 고유 기사 인덱스됨.

전략 (hwupgrade/gsmchoice 의 GN-RSS 패턴 차용)
  - Google News RSS 9개 키워드 쿼리 병합:
      samsung galaxy / galaxy s25 / s26 / samsung review / galaxy buds /
      galaxy watch / one ui / samsung fold / samsung flip
      → guid 기준 dedupe
  - title 끝의 ' - MySmartPrice' / ' - MySmartPrice Gear' 시그니처 제거
  - 키워드 필터 (title 기준) — Samsung/Galaxy/One UI/Exynos/Bixby/Galaxy 시리즈
  - source_url: Google News 캡슐 URL 사용 (원본 mysmartprice.com URL 은 캡슐 안
    base64 인코딩되어 있고 직접 디코딩이 비안정. 캡슐 URL 도 안정적 식별자.)
  - published_at: pubDate(GMT) → UTC (Google News 가 항상 GMT 정규화 제공)
  - external_id: GN guid 의 md5 16자 (DB 인덱스 일관)
  - 댓글: Cloudflare 가 모든 본문 fetch 차단 → 수집 불가. 본문은 제목만 사용.

회고
  - 한국 NCSI 와 비슷한 강도의 인도 Cloudflare 보안. 다른 동급 인도 사이트
    (gadgets360) 가 RSS 통과하는 것과 대조적.
  - robots.txt 에 /gear/ Allow 가 있지만 실제로는 cloudflare 가 차단.
  - 향후 cf-clearance 캐싱 / curl-cffi(JA3 spoof) 인프라 도입 시 본문 fetch
    재시도할 수 있음.
"""
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

# Google News RSS endpoint — site:mysmartprice.com 인도 로케일
GN_RSS = (
    "https://news.google.com/rss/search?"
    "q=site:mysmartprice.com+{kw}&hl=en-IN&gl=IN&ceid=IN:en"
)

# fan-out 검색 키워드 (중복은 guid dedupe 로 제거됨)
SEARCH_TERMS = [
    "samsung galaxy",
    "galaxy s25",
    "galaxy s26",
    "samsung review",
    "galaxy buds",
    "galaxy watch",
    "one ui",
    "samsung fold",
    "samsung flip",
]

# 결과 캡 (LIST_PAGES=12 와 함께 안정성 보장; GN 은 페이지당 ~100건이라
# 사실상 키워드 수 × 100 이 상한)
LIST_PAGES = 12          # 향후 GN 페이지네이션 지원 시 활용 (현재는 1페이지/쿼리)
MAX_POSTS = 150

# Galaxy/Samsung 키워드 필터 (영문, mysmartprice 는 영문 사이트)
# 일반어 단독(tab/watch/ring/fold) 은 false positive 회피 위해 제외.
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# title 끝의 source 시그니처 (예: " - MySmartPrice", " - MySmartPrice Gear")
TITLE_SUFFIX_RE = re.compile(
    r"\s*[-–—|]\s*MySmartPrice(?:\s+Gear)?\s*$", re.I
)

# IST (UTC+5:30) — pubDate 가 naive 인 경우 폴백 (Google News 는 항상 GMT 제공)
IST = timezone(timedelta(hours=5, minutes=30))


class MySmartPriceCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "mysmartprice", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # Chrome UA → 일반 사용자 흉내. Firefox UA 폴백은 GN 차단 시점에만
            # 의미 있고 GN 은 사실상 항상 200 이라 분기 안 함.
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "en-IN,en-US;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )

            for kw in SEARCH_TERMS:
                try:
                    posts = await self._fetch_gn_feed(client, kw)
                    items.extend(posts)
                    logger.info(
                        f"  MySmartPrice GN[{kw}]: {len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  MySmartPrice GN[{kw}] 실패: {e}")

        # dedupe by external_id (Google News guid 의 md5)
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.external_id in seen:
                continue
            seen.add(it.external_id)
            unique.append(it)

        # Galaxy/Samsung 키워드 필터 (제목 기준 — 본문 없음)
        filtered = [v for v in unique if self._is_galaxy_related(v)]

        # 최신순 정렬 → 상위 MAX_POSTS
        filtered.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = filtered[:MAX_POSTS]
        logger.info(
            f"MySmartPrice 수집 완료: {len(result)}건 "
            f"(GN 후보 {len(items)} → 고유 {len(unique)} → Galaxy {len(filtered)})"
        )
        return result

    # ---------- fetchers ----------

    async def _fetch_gn_feed(
        self, client: httpx.AsyncClient, keyword: str
    ) -> List[RawVOC]:
        # GN 은 ' ' → '+' 자동 인코딩, 공백 포함 키워드도 정상 동작
        url = GN_RSS.format(kw=keyword.replace(" ", "+"))
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_gn_feed(resp.text)

    # ---------- parsers ----------

    def _parse_gn_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"MySmartPrice GN RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                raw_title = (item.findtext("title") or "").strip()
                if not raw_title:
                    continue
                # trailing " - MySmartPrice" 제거
                title = TITLE_SUFFIX_RE.sub("", raw_title).strip()
                if not title:
                    continue

                link = (item.findtext("link") or "").strip()
                if not link:
                    continue

                guid_raw = (item.findtext("guid") or link).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                # external_id: GN guid 가 안정적 (base64-like). md5 16자 축약.
                external_id = hashlib.md5(
                    f"mysmartprice#{guid_raw}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=title,
                    source_url=link,
                    author_name=None,
                    published_at=published_at,
                    country_code="IN",
                    meta={
                        "guid": guid_raw,
                        "source": "google_news_rss",
                        "publisher": "MySmartPrice",
                    },
                ))
            except Exception as e:
                logger.debug(f"MySmartPrice item 파싱 실패: {e}")
        return results

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 10:13:34 GMT' → UTC datetime.

        Google News 는 pubDate 를 항상 GMT 로 정규화 제공. naive 인 경우
        (미발생 예상) IST(+05:30) 폴백 후 UTC 변환.
        """
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
