"""
MobileSyrup 크롤러 — httpx + HTML 검색 페이지네이션, Samsung 기사 본문

mobilesyrup.com (캐나다 대표 모바일 IT 미디어, en-CA, WordPress) 의 Samsung/Galaxy
관련 기사 본문 수집.

접근성 분석 (2026-06 기준)
  - /tag/samsung/    : 301 redirect (개별 기사로 점프) — 사용 불가.
  - /tag/samsung/feed: 404 — 태그 RSS 미지원.
  - /wp-json/wp/v2/posts?search=samsung : 200 OK 인데 CloudFront 캐시 때문에
    page/offset/before/slug 파라미터를 모두 무시하고 항상 동일 10건만 반환 →
    페이지네이션 불가.
  - /wp-json/wp/v2/comments : 마찬가지로 항상 동일한 10건의 스팸 댓글 반환.
    실제 댓글은 Viafoura(JS 위젯)로 로드되어 httpx 로는 접근 불가.
  - /feed/ : 200, 최신 20건. Samsung 글 비율 낮음.
  - /?s=samsung + /page/N/?s=samsung : 200, 페이지당 ~20건 article URL,
    수십~수백 페이지 페이지네이션 동작. → 메인 수집 경로.

전략
  1) HTML 검색 결과 페이지에서 article URL 추출 — 페이지네이션 N=LIST_PAGES.
  2) 각 article 페이지 HTML fetch → meta 태그(og:title, article:published_time,
     author) + .article-content 본문 추출.
  3) post id 는 <body class="post-NNNNNNN ..."> 에서 추출 (WP 표준).
  4) 댓글: Viafoura JS-only → 본문 + 댓글 함께 라는 규칙은 본 사이트에서
     기술적으로 불가 (WP comments REST 는 캐시로 잘못된 데이터 반환).
     본문만 수집하되 메타에 viafoura=True 로 표시.
  5) 보조: 메인 RSS 의 최신 Samsung 글 (categories 에 Samsung 포함) 도 추가
     수집해 신선도 보강.
  6) 시간: meta article:published_time = ISO 'YYYY-MM-DDTHH:MM:SS+00:00' (UTC).
     RSS pubDate = RFC822 (UTC). 캐나다 EST/EDT 변환 불필요 — 항상 UTC 발행.
  7) 키워드 필터: 영문 'samsung'/'galaxy'/모델명. 'watch'/'ring' 같이 일반어와
     충돌하는 약어는 단어 경계 정규식으로만 매칭.
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

BASE_URL = "https://mobilesyrup.com"
SEARCH_URL = f"{BASE_URL}/?s=samsung"
SEARCH_PAGED_URL = f"{BASE_URL}/page/{{page}}/?s=samsung"
RSS_URL = f"{BASE_URL}/feed/"

# 표준 contract — LIST_PAGES=12, MAX_POSTS=150
LIST_PAGES = 12
MAX_POSTS = 150

# 영문권 — 강한 키워드(부분일치 OK) + 모델명(단어 경계 필요)
GALAXY_STRONG = ("galaxy", "samsung", "exynos", "bixby", "one ui", "oneui")
GALAXY_MODELS_RE = re.compile(
    r"\b(s2[3-7]\s*(?:ultra|plus|fe)?"
    r"|z\s*fold|z\s*flip"
    r"|galaxy\s*(?:watch|buds|tab|ring|book|fold|flip|s\d+)"
    r"|note\s*2[0-5])\b",
    re.IGNORECASE,
)

# article URL 패턴: /YYYY/MM/DD/<slug>/
ARTICLE_URL_RE = re.compile(
    r'href="(https://mobilesyrup\.com/20\d{2}/\d{2}/\d{2}/[^"#?]+/)"'
)
# <body class="... post-NNNNNNN ...">
POST_ID_RE = re.compile(r"\bpost-(\d{4,})\b")

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}


class MobileSyrupCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "mobilesyrup", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-CA,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1차: HTML 검색 페이지에서 article URL 수집
            article_urls: List[str] = []
            for page in range(1, LIST_PAGES + 1):
                try:
                    urls = await self._fetch_search_page(client, page)
                    new = [u for u in urls if u not in seen_links]
                    for u in new:
                        seen_links.add(u)
                    article_urls.extend(new)
                    logger.info(
                        f"  MobileSyrup search page={page}: {len(new)} 신규 "
                        f"(전체 {len(urls)})"
                    )
                    if not urls:
                        break
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  MobileSyrup search page={page} 실패: {e}")

            # 2차: 메인 RSS 의 Samsung 글 보강
            try:
                rss_items = await self._fetch_main_rss(client)
                rss_filtered = [p for p in rss_items if self._is_galaxy_related(p)]
                for p in rss_filtered:
                    if p.source_url in seen_links:
                        continue
                    seen_links.add(p.source_url)
                    items.append(p)
                logger.info(
                    f"  MobileSyrup RSS 보강: {len(rss_filtered)} 추가"
                )
            except Exception as e:
                logger.warning(f"  MobileSyrup RSS 보강 실패: {e}")

            # 3차: 각 article HTML fetch → 본문 추출 + Samsung 필터
            for url in article_urls[: MAX_POSTS]:
                try:
                    voc = await self._fetch_article(client, url)
                    if voc and self._is_galaxy_related(voc):
                        items.append(voc)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  MobileSyrup article 실패 ({url}): {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"MobileSyrup 수집 완료: {len(result)}건 (후보 {len(items)})"
        )
        return result

    # ---- HTML 검색 ----

    async def _fetch_search_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[str]:
        url = SEARCH_URL if page == 1 else SEARCH_PAGED_URL.format(page=page)
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"  search HTTP {resp.status_code}")
            return []
        # 검색 결과의 article 링크만 추출 (해시/쿼리 제외)
        urls = ARTICLE_URL_RE.findall(resp.text)
        # 중복 제거 + 검색 페이지 자체로의 self-link 류 제외
        return list(dict.fromkeys(urls))

    # ---- 개별 article ----

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[RawVOC]:
        resp = await client.get(
            url,
            headers={
                "Referer": SEARCH_URL,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return None
        return self._parse_article(resp.text, url)

    def _parse_article(self, html: str, url: str) -> Optional[RawVOC]:
        # 메타데이터 (정규식 기반 — bs4 의존성 최소화)
        title = self._extract_meta(html, "og:title") or self._extract_meta(
            html, "twitter:title"
        )
        if not title:
            # <title> 태그 폴백
            m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
            title = m.group(1).strip() if m else ""
        title = self._clean_text(title)

        pub_raw = self._extract_meta(html, "article:published_time", prop=True)
        published_at = self._parse_iso_date(pub_raw)

        author = self._extract_meta(html, "author", prop=False)

        # 본문 추출 — .article-content div
        body = self._extract_article_body(html)

        if not title and not body:
            return None
        content = f"{title}\n{body}".strip() if body else title
        if len(content) < 30:
            return None

        # post id
        post_id_match = POST_ID_RE.search(html)
        post_id = post_id_match.group(1) if post_id_match else None
        id_key = f"post_{post_id}" if post_id else hashlib.md5(url.encode()).hexdigest()[:12]
        external_id = hashlib.md5(f"{url}#{id_key}".encode()).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=author or None,
            published_at=published_at,
            country_code="CA",
            meta={
                "post_id": int(post_id) if post_id else None,
                "source": "html",
                "kind": "post",
                "viafoura": True,  # 댓글은 Viafoura JS 위젯 (수집 불가)
            },
        )

    @staticmethod
    def _extract_meta(html: str, key: str, prop: bool = True) -> str:
        """<meta property="og:title" content="..."> 또는 <meta name="author" content="...">."""
        attr = "property" if prop else "name"
        # 단일 메타 — content 가 먼저인 경우도 처리
        pattern = (
            rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']*)["\']'
        )
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # 역순 (content 가 먼저)
        pattern2 = (
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+{attr}=["\']{re.escape(key)}["\']'
        )
        m = re.search(pattern2, html, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _extract_article_body(self, html: str) -> str:
        """<div class="article-content"> ... </div> 내부 텍스트만 추출.
        flat tag-strip 으로 충분 (이미지/스크립트 등은 _strip_html 에서 정리)."""
        # 시작 div 찾기
        m = re.search(
            r'<div[^>]*class=["\'][^"\']*article-content[^"\']*["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if not m:
            # 폴백: <article>...</article>
            m2 = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
            return self._strip_html(m2.group(1), limit=4000) if m2 else ""
        start = m.end()
        # nested div 추적 — 깊이 0 까지 매칭
        depth = 1
        i = start
        n = len(html)
        while i < n and depth > 0:
            opener = html.find("<div", i)
            closer = html.find("</div", i)
            if closer == -1:
                break
            if opener != -1 and opener < closer:
                depth += 1
                i = opener + 4
            else:
                depth -= 1
                i = closer + 5
        body_html = html[start:i] if depth == 0 else html[start:start + 20000]
        return self._strip_html(body_html, limit=4000)

    # ---- 메인 RSS 보강 ----

    async def _fetch_main_rss(self, client: httpx.AsyncClient) -> List[RawVOC]:
        resp = await client.get(
            RSS_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"MobileSyrup RSS 파싱 실패: {e}")
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
                m = re.search(r"\?p=(\d+)", guid)
                post_id = int(m.group(1)) if m else None

                content_enc = item.findtext("content:encoded", default="", namespaces=NS)
                body = self._strip_html(content_enc, limit=4000)
                if not body:
                    desc_raw = item.findtext("description") or ""
                    body = self._strip_html(desc_raw, limit=4000)

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 30:
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

                id_key = f"post_{post_id}" if post_id else hashlib.md5(link.encode()).hexdigest()[:12]
                external_id = hashlib.md5(f"{link}#{id_key}".encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    comments_count=comments_count,
                    country_code="CA",
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                        "kind": "post",
                    },
                ))
            except Exception as e:
                logger.debug(f"MobileSyrup RSS item 파싱 실패: {e}")
        return results

    # ---- 유틸 ----

    @staticmethod
    def _strip_html(s: str, limit: Optional[int] = None) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        if limit and len(no_tags) > limit:
            no_tags = no_tags[:limit]
        return no_tags

    @staticmethod
    def _clean_text(s: str) -> str:
        if not s:
            return ""
        return html_lib.unescape(re.sub(r"\s+", " ", s).strip())

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        if any(kw in text for kw in GALAXY_STRONG):
            return True
        if GALAXY_MODELS_RE.search(text):
            return True
        cats_l = [c.lower() for c in (voc.meta.get("categories") or [])]
        for c in cats_l:
            if any(kw in c for kw in GALAXY_STRONG):
                return True
            if GALAXY_MODELS_RE.search(c):
                return True
        return False

    def _parse_iso_date(self, text: Optional[str]) -> Optional[datetime]:
        """ISO 'YYYY-MM-DDTHH:MM:SS+00:00' → UTC."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
