"""
9to5Google 크롤러 — httpx + BeautifulSoup
9to5google.com 기사 댓글에서 Samsung Galaxy 관련 VOC 수집
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://9to5google.com"

# Galaxy 관련 태그/카테고리 URL (tag/ → guides/ 리디렉션 처리됨)
FEED_URLS = [
    f"{BASE_URL}/guides/samsung-galaxy/",
    f"{BASE_URL}/guides/samsung-galaxy-s26/",
    f"{BASE_URL}/guides/samsung-galaxy-fold/",
    f"{BASE_URL}/guides/samsung/",
]

GALAXY_KEYWORDS = [
    "Galaxy", "Samsung", "S25", "Fold", "Flip", "Buds", "Watch", "Ring",
]


# @lat: NineTo5GoogleCrawler — [[crawler#Platform Strategy]] 참조.
class NineTo5GoogleCrawler(BaseCrawler):
    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    def __init__(self, platform_code: str = "9to5google", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for feed_url in FEED_URLS:
                try:
                    articles = await self._fetch_article_list(client, feed_url)
                    raw_vocs.extend(articles)
                    logger.info(f"  9to5Google [{feed_url.split('/')[-2]}]: {len(articles)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  9to5Google [{feed_url}] 실패: {e}")

        # 중복 제거 (같은 article이 여러 태그에 나타날 수 있음)
        seen = set()
        unique = []
        for voc in raw_vocs:
            if voc.external_id not in seen:
                seen.add(voc.external_id)
                unique.append(voc)

        logger.info(f"9to5Google 수집 완료: {len(unique)}건 (중복 제거 후)")
        return unique

    async def _fetch_article_list(self, client: httpx.AsyncClient, feed_url: str) -> List[RawVOC]:
        resp = await client.get(feed_url)
        resp.raise_for_status()
        return self._parse_article_list(resp.text)

    def _parse_article_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # 기사 목록 — article 태그 또는 리스트 아이템
        article_els = (
            soup.select("article.river-item")
            or soup.select("article")
            or soup.select(".post-block")
        )

        for el in article_els[:20]:
            try:
                # 타이틀 + 링크
                title_el = (
                    el.select_one("a.article__title-link")
                    or el.select_one("h2 a")
                    or el.select_one("h3 a")
                    or el.select_one(".post-block__title a")
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                article_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 날짜: <span class="meta__post-date">Apr 15 2026 - 9:01 am PT</span>
                date_el = el.select_one(".meta__post-date") or el.select_one("time")
                published_at = None
                if date_el:
                    dt_attr = date_el.get("datetime", "")
                    if dt_attr:
                        published_at = self._parse_iso_date(dt_attr)
                    else:
                        published_at = self._parse_9to5_date(date_el.get_text(strip=True))

                # 저자
                author_el = (
                    el.select_one(".author__link a")
                    or el.select_one("[rel=author]")
                    or el.select_one(".post-block__meta a")
                )
                author = author_el.get_text(strip=True) if author_el else "9to5Google"

                # 요약 (있으면 title + summary 합치기)
                excerpt_el = el.select_one(".excerpt") or el.select_one(".post-block__body")
                excerpt = excerpt_el.get_text(strip=True) if excerpt_el else ""
                content = f"{title}\n{excerpt}".strip() if excerpt else title

                # Galaxy 관련 여부 확인
                if not any(kw.lower() in content.lower() for kw in GALAXY_KEYWORDS):
                    continue

                uid = hashlib.md5(article_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=content,
                    source_url=article_url,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                ))
            except Exception as e:
                logger.debug(f"9to5Google 기사 파싱 실패: {e}")

        return results

    def _parse_9to5_date(self, text: str):
        """'Apr 15 2026 - 9:01 am PT' 또는 'May 15 2026 - 11:30 am PT' 파싱"""
        text = text.strip()
        # 날짜 부분만 추출: "Apr 15 2026"
        m = re.match(r"([A-Za-z]+ \d{1,2} \d{4})", text)
        if m:
            try:
                return datetime.strptime(m.group(1), "%b %d %Y").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        return None

    def _parse_iso_date(self, dt_str: str):
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            pass
        return None
