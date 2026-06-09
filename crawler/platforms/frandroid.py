"""
Frandroid 크롤러 — httpx + WordPress RSS, Samsung 카테고리 직타

frandroid.com (프랑스 안드로이드 전문 매체, fr-FR, WordPress) 의 Samsung/Galaxy
관련 기사 본문 수집.

전략
  - 메인 카테고리 HTML (/marques/samsung) 은 200 으로 응답하지만 본문 추출이
    무겁다. WordPress RSS (/marques/samsung/feed) 가 Samsung 으로 이미 필터된
    15건/페이지 × ?paged=N 으로 안정적이며 <content:encoded> 본문 전문 포함.
  - 댓글은 Disqus (JS 위젯) 로 로드되며 httpx 로는 접근 불가.
    상세 페이지의 /feed 는 본문 HTML 으로 redirect 됨. → 댓글 채집 보류.
    한 게시글 = 한 VOC, <slash:comments> 만 메타로 보존.
  - 시간: RSS pubDate 가 +0000 UTC. naive 가 들어오면 CEST(UTC+2, 5월 기준)
    가정 후 UTC 변환. (프랑스는 봄~가을 CEST, 가을~봄 CET. 보수적으로 CEST.)
  - 키워드 필터: 카테고리에 'Samsung' 이 들어간 시점에서 Samsung 관련.
    추가로 본문/제목에 galaxy/samsung 검사 (브랜드 외 무관 글 컷).
"""
import hashlib
import html as html_lib
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

BASE_URL = "https://www.frandroid.com"
RSS_URL = f"{BASE_URL}/marques/samsung/feed"

# Samsung 카테고리 RSS 페이지네이션 — 15건/페이지
LIST_PAGES = 12
MAX_POSTS = 150

# 프랑스 표준시 — 5월 (DST 적용중) CEST = UTC+2
# 겨울철은 CET = UTC+1. RSS 가 +0000 으로 응답하면 그대로 사용.
CEST = timezone(timedelta(hours=2))

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 프랑스어 환경에서도 Galaxy / Samsung 표기는 영문 동일.
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class FrandroidCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "frandroid", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "fr-FR,fr;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  Frandroid RSS page={page}: 0건 → 종료")
                        break

                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    new_count = 0
                    for p in filtered:
                        if p.source_url in seen_links:
                            continue
                        seen_links.add(p.source_url)
                        items.append(p)
                        new_count += 1
                    logger.info(
                        f"  Frandroid RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Frandroid page={page} 실패: {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"Frandroid 수집 완료: {len(result)}건 (후보 {len(items)})"
        )
        return result

    async def _fetch_feed_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[RawVOC]:
        url = RSS_URL if page == 1 else f"{RSS_URL}?paged={page}"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"Frandroid feed page={page} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Frandroid RSS 파싱 실패: {e}")
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        results: List[RawVOC] = []
        for item in channel.findall("item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                # WordPress GUID — Frandroid 의 guid 는 슬러그 형태 (post id 없음)
                # → post id 는 URL 슬러그 앞 숫자에서 추출 (예: /3118589_...)
                post_id = self._extract_post_id(link) or hashlib.md5(
                    link.encode()
                ).hexdigest()[:12]

                content_enc = item.findtext("content:encoded", default="", namespaces=NS)
                body = self._strip_html(content_enc)
                if not body:
                    desc_raw = item.findtext("description") or ""
                    body = self._strip_html(desc_raw)

                if len(body) > 4000:
                    body = body[:4000]

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 20:
                    continue

                published_at = self._parse_rss_date(item.findtext("pubDate") or "")
                author = item.findtext("dc:creator", default="", namespaces=NS).strip() or None

                comments_count = 0
                try:
                    comments_count = int(
                        (item.findtext("slash:comments", default="0", namespaces=NS) or "0").strip()
                    )
                except (TypeError, ValueError):
                    comments_count = 0

                cats = [
                    (c.text or "").strip()
                    for c in item.findall("category")
                    if c.text
                ]

                external_id = hashlib.md5(
                    f"{link}#{post_id}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    comments_count=comments_count,
                    country_code="FR",
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"Frandroid item 파싱 실패: {e}")

        return results

    @staticmethod
    def _extract_post_id(link: str) -> Optional[str]:
        """Frandroid URL 패턴: /{section}/{post_id}_{slug}
        예: /marques/samsung/3118589_galaxy-watch-9-classic-... → '3118589'."""
        if not link:
            return None
        m = re.search(r"/(\d{6,})_", link)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        if any(kw in text for kw in GALAXY_KEYWORDS):
            return True
        cats = " ".join(voc.meta.get("categories") or []).lower()
        return any(kw in cats for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Fri, 29 May 2026 12:54:53 +0000' → UTC.
        naive 일 경우 CEST(UTC+2) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CEST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
