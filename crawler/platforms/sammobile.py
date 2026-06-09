"""
SamMobile 크롤러 — httpx + RSS(전문 본문) 기반

sammobile.com (Samsung 전문 글로벌 영문 뉴스 사이트, WordPress) 의 Samsung/Galaxy
뉴스/리뷰 본문 수집. 사실상 사이트 전체가 Samsung 콘텐츠이므로 키워드 필터는
관대하게 적용한다.

전략
  - 메인 RSS /feed/ 와 페이지네이션 /feed/?paged=N (1..LIST_PAGES, 페이지당 10건).
  - 응답이 Cloudflare 통과 → 본문은 <content:encoded> 에 전문 포함.
  - 댓글은 본문 HTML 에서 식별되지 않음 (Disqus/WP-Comments 마커 없음).
    → 본문 한 건 = 한 VOC. <slash:comments> 도 부재.
  - 시간: RSS pubDate (RFC822 +0000 UTC 표준화 됨) → 그대로 UTC 변환.
    naive 가 들어오면 UTC 가정 (GLOBAL 사이트).
  - Galaxy/Samsung/One UI 등 영문 키워드로 관대 매칭 — 카테고리 매칭도 허용.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sammobile.com"
RSS_URL = f"{BASE_URL}/feed/"

# RSS 페이지네이션 — paged=1..LIST_PAGES (각 10건). 12 × 10 = 120 후보
LIST_PAGES = 12
MAX_POSTS = 150

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 영문 환경 — 사이트 자체가 Samsung 전문이라 보수적으로 매칭
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
    "tizen", "knox", "dex",
]


class SamMobileCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "sammobile", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  SamMobile RSS page={page}: 0건 → 종료")
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
                        f"  SamMobile RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  SamMobile page={page} 실패: {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"SamMobile 수집 완료: {len(result)}건 (후보 {len(items)})"
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
            logger.debug(f"SamMobile feed page={page} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"SamMobile RSS 파싱 실패: {e}")
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

                guid = (item.findtext("guid") or "").strip()
                post_id = self._extract_post_id(guid) or hashlib.md5(
                    link.encode()
                ).hexdigest()[:12]

                # 본문 — content:encoded 전문, 없으면 description
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
                    country_code=None,  # GLOBAL
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"SamMobile item 파싱 실패: {e}")

        return results

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """WordPress GUID 'https://www.sammobile.com/?p=12345' 에서 post id 추출."""
        if not guid:
            return None
        m = re.search(r"[?&]p=(\d+)", guid)
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
        """RFC822 'Mon, 01 Jun 2026 15:29:09 +0000' → UTC.
        naive 일 경우 UTC 가정 (GLOBAL)."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
