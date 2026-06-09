"""
Android Police 크롤러 — httpx + RSS (XML) + HTML 보강

androidpolice.com 의 본문 기사(저널리즘/리뷰)에서 Samsung/Galaxy 관련 VOC 수집.

전략
  - 메인 RSS(/feed/) 는 200 OK 로 응답하지만 Samsung 비율이 낮음.
    /tag/samsung/feed/ 같은 tag-specific RSS 는 404 → 사용 불가.
  - HTML 태그 페이지(/tag/samsung/, /tag/galaxy/)는 200 OK 이며
    page/2,3,... 페이지네이션도 정상 응답.
  - 따라서 (1) /feed/ 에서 최신 기사 메타 후보 확보 → (2) /tag/{samsung,galaxy}/page/N
    리스트에서 article 슬러그 수집 → (3) 각 article 상세 HTML 에서 본문 추출.
  - 댓글 시스템은 JS 비동기 로드 (외부 댓글 위젯) → httpx 직접 수집 불가.
    본문 기반 VOC 만 수집하고 LIST_PAGES 늘려 정보 밀도 보강.
  - GLOBAL 영문 매체이므로 country_code="US".
  - Galaxy/Samsung 키워드 필터로 Pixel/Apple 기사 컷.
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
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.androidpolice.com"
MAIN_FEED_URL = f"{BASE_URL}/feed/"

# HTML 보강용 태그 페이지 (page/N 페이지네이션 지원)
HTML_TAG_PATHS = ["/tag/samsung/", "/tag/galaxy/"]
# 태그당 스캔 페이지 수
LIST_PAGES = 12

# 상세 fetch 최대 article 수 (per source: rss + 각 tag)
MAX_DETAIL_PER_SOURCE = 60
# 최종 처리 캡
MAX_POSTS = 150

NS = {"dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class AndroidPoliceCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "androidpolice", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 메인 RSS — 키워드 필터로 Samsung/Galaxy 만
            try:
                rss_items = await self._fetch_main_feed(client)
                filtered = [p for p in rss_items if self._is_galaxy_related(p)]
                items.extend(filtered)
                logger.info(
                    f"  AndroidPolice RSS /feed/: {len(filtered)}/{len(rss_items)}건"
                )
                await self._random_delay()
            except Exception as e:
                logger.warning(f"  AndroidPolice RSS 실패: {e}")

            # 2) HTML 태그 페이지 → article URL 수집
            article_urls: list = []
            known = {it.source_url for it in items}
            for tag_path in HTML_TAG_PATHS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        urls = await self._fetch_tag_links(client, tag_path, page)
                        new = [u for u in urls if u not in known and u not in article_urls]
                        article_urls.extend(new)
                        logger.info(
                            f"  AndroidPolice {tag_path} p{page}: 신규 {len(new)} (전체 {len(urls)})"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  AndroidPolice {tag_path} p{page} 실패: {e}"
                        )

            article_urls = article_urls[:MAX_DETAIL_PER_SOURCE * len(HTML_TAG_PATHS)]
            logger.info(f"  AndroidPolice 상세 수집 대상: {len(article_urls)}건")

            for art_url in article_urls:
                try:
                    voc = await self._fetch_article(client, art_url)
                    if voc and self._is_galaxy_related(voc):
                        items.append(voc)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"    article {art_url} 실패: {e}")

        # link 단위 중복 제거
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.source_url in seen:
                continue
            seen.add(it.source_url)
            unique.append(it)

        # 최신순 정렬 → 상위 MAX_POSTS
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = unique[:MAX_POSTS]
        logger.info(
            f"AndroidPolice 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    async def _fetch_main_feed(self, client: httpx.AsyncClient) -> List[RawVOC]:
        resp = await client.get(
            MAIN_FEED_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"AndroidPolice RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                guid = (item.findtext("guid") or link).strip()
                # content:encoded 가 있으면 우선, 없으면 description
                ce_el = item.find("content:encoded", NS)
                body_html = (ce_el.text if ce_el is not None and ce_el.text else "") or \
                    (item.findtext("description") or "")
                body = self._strip_html(body_html)

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = creator_el.text.strip() if creator_el is not None and creator_el.text else None

                # 슬러그 기반 안정 ID — WP 사이트는 slug 가 영구적
                slug = self._slug_from_url(link)
                external_id = hashlib.md5(f"{link}#{slug}".encode()).hexdigest()[:16]

                full_content = f"{title}\n{body}".strip() if body else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={"slug": slug, "guid": guid, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"AndroidPolice item 파싱 실패: {e}")

        return results

    async def _fetch_tag_links(
        self, client: httpx.AsyncClient, tag_path: str, page: int
    ) -> List[str]:
        # page=1 은 base path, 그 이상은 /page/N/
        if page == 1:
            url = BASE_URL + tag_path
        else:
            url = BASE_URL + tag_path + f"page/{page}/"

        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code == 404:
            # 페이지 한계 도달
            return []
        resp.raise_for_status()

        # 카테고리/유틸 경로 제외 — 단일 슬러그 article 만
        # /samsung-..., /galaxy-..., /the-..., /one-ui-... 등 두 슬래시 사이 단일 path
        EXCLUDE = {
            "tag", "page", "category", "feed", "about", "contact",
            "ai-machine-learning", "operating-systems", "phones", "tablets",
            "wearables", "smart-home", "smart-tv", "gadgets", "apps", "deals",
            "news", "carriers", "accessories", "awards", "videos", "productivity",
            "utilities", "work-with-us", "sitemap", "rss",
        }
        pattern = re.compile(r'<a[^>]+href="(/[a-z0-9][a-z0-9-]+/)"')
        seen = set()
        out: List[str] = []
        for m in pattern.finditer(resp.text):
            path = m.group(1)
            slug = path.strip("/").split("/")[0]
            if slug in EXCLUDE or len(slug) < 12:  # 짧은 슬러그는 카테고리일 확률 높음
                continue
            full = BASE_URL + path
            if full in seen:
                continue
            seen.add(full)
            out.append(full)
        return out

    async def _fetch_article(self, client: httpx.AsyncClient, url: str) -> Optional[RawVOC]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # 제목 — og:title 우선
        title_el = soup.find("meta", attrs={"property": "og:title"})
        title = title_el.get("content", "").strip() if title_el else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # 본문 — <section id="article-body" class="article-body" itemprop="articleBody">
        # (div 가 아니라 section 태그). 로그인/뉴스레터 모달 p 도 같은 컨테이너에
        # 들어있어 키워드 기반 컷이 필요.
        body_parts: List[str] = []
        article_body = soup.find(attrs={"id": "article-body"})
        if article_body is None:
            article_body = soup.find(attrs={"itemprop": "articleBody"})
        if article_body is None:
            article_body = soup.select_one("section.article-body, div.article-body")
        if article_body is not None:
            for el in article_body.select("p, li"):
                # 부모 클래스에 'valnet-login', 'welcome-msg', 'login-features',
                # 'thread-alert', 'user-msg', 'footer-threads' 가 있으면 모달/광고
                skip = False
                cur = el
                for _ in range(6):
                    if cur is None or cur is article_body:
                        break
                    cls = " ".join(cur.get("class", []) if hasattr(cur, "get") else [])
                    if any(k in cls for k in (
                        "valnet-login", "welcome-msg", "login-features",
                        "thread-alert", "user-msg", "footer-threads",
                    )):
                        skip = True
                        break
                    cur = cur.parent
                if skip:
                    continue
                txt = el.get_text(" ", strip=True)
                if not txt or len(txt) < 20:
                    continue
                # 본문에 섞이는 일반 boilerplate 문구
                tl = txt.lower()
                if any(k in tl for k in (
                    "terms of use and privacy policy",
                    "engage in discussions",
                    "follow and like top authors",
                    "browse with fewer ads",
                    "personalize your profile",
                    "content feed tailored",
                    "forgot your password",
                    "*required: 8 chars",
                )):
                    continue
                body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback: og:description
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = desc_el.get("content", "").strip() if desc_el else ""

        if not title and not body:
            return None

        # 발행일 — meta[property=article:published_time]
        pub_el = soup.find("meta", attrs={"property": "article:published_time"})
        published_at = self._parse_iso_date(pub_el.get("content")) if pub_el else None

        # 저자 — meta[property=article:author] → meta[name=author]
        author_el = soup.find("meta", attrs={"property": "article:author"})
        if not author_el:
            author_el = soup.find("meta", attrs={"name": "author"})
        author = author_el.get("content", "").strip() if author_el else None

        slug = self._slug_from_url(url)
        external_id = hashlib.md5(f"{url}#{slug}".encode()).hexdigest()[:16]

        # 본문 길이 제한 (longform 컷)
        if len(body) > 4000:
            body = body[:4000]

        content = f"{title}\n{body}".strip() if body else title

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="US",
            meta={"slug": slug, "source": "html"},
        )

    # --- helpers ---

    @staticmethod
    def _slug_from_url(url: str) -> str:
        m = re.search(r"androidpolice\.com/([a-z0-9-]+)/?", url)
        return m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:12]

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 13:30:10 GMT' → UTC"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_iso_date(self, text: Optional[str]) -> Optional[datetime]:
        """ISO8601 'YYYY-MM-DDTHH:MM:SSZ' → UTC"""
        if not text:
            return None
        try:
            t = text.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
