"""
DroidSans 크롤러 — httpx + WordPress RSS (sammobile 패턴 재사용)

droidsans.com (태국 Android/모바일 전문 매체, WordPress) 의 Galaxy/Samsung 뉴스 수집.

전략
  - 메인 RSS /feed/ 와 페이지네이션 /feed/?paged=N.
  - 응답 200 OK (Cloudflare 없음), content:encoded 전문 포함.
  - 키워드 필터: 영문 galaxy/samsung + 태국어 ซัมซุง/กาแล็คซี่.
  - country_code="TH".

R5 보수 정책 (Stage 5B)
  - 신규 사이트 첫 수집: LIST_PAGES=2 만 (OOM 안전).
  - 본문 4000자 cap, MAX_POSTS=120 cap.
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

BASE_URL = "https://droidsans.com"
RSS_URL = f"{BASE_URL}/feed/"

# R5 첫 수집 보수: 2 페이지만 (OOM 안전). 안정화 후 증가.
LIST_PAGES = 2
MAX_POSTS = 120

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# Galaxy/Samsung 키워드 — 영문 + 태국어
GALAXY_KEYWORD_RE = re.compile(
    r"("
    r"galaxy|samsung"
    r"|one ?ui|oneui|bixby|exynos|tizen|knox"
    r"|s2[3-9]|s30"
    r"|z ?fold|z ?flip"
    r"|ซัมซุง|กาแล็ก|กาแล็คซี่|กาแลคซี่"
    r")",
    re.I,
)


class DroidSansCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "droidsans", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "th-TH,th;q=0.9,en;q=0.7"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  DroidSans RSS page={page}: 0건 → 종료")
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
                        f"  DroidSans RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  DroidSans page={page} 실패: {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"DroidSans 수집 완료: {len(result)}건 (후보 {len(items)})"
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
            logger.debug(f"DroidSans feed page={page} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"DroidSans RSS 파싱 실패: {e}")
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
                    f"droidsans#{link}#{post_id}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    comments_count=comments_count,
                    country_code="TH",
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"DroidSans item 파싱 실패: {e}")

        return results

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """WordPress GUID 'https://droidsans.com/?p=12345' 에서 post id 추출."""
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
        text = voc.content or ""
        if not text.strip():
            return False
        if GALAXY_KEYWORD_RE.search(text):
            return True
        cats = " ".join(voc.meta.get("categories") or [])
        return bool(GALAXY_KEYWORD_RE.search(cats))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 → UTC. naive 면 +07 (태국) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                ict = timezone(timedelta(hours=7))
                dt = dt.replace(tzinfo=ict)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
