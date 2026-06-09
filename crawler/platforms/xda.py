"""
XDA Developers 크롤러 — httpx + BeautifulSoup (+ RSS fallback)

xda-developers.com **news tag 인덱스 (`/tag/<slug>/`)** 를 정식 수집 경로로 사용.
포럼 (`/forum/`) 은 게이트웨이 차단으로 비활성. Harvest 5 V1 에서 news_tag
정식 채택 — 9 개 태그 인덱스를 폭넓게 수집한 뒤 Galaxy 키워드 필터.

태그 인덱스가 일시적 차단 (예: 5xx, cf challenge) 일 때를 대비해
`/feed/tag/samsung/` RSS fallback 을 함께 시도하여 신뢰성을 높인다.
"""
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.xda-developers.com"

# Harvest 5 V1: XDA news_tag 정식 — 4 → 9 카테고리 확장.
# Discovery 단계에서 모두 200 OK 확인.
XDA_FEEDS = [
    f"{BASE_URL}/tag/samsung-galaxy/",
    f"{BASE_URL}/tag/samsung-galaxy-fold/",
    f"{BASE_URL}/tag/one-ui/",
    f"{BASE_URL}/tag/samsung/",
    # H5 V1 추가
    f"{BASE_URL}/tag/samsung-galaxy-z-flip/",
    f"{BASE_URL}/tag/samsung-galaxy-watch/",
    f"{BASE_URL}/tag/samsung-galaxy-buds/",
    f"{BASE_URL}/tag/samsung-galaxy-tab/",
    f"{BASE_URL}/tag/samsung-galaxy-a/",
]

# RSS fallback — HTML 인덱스 차단 시 보강. samsung 단일 tag 만 사용
# (전 태그 RSS 미존재). 검증 200 OK + items 10건/회.
XDA_RSS_FALLBACK = f"{BASE_URL}/feed/tag/samsung/"

GALAXY_KEYWORDS = [
    "Galaxy", "Samsung", "S25", "S26", "Fold", "Flip", "Buds", "Watch", "One UI",
]


# @lat: XDACrawler — [[crawler#Platform Strategy]] 참조.
class XDACrawler(BaseCrawler):
    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    def __init__(self, platform_code: str = "xda", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for feed_url in XDA_FEEDS:
                try:
                    articles = await self._fetch_article_list(client, feed_url)
                    raw_vocs.extend(articles)
                    tag = feed_url.rstrip("/").split("/")[-1]
                    logger.info(f"  XDA [{tag}]: {len(articles)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  XDA [{feed_url}] 실패: {e}")

            # RSS fallback — HTML 인덱스 전부 실패해도 최소 10건은 보장.
            # 항상 시도 (정상 path 와 dedup 으로 머지). H5 V1.
            try:
                rss_items = await self._fetch_rss_fallback(client, XDA_RSS_FALLBACK)
                raw_vocs.extend(rss_items)
                logger.info(f"  XDA [RSS:samsung]: {len(rss_items)}건")
            except Exception as e:
                logger.warning(f"  XDA RSS fallback 실패: {e}")

        # 중복 제거
        seen: set = set()
        unique = []
        for voc in raw_vocs:
            if voc.external_id not in seen:
                seen.add(voc.external_id)
                unique.append(voc)

        logger.info(f"XDA 수집 완료: {len(unique)}건 (중복 제거 후)")
        return unique

    async def _fetch_article_list(self, client: httpx.AsyncClient, feed_url: str) -> List[RawVOC]:
        resp = await client.get(feed_url)
        resp.raise_for_status()
        return self._parse_article_list(resp.text)

    async def _fetch_rss_fallback(
        self, client: httpx.AsyncClient, rss_url: str
    ) -> List[RawVOC]:
        """XDA `/feed/tag/samsung/` RSS — HTML 인덱스 일시 차단 시 보강."""
        resp = await client.get(rss_url)
        resp.raise_for_status()
        return self._parse_rss_feed(resp.text)

    def _parse_rss_feed(self, xml_text: str) -> List[RawVOC]:
        """XDA WordPress RSS 2.0 파싱 — 표준 item/title/link/pubDate/dc:creator."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"XDA RSS 파싱 실패: {e}")
            return []

        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue
                # Galaxy/Samsung 키워드 필터 (HTML 경로와 동일 정책)
                if not any(kw.lower() in title.lower() for kw in GALAXY_KEYWORDS):
                    continue

                author = (item.findtext("dc:creator", namespaces=ns) or "XDA").strip()
                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                uid = hashlib.md5(link.encode()).hexdigest()[:16]
                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                ))
            except Exception as e:
                logger.debug(f"XDA RSS item 파싱 실패: {e}")
        return results

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 → UTC. XDA RSS 는 pubDate 를 GMT 로 제공."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_article_list(self, html: str) -> List[RawVOC]:
        import base64
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for card in soup.select("div.display-card.article")[:20]:
            try:
                title_el = (
                    card.select_one("h5.display-card-title a")
                    or card.select_one("h2 a")
                    or card.select_one("h3 a")
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                article_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 날짜: base64 인코딩된 data-b64-ts 속성 (예: "Apr 2, 2025")
                date_el = card.select_one(".display-card-date")
                published_at = None
                if date_el:
                    b64 = date_el.get("data-b64-ts", "")
                    if b64:
                        try:
                            date_str = base64.b64decode(b64).decode()
                            published_at = self._parse_xda_date(date_str)
                        except Exception:
                            pass

                # 저자
                author_el = card.select_one("a.article-author") or card.select_one("[rel=author]")
                author = author_el.get_text(strip=True) if author_el else "XDA"

                # Galaxy 관련 여부 확인
                if not any(kw.lower() in title.lower() for kw in GALAXY_KEYWORDS):
                    continue

                uid = hashlib.md5(article_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=article_url,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                ))
            except Exception as e:
                logger.debug(f"XDA 기사 파싱 실패: {e}")

        return results

    def _parse_xda_date(self, text: str):
        """'Apr 2, 2025' 또는 'May 15, 2026' 형식 파싱"""
        try:
            return datetime.strptime(text.strip(), "%b %d, %Y").replace(tzinfo=timezone.utc)
        except Exception:
            pass
        return None

    def _parse_iso_date(self, dt_str: str):
        """ISO 8601 형식 파싱"""
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            pass
        return None
