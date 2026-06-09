"""
ShiftDelete 크롤러 — httpx + WordPress RSS + 검색 페이지 + 상세 본문/댓글

shiftdelete.net (터키 최대 IT 매체, TR, WordPress) 의 Samsung/Galaxy
관련 기사 본문과 독자 댓글 수집.

전략
  - /tag/samsung, /tag/galaxy 는 미디어 첨부 페이지로 301 → 사용 불가.
  - 정상 동작 채널:
      1) /feed (전 카테고리 RSS, +pagination=?paged=N) → 키워드 필터링
      2) /?s=samsung&paged=N (WP 사이트 검색) → Samsung 전용 백카탈로그
  - 상세 페이지 (/슬러그) 는 200 OK. 본문 + 댓글 함께 노출.
  - 본문: <article class="article" data-post-id="..."> 내부 <p>.
  - 댓글: <ol class="comment-list"> 내부 <li id="comment-NNN">.
           본문 텍스트는 <div class="comment-content"> 안의 <p>.
           작성자는 <cite class="fn">. 시각은 HTML 에 없음.
  - 시간:
      * RSS pubDate 는 +0000 → UTC 그대로.
      * article:published_time meta 는 ISO8601 +TZ → UTC.
      * naive 가 들어오면 TRT (UTC+3, DST 없음) 가정.
  - 댓글 시각이 비어있으므로 부모 게시물 published_at 으로 폴백.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://shiftdelete.net"
RSS_URL = f"{BASE_URL}/feed"
SEARCH_URL = f"{BASE_URL}/?s=samsung"

# 페이지네이션 캡
LIST_PAGES = 12
MAX_POSTS = 150

# 상세 페이지 최대 동시 처리 수 (정중한 크롤)
DETAIL_MAX = 60

# 터키 표준시 (TRT, UTC+3). 2016 년 이후 영구 DST → 항상 +03:00.
TRT = timezone(timedelta(hours=3))

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 터키 환경에서도 Samsung / Galaxy 표기는 영문 동일
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class ShiftDeleteCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "shiftdelete", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        # (url, meta_hint) 쌍
        candidates: List[Tuple[str, dict]] = []
        seen_urls: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "tr-TR,tr;q=0.9,en;q=0.5"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) RSS feed: 최신 글 우선 → 키워드 필터
            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(f"  ShiftDelete RSS page={page}: 0건 → 종료")
                        break
                    new_n = 0
                    for url, hint in posts:
                        if url in seen_urls:
                            continue
                        if not self._hint_matches(hint):
                            continue
                        seen_urls.add(url)
                        candidates.append((url, hint))
                        new_n += 1
                    logger.info(
                        f"  ShiftDelete RSS page={page}: +{new_n} 후보 "
                        f"(전체 {len(posts)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  ShiftDelete RSS page={page} 실패: {e}")

            # 2) WP search: Samsung 백카탈로그 보강
            for page in range(1, LIST_PAGES + 1):
                try:
                    urls = await self._fetch_search_page(client, page)
                    if not urls:
                        logger.info(f"  ShiftDelete search page={page}: 0건 → 종료")
                        break
                    new_n = 0
                    for url in urls:
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        candidates.append((url, {"source": "search"}))
                        new_n += 1
                    logger.info(
                        f"  ShiftDelete search page={page}: +{new_n} 후보 "
                        f"(전체 {len(urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  ShiftDelete search page={page} 실패: {e}")

            # 상세 페이지 처리 (캡)
            details = candidates[:DETAIL_MAX]
            results: List[RawVOC] = []
            for url, hint in details:
                try:
                    vocs = await self._fetch_detail(client, url, hint)
                    if vocs:
                        results.extend(vocs)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  ShiftDelete detail {url} 실패: {e}")

        # 최신순 정렬 → 상위 MAX_POSTS
        results.sort(
            key=lambda v: v.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        results = results[:MAX_POSTS]
        logger.info(f"ShiftDelete 수집 완료: {len(results)}건")
        return results

    # ------------------------------------------------------------------
    # RSS 페이지
    # ------------------------------------------------------------------

    async def _fetch_feed_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[Tuple[str, dict]]:
        url = RSS_URL if page == 1 else f"{RSS_URL}?paged={page}"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"ShiftDelete feed page={page} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[Tuple[str, dict]]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"ShiftDelete RSS 파싱 실패: {e}")
            return []
        channel = root.find("channel")
        if channel is None:
            return []
        out: List[Tuple[str, dict]] = []
        for item in channel.findall("item"):
            link = (item.findtext("link") or "").strip()
            if not link:
                continue
            title = (item.findtext("title") or "").strip()
            pub = self._parse_rss_date(item.findtext("pubDate") or "")
            author = (item.findtext("dc:creator", default="", namespaces=NS) or "").strip() or None
            cats = [(c.text or "").strip() for c in item.findall("category") if c.text]
            slash = item.findtext("slash:comments", default="0", namespaces=NS) or "0"
            try:
                cn = int(slash.strip())
            except ValueError:
                cn = 0
            out.append((link, {
                "title": title,
                "published_at": pub,
                "author": author,
                "categories": cats[:10],
                "comments_count_hint": cn,
                "source": "rss",
            }))
        return out

    # ------------------------------------------------------------------
    # WP 검색 페이지 (HTML)
    # ------------------------------------------------------------------

    async def _fetch_search_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[str]:
        url = SEARCH_URL if page == 1 else f"{SEARCH_URL}&paged={page}"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"ShiftDelete search page={page} HTTP {resp.status_code}")
            return []
        return self._parse_search(resp.text)

    _RE_SEARCH_ITEM = re.compile(
        r'<article\s+class="search-post-item".*?'
        r'<h2\s+class="search-post-title">.*?'
        r'<a[^>]*href="(?P<url>https?://shiftdelete\.net/[a-z0-9][a-z0-9\-]+)"',
        re.DOTALL,
    )

    def _parse_search(self, html: str) -> List[str]:
        urls: List[str] = []
        for m in self._RE_SEARCH_ITEM.finditer(html):
            u = m.group("url")
            # 카테고리/태그 URL 제외 — 보통 단일 단어. 슬러그는 통상 3 단어 이상.
            slug = u.rsplit("/", 1)[-1]
            if "-" not in slug:
                continue
            urls.append(u)
        return urls

    # ------------------------------------------------------------------
    # 상세 페이지: 본문 + 댓글
    # ------------------------------------------------------------------

    _RE_ARTICLE = re.compile(
        r'<article\s+class="article"\s+data-post-id="(?P<pid>\d+)"[^>]*>(?P<body>.*?)</article>',
        re.DOTALL,
    )
    _RE_PUBLISHED = re.compile(
        r'<meta\s+property="article:published_time"\s+content="([^"]+)"'
    )
    _RE_OGTITLE = re.compile(
        r'<meta\s+property="og:title"\s+content="([^"]+)"'
    )
    _RE_BYLINE = re.compile(
        r'<span class="byline">([^<]+)</span>'
    )
    _RE_COMMENT_LIST = re.compile(
        r'<ol[^>]*class="comment-list[^"]*"[^>]*>(?P<body>.*)$',
        re.DOTALL,
    )
    _RE_COMMENT_LI = re.compile(
        r'<li[^>]*id="comment-(?P<cid>\d+)"[^>]*>'
        r'(?P<body>.*?)'
        r'(?=<li[^>]*id="comment-\d+"|</ol>)',
        re.DOTALL,
    )
    _RE_COMMENT_AUTHOR = re.compile(
        r'<cite class="fn">([^<]+)</cite>'
    )
    _RE_COMMENT_CONTENT = re.compile(
        r'<div class="comment-content">(.*?)</div>',
        re.DOTALL,
    )

    async def _fetch_detail(
        self, client: httpx.AsyncClient, url: str, hint: dict
    ) -> List[RawVOC]:
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return []
        html = resp.text

        m = self._RE_ARTICLE.search(html)
        if not m:
            return []
        post_id = m.group("pid")
        article_html = m.group("body")

        # 본문 추출 — <article> 내부의 <p>, 단 임베드된 <style>/<script> 제외
        cleaned = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            article_html, flags=re.DOTALL | re.IGNORECASE,
        )
        # 사이드바/sdn-comment-modal 등 본문 아닌 블록 제거
        cleaned = re.sub(
            r'<div[^>]*class="(sidebar-[^"]+|sdn-comment-modal[^"]*)"[^>]*>.*?</div>',
            " ", cleaned, flags=re.DOTALL,
        )
        paragraphs: List[str] = []
        for pm in re.finditer(r"<p[^>]*>(.*?)</p>", cleaned, re.DOTALL):
            t = self._strip_html(pm.group(1))
            # 'AI ile Özetle' 같은 UI 부스러기 컷
            if not t or len(t) < 25:
                continue
            if t.lower().startswith("ai ile özetle"):
                continue
            paragraphs.append(t)
        body_text = "\n".join(paragraphs)
        if len(body_text) > 4000:
            body_text = body_text[:4000]

        # 제목 / 발행시각 / 저자
        og = self._RE_OGTITLE.search(html)
        title = (og.group(1).rsplit(" - ShiftDelete.Net", 1)[0]
                 if og else hint.get("title", "")).strip()
        title = html_lib.unescape(title)

        pub_m = self._RE_PUBLISHED.search(html)
        published_at = (self._parse_iso_dt(pub_m.group(1))
                        if pub_m else hint.get("published_at"))

        author = hint.get("author")
        if not author:
            am = self._RE_BYLINE.search(html)
            if am:
                author = html_lib.unescape(am.group(1).strip())

        # 키워드 필터 — title/body/categories 중 하나라도 매칭
        full_content = f"{title}\n{body_text}".strip()
        if len(full_content) < 30:
            return []
        if not self._content_matches(full_content, hint.get("categories")):
            return []

        # 댓글 파싱
        comment_vocs = self._parse_comments(html, url, published_at)

        body_voc = RawVOC(
            external_id=hashlib.md5(f"{url}#p{post_id}".encode()).hexdigest()[:16],
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            comments_count=len(comment_vocs),
            country_code="TR",
            meta={
                "post_id": post_id,
                "categories": hint.get("categories") or [],
                "source": hint.get("source", "detail"),
            },
        )
        logger.info(
            f"  ShiftDelete {url.rsplit('/', 1)[-1][:40]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _parse_comments(
        self, html: str, post_url: str, fallback_dt: Optional[datetime]
    ) -> List[RawVOC]:
        cl = self._RE_COMMENT_LIST.search(html)
        if not cl:
            return []
        # 댓글 리스트 영역만 잘라낸다 — 무한 매칭 방지 위해 </ol> 까지의 첫 컷.
        region = cl.group("body")
        # 첫번째 토픽-레벨 </ol> 또는 폼 직전까지 — 보수적으로 .comments-toggle 직전.
        cut = region.find('<div class="comments-toggle-bar')
        if cut > 0:
            region = region[:cut]

        out: List[RawVOC] = []
        for m in self._RE_COMMENT_LI.finditer(region):
            cid = m.group("cid")
            block = m.group("body")
            am = self._RE_COMMENT_AUTHOR.search(block)
            author = html_lib.unescape(am.group(1).strip()) if am else None
            cm = self._RE_COMMENT_CONTENT.search(block)
            if not cm:
                continue
            ctext = self._strip_html(cm.group(1))
            if not ctext or len(ctext) < 5:
                continue
            out.append(RawVOC(
                external_id=hashlib.md5(
                    f"{post_url}#c{cid}".encode()
                ).hexdigest()[:16],
                content=ctext,
                source_url=post_url,
                author_name=author,
                published_at=fallback_dt,
                country_code="TR",
                meta={"comment_id": cid},
            ))
        return out

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

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
        return re.sub(r"\s+", " ", no_tags).strip()

    def _hint_matches(self, hint: dict) -> bool:
        """RSS 단계 키워드 사전 필터: 제목/카테고리 기반.
        본문은 상세에서 한 번 더 검증."""
        title = (hint.get("title") or "").lower()
        cats = " ".join(hint.get("categories") or []).lower()
        blob = f"{title} {cats}"
        return any(kw in blob for kw in GALAXY_KEYWORDS)

    def _content_matches(self, text: str, cats: Optional[List[str]]) -> bool:
        blob = text.lower()
        if any(kw in blob for kw in GALAXY_KEYWORDS):
            return True
        cblob = " ".join(cats or []).lower()
        return any(kw in cblob for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sun, 31 May 2026 13:00:45 +0000' → UTC.
        naive 일 경우 TRT(UTC+3) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_iso_dt(self, text: str) -> Optional[datetime]:
        """ISO8601 '2026-05-31T09:00:00+00:00' → UTC.
        naive 일 경우 TRT(UTC+3) 가정."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
