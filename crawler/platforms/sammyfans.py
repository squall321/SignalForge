"""
SammyFans 크롤러 — httpx + RSS 기반 (Cloudflare 우회)

sammyfans.com (Samsung/Galaxy 전문 영문 글로벌 뉴스, WordPress) 의 본문 수집.
사이트 자체가 Samsung 전용이라 키워드 필터는 관대하게 적용.

전략
  - 메인 RSS /feed/ + WP 표준 페이지네이션 /feed/?paged=N (페이지당 10건).
  - 기본 Chrome UA 는 Cloudflare 403 → Safari UA 로 폴백, 그래도 403 이면 Firefox UA.
  - WP REST (/wp-json/wp/v2/posts) 와 카테고리 RSS 는 차단되어 사용 안 함.
  - 본문은 RSS <content:encoded> 전문 사용.
  - 댓글은 글 단위 /feed/ 엔드포인트(WordPress Comment RSS)에서 끌어오되,
    응답이 비어있거나(채널만 있고 item 없음) 빈 채널이면 0개로 처리.
  - 시간: pubDate(RFC822, +0000) → UTC. naive 들어오면 UTC 가정.
  - external_id: md5(link + '#post')[:16] / md5(link + '#c' + comment_id)[:16]
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sammyfans.com"
RSS_URL = f"{BASE_URL}/feed/"

# 페이지네이션 — 12페이지 × 10건 ≈ 120 후보 → MAX 150 컷
LIST_PAGES = 12
MAX_POSTS = 150

# Cloudflare 우회용 UA 폴백 체인 (Safari → Firefox)
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
)
FIREFOX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
)

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 영문 키워드 — 사이트 전체가 Samsung 이라 보수적 매칭
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
    "tizen", "knox", "dex",
]

# WordPress 링크에서 post slug/날짜 추출용
POST_URL_RE = re.compile(
    r"^https?://(?:www\.)?sammyfans\.com/(\d{4})/(\d{2})/(\d{2})/([^/?#]+)/?$"
)


class SammyFansCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "sammyfans", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            # Safari UA 로 시작 (Cloudflare 우회 가장 안정)
            client.headers["User-Agent"] = SAFARI_UA
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            # brotli 는 httpx 가 항상 디코딩 못함 → gzip/deflate 만 요청
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  SammyFans RSS page={page}: 0건 → 종료")
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
                        f"  SammyFans RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  SammyFans page={page} 실패: {e}")

            # 정렬 후 상위 MAX_POSTS 만 댓글 fetch 시도
            items.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            items = items[:MAX_POSTS]

            # 댓글 수집 (WordPress 글별 /feed/) — 비어있어도 무시
            all_items: List[RawVOC] = []
            for post in items:
                all_items.append(post)
                try:
                    comments = await self._fetch_comments(
                        client, post.source_url, post.meta.get("post_id", "")
                    )
                    if comments:
                        all_items.extend(comments)
                        # 본문 댓글 수 갱신
                        post.comments_count = max(post.comments_count, len(comments))
                except Exception as e:
                    logger.debug(f"  SammyFans 댓글 실패 {post.source_url}: {e}")

        logger.info(
            f"SammyFans 수집 완료: 본문 {len(items)}건 / 전체 {len(all_items)}건"
        )
        return all_items

    # ---------- RSS 페이지 fetch (UA 폴백) ----------

    async def _fetch_feed_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[RawVOC]:
        url = RSS_URL if page == 1 else f"{RSS_URL}?paged={page}"
        text, ok = await self._get_with_ua_fallback(client, url)
        if not ok or not text:
            return []
        return self._parse_rss(text)

    async def _get_with_ua_fallback(
        self, client: httpx.AsyncClient, url: str
    ) -> Tuple[str, bool]:
        """Safari UA → 403 이면 Firefox UA 재시도. 둘 다 실패 시 ("", False)."""
        for ua in (SAFARI_UA, FIREFOX_UA):
            try:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": ua,
                        "Referer": BASE_URL + "/",
                        "Accept": (
                            "application/rss+xml, application/xml;q=0.9, "
                            "text/html;q=0.8, */*;q=0.7"
                        ),
                    },
                )
                if resp.status_code == 200:
                    return resp.text, True
                if resp.status_code == 403:
                    logger.debug(f"SammyFans {url} 403 → UA 폴백")
                    continue
                logger.debug(f"SammyFans {url} HTTP {resp.status_code}")
                return "", False
            except Exception as e:
                logger.debug(f"SammyFans {url} 예외 {e} → 다음 UA")
        return "", False

    # ---------- RSS 파싱 ----------

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"SammyFans RSS 파싱 실패: {e}")
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
                    body = self._strip_html(item.findtext("description") or "")
                if len(body) > 4000:
                    body = body[:4000]

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 20:
                    continue

                published_at = self._parse_rss_date(item.findtext("pubDate") or "")
                author = (
                    item.findtext("dc:creator", default="", namespaces=NS) or ""
                ).strip() or None

                comments_count = 0
                try:
                    comments_count = int(
                        (item.findtext("slash:comments", default="0", namespaces=NS)
                         or "0").strip()
                    )
                except (TypeError, ValueError):
                    comments_count = 0

                cats = [
                    (c.text or "").strip()
                    for c in item.findall("category")
                    if c.text
                ]

                external_id = hashlib.md5(
                    f"{link}#post".encode()
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
                        "kind": "article",
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"SammyFans item 파싱 실패: {e}")
        return results

    # ---------- 댓글 (글별 WP /feed/) ----------

    async def _fetch_comments(
        self, client: httpx.AsyncClient, post_url: str, post_id: str
    ) -> List[RawVOC]:
        """WordPress 글별 댓글 RSS — {post_url}feed/.

        대부분 비어있지만, 활성 글에는 댓글이 채워짐. 본문 RawVOC 와 동일 포맷으로 반환.
        """
        if not post_url.endswith("/"):
            comment_url = post_url + "/feed/"
        else:
            comment_url = post_url + "feed/"

        text, ok = await self._get_with_ua_fallback(client, comment_url)
        if not ok or not text:
            return []

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        channel = root.find("channel")
        if channel is None:
            return []

        out: List[RawVOC] = []
        for item in channel.findall("item"):
            try:
                cid = self._extract_comment_id(item.findtext("guid") or "")
                if not cid:
                    # guid 없으면 텍스트 md5
                    raw = (item.findtext("description") or "").strip()
                    cid = hashlib.md5(raw.encode()).hexdigest()[:10]

                body = self._strip_html(
                    item.findtext("content:encoded", default="", namespaces=NS)
                    or item.findtext("description") or ""
                )
                if not body or len(body) < 5:
                    continue

                author = (
                    item.findtext("dc:creator", default="", namespaces=NS) or ""
                ).strip() or None
                published_at = self._parse_rss_date(item.findtext("pubDate") or "")

                ext_id = hashlib.md5(
                    f"{post_url}#c{cid}".encode()
                ).hexdigest()[:16]

                out.append(RawVOC(
                    external_id=ext_id,
                    content=body[:2000],
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    country_code=None,
                    meta={
                        "post_id": post_id,
                        "comment_id": cid,
                        "kind": "comment",
                        "source": "rss-comments",
                    },
                ))
            except Exception as e:
                logger.debug(f"SammyFans comment 파싱 실패: {e}")
        return out

    # ---------- 유틸 ----------

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """WordPress GUID 'https://www.sammyfans.com/?p=150303' → '150303'."""
        if not guid:
            return None
        m = re.search(r"[?&]p=(\d+)", guid)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_comment_id(guid: str) -> Optional[str]:
        """WordPress comment guid '...#comment-12345' → '12345'."""
        if not guid:
            return None
        m = re.search(r"#comment-(\d+)", guid)
        if m:
            return m.group(1)
        m = re.search(r"comment[_-](\d+)", guid)
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
        """RFC822 → UTC. naive 면 UTC 가정 (GLOBAL)."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
