"""
Tecnoblog 크롤러 — httpx + RSS(전문 본문) 기반, Cloudflare 우회

tecnoblog.net (브라질 최대 테크 미디어, PT-BR, WordPress) 의 Samsung/Galaxy
관련 기사 본문 수집.

전략
  - 메인 카테고리/태그 HTML 페이지는 Cloudflare JS 챌린지로 403.
    상세 기사 HTML 도 403. WP-JSON, sitemap 도 403.
  - 단, /feed/ (메인 RSS) 와 페이지네이션 /feed/?paged=N 은 200 으로 응답:
    50건 × 4페이지 = 약 200 후보. 본문은 <content:encoded> 에 전문 포함.
  - 댓글은 페이지별 슬러그-feed/ 가 다른 글로 redirect 되므로 채집 불가.
    → 본문 한 건 = 한 VOC. <slash:comments> 카운트만 메타로 보존.
  - 시간: RSS pubDate (RFC822 +0000 UTC 표준화 됨) → 그대로 UTC 변환.
    혹 naive 가 들어오면 BRT(UTC-3) 가정.
  - Galaxy/Samsung 키워드 필터 — 제목/카테고리/본문 어느 한 곳이라도 매칭.
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

BASE_URL = "https://tecnoblog.net"
RSS_URL = f"{BASE_URL}/feed/"

# RSS 페이지네이션 — paged=1..LIST_PAGES (각 50건)
LIST_PAGES = 12
# 최종 처리 캡
MAX_POSTS = 150

# 브라질 표준시 (BRT, UTC-3). 브라질은 2019년 이후 DST 없음.
BRT = timezone(timedelta(hours=-3))

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 포르투갈어 환경에서도 Galaxy / Samsung 표기는 영문 동일
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class TecnoblogCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "tecnoblog", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "pt-BR,pt;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  Tecnoblog RSS page={page}: 0건 → 종료")
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
                        f"  Tecnoblog RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Tecnoblog page={page} 실패: {e}")

        # 최신순 정렬 → 상위 MAX_POSTS
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"Tecnoblog 수집 완료: {len(result)}건 (후보 {len(items)})"
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
            logger.debug(f"Tecnoblog feed page={page} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Tecnoblog RSS 파싱 실패: {e}")
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

                # WordPress GUID 우선 사용 (post_id 추출용), 없으면 link
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

                # 본문 길이 컷 (4000자)
                if len(body) > 4000:
                    body = body[:4000]

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 20:
                    # 너무 빈약한 글 (이미지만 있는 등) 컷
                    continue

                # 발행일 — pubDate (RFC822). RSS 가 +0000 UTC 로 응답하므로 안전.
                published_at = self._parse_rss_date(item.findtext("pubDate") or "")

                # 저자 — dc:creator
                author = item.findtext("dc:creator", default="", namespaces=NS).strip() or None

                # 댓글 수 — slash:comments
                comments_count = 0
                try:
                    comments_count = int(
                        (item.findtext("slash:comments", default="0", namespaces=NS) or "0").strip()
                    )
                except (TypeError, ValueError):
                    comments_count = 0

                # 카테고리 리스트
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
                    country_code="BR",
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"Tecnoblog item 파싱 실패: {e}")

        return results

    # --- helpers ---

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """WordPress GUID 'https://tecnoblog.net/?p=12345' 또는
        'https://tecnoblog.net/?post_type=achados&p=12345' 에서 post id 추출."""
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
        # 스크립트/스타일 블록 통째로 제거
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        # 태그 제거 + 공백 정리
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        # 본문 / 제목 키워드 매칭
        if any(kw in text for kw in GALAXY_KEYWORDS):
            return True
        # 카테고리 매칭 (메타에 보존됨)
        cats = " ".join(voc.meta.get("categories") or []).lower()
        return any(kw in cats for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Fri, 29 May 2026 21:38:01 +0000' → UTC.
        naive 일 경우 BRT(UTC-3) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
