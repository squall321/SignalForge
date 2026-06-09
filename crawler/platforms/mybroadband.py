"""
MyBroadband 크롤러 — httpx + Disqus API (남아공 IT 미디어, 영문)

mybroadband.co.za 는 남아공 최대 IT 뉴스 사이트 (WordPress). Samsung tag 페이지
(`/news/tag/samsung/page/N`) 를 베이스로 한다. 사이트는 Cloudflare 보호이지만
**Googlebot UA** 로 정상 200 응답을 받을 수 있다 (Firefox UA 는 챌린지 페이지).

전략
  - 리스트: `/news/tag/samsung[/page/N]` 페이지에서 `tag-samsung` 클래스를 가진
    `<article>` 의 entry-title 링크 + post-id 추출. 1페이지 ~ 6-8 신규/페이지.
  - 본문: 각 기사 HTML 의 `<article class="... post-{ID} ...">` 안 첫 entry-title
    (h1) 과 entry-content 의 `<p>` 들. 날짜는 `<span class="small">DD.MM.YYYY</span>`
    또는 "Nh ago" 표기, 저자는 `entry-meta .author-link a`.
  - 댓글: 글 페이지에 Disqus 위젯이 lazy-load 됨 (shortname=mybroadband).
    Disqus 공개 API `https://disqus.com/api/3.0/threads/listPosts.json
    ?forum=mybroadband&thread:link={url}&limit=100&api_key=…` 호출.
    공식 임베드 키 사용. 댓글 1건 = VOC 1건.
  - 시각: 페이지 날짜는 ZA 현지 (SAST = UTC+2, DST 없음). Disqus 응답의
    `createdAt` 은 naive UTC (ISO) 라 그대로 UTC 처리.
  - 키워드 필터: 본문/제목 'samsung' / 'galaxy' 매칭 시 채택. 댓글은 부모 글이
    samsung 태그 기사라 무조건 포함.

차단 / 폴백
  - 기본 UA 차단 시 Googlebot UA 로 자동 폴백, 그래도 안 되면 RSS feed
    (`/news/tag/samsung/feed`) 로 폴백 (단 댓글 없음).
"""
import hashlib
import html as html_lib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import quote
import logging
import xml.etree.ElementTree as ET

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://mybroadband.co.za"
TAG_URL = f"{BASE_URL}/news/tag/samsung"
RSS_URL = f"{BASE_URL}/news/tag/samsung/feed"

# Cloudflare 우회 — Googlebot UA (대부분 SA 뉴스 사이트가 검색엔진 크롤러는 허용)
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# 페이지 수집 캡
LIST_PAGES = 12
MAX_POSTS = 150

# SAST 남아공 표준시 — UTC+2, 연중 고정 (DST 없음)
SAST = timezone(timedelta(hours=2))

# Disqus 공식 임베드용 공개 키 (embed.js 에 그대로 노출되는 값)
DISQUS_API_KEY = "E8Uh5l5fHZ6gD8U3KycjAIAk46f68Zw7C6eW8WSjZvCLXebZ7p0r1yrYDrLilk2F"
DISQUS_FORUM = "mybroadband"

# Galaxy/Samsung 키워드 (영문)
GALAXY_KEYWORDS = [
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class MyBroadbandCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "mybroadband", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()

        async with self._make_httpx_client() as client:
            # Cloudflare 우회 — Googlebot UA 고정
            client.headers["User-Agent"] = GOOGLEBOT_UA
            client.headers["Accept-Language"] = "en-ZA,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 리스트 페이지 순회 → 기사 후보 수집
            candidates: list = []  # [(post_id, url)]
            list_ok = 0
            for page in range(1, LIST_PAGES + 1):
                try:
                    page_items = await self._fetch_list_page(client, page)
                    if not page_items:
                        logger.info(f"  MyBroadband list page={page}: 0건 → 종료")
                        break
                    list_ok += 1
                    new = 0
                    for pid, url in page_items:
                        if pid in seen_ids:
                            continue
                        seen_ids.add(pid)
                        candidates.append((pid, url))
                        new += 1
                    logger.info(
                        f"  MyBroadband list page={page}: +{new} 신규 (누적 {len(candidates)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  MyBroadband list page={page} 실패: {e}")

            # 차단 시 RSS 폴백
            if list_ok == 0:
                logger.info("  MyBroadband list 전면 차단 → RSS 폴백")
                try:
                    rss_items = await self._fetch_rss(client)
                    return rss_items[:MAX_POSTS]
                except Exception as e:
                    logger.warning(f"  MyBroadband RSS 폴백 실패: {e}")
                    return []

            # 2) 기사 본문 + Disqus 댓글 수집
            candidates = candidates[:MAX_POSTS]
            for pid, url in candidates:
                try:
                    post_voc, comment_vocs = await self._fetch_article(client, pid, url)
                    if post_voc and self._is_galaxy_related(post_voc):
                        items.append(post_voc)
                        items.extend(comment_vocs)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  MyBroadband article {pid} 실패: {e}")

        # 시간 내림차순 (None 은 맨 뒤)
        items.sort(
            key=lambda v: v.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"MyBroadband 수집 완료: {len(result)}건 (후보 {len(items)})")
        return result

    # --- 리스트 페이지 ---

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[tuple]:
        url = TAG_URL if page == 1 else f"{TAG_URL}/page/{page}"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            logger.debug(f"MyBroadband list page={page} HTTP {resp.status_code}")
            return []
        return self._parse_list(resp.text)

    @staticmethod
    def _parse_list(html: str) -> List[tuple]:
        """tag-samsung 클래스를 가진 article 의 (post_id, url) 추출."""
        results: list = []
        # 메인 영역 (latest-posts 이전) 만 — 사이드 추천 글 노이즈 컷
        main_html = html.split('id="latest-posts-wrapper"')[0]

        # <article ... class="... post-{ID} ... tag-samsung ..." ...>
        # 또는 <article id="post-{ID}" ...>
        # 두 패턴 모두 tag-samsung 보유한 것만.
        article_re = re.compile(
            r'<article[^>]*\bclass="[^"]*\bpost-(\d+)[^"]*\btag-samsung\b[^"]*"[^>]*>',
            re.IGNORECASE,
        )
        # entry-title 의 첫 a href
        url_re = re.compile(
            r'<h[1-6][^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*'
            r'<a[^>]+href="(https://mybroadband\.co\.za/news/[^"]+\.html)"',
            re.IGNORECASE,
        )

        # 각 article 블록을 추출하기 위해 article 닫는 태그까지 잘라낸다
        # 간단히: 모든 article 시작 위치 → 다음 </article> 또는 다음 <article 까지
        positions = [(m.start(), m.group(1)) for m in article_re.finditer(main_html)]
        for i, (start, pid) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(main_html)
            block = main_html[start:end]
            m = url_re.search(block)
            if not m:
                continue
            url = html_lib.unescape(m.group(1))
            results.append((pid, url))

        return results

    # --- 기사 본문 ---

    async def _fetch_article(
        self, client: httpx.AsyncClient, post_id: str, url: str
    ) -> tuple:
        resp = await client.get(url, headers={"Referer": TAG_URL})
        if resp.status_code != 200:
            return None, []

        html = resp.text
        title, body, published_at, author = self._parse_article(html, post_id)
        if not body and not title:
            return None, []

        full_content = f"{title}\n{body}".strip() if body else title
        if len(full_content) < 20:
            return None, []
        if len(full_content) > 4000:
            full_content = full_content[:4000]

        post_voc = RawVOC(
            external_id=hashlib.md5(url.encode()).hexdigest()[:16],
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="ZA",
            meta={"post_id": post_id, "kind": "post"},
        )

        # Disqus 댓글
        comments = await self._fetch_disqus_comments(client, url)
        comment_vocs: List[RawVOC] = []
        for c in comments:
            cid = c.get("id")
            msg = self._strip_html(c.get("message") or c.get("raw_message") or "")
            if not cid or not msg or len(msg) < 5:
                continue
            ext_id = hashlib.md5(f"{url}#c{cid}".encode()).hexdigest()[:16]
            comment_vocs.append(RawVOC(
                external_id=ext_id,
                content=msg[:4000],
                source_url=url,
                author_name=(c.get("author") or {}).get("name"),
                published_at=self._parse_disqus_date(c.get("createdAt")),
                likes_count=int(c.get("likes") or 0),
                country_code="ZA",
                meta={
                    "post_id": post_id,
                    "comment_id": cid,
                    "kind": "comment",
                    "parent_url": url,
                },
            ))

        post_voc.comments_count = len(comment_vocs)
        return post_voc, comment_vocs

    def _parse_article(self, html: str, post_id: str) -> tuple:
        # 메인 article 블록만 잘라낸다 (latest-posts 추천 글과 분리)
        main = html.split('id="latest-posts-wrapper"')[0]

        # 제목
        title = ""
        m = re.search(
            r'<h1[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</h1>',
            main, re.IGNORECASE | re.DOTALL,
        )
        if m:
            title = self._strip_html(m.group(1))

        # 본문 — entry-content 안 <p> 들
        body_parts: list = []
        # 정확하게 post-{post_id} 의 entry-content 만
        article_re = re.compile(
            rf'<article[^>]*\bpost-{re.escape(post_id)}\b[^>]*>(.*?)</article>',
            re.IGNORECASE | re.DOTALL,
        )
        am = article_re.search(main)
        if am:
            article_html = am.group(1)
            ec = re.search(
                r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</article>',
                article_html + "</article>",
                re.IGNORECASE | re.DOTALL,
            )
            content_html = ec.group(1) if ec else article_html
            for pm in re.finditer(r"<p[^>]*>(.*?)</p>", content_html, re.IGNORECASE | re.DOTALL):
                t = self._strip_html(pm.group(1))
                if t and len(t) > 10:
                    body_parts.append(t)

        body = "\n".join(body_parts)

        # 날짜 — entry-header 의 <span class="small">DD.MM.YYYY</span>
        published_at: Optional[datetime] = None
        # 첫 article 의 entry-header 안에서
        hm = re.search(
            r'<div\s+class="entry-meta'  # entry-meta 이전 한 줄 위 .small DD.MM.YYYY
            , main, re.IGNORECASE | re.DOTALL,
        )
        # 더 안전한 방식: post-{post_id} 블록에서 DD.MM.YYYY 매치
        if am:
            dm = re.search(r'<span[^>]*class="small"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>', am.group(1))
            if dm:
                try:
                    naive = datetime.strptime(dm.group(1), "%d.%m.%Y")
                    published_at = naive.replace(tzinfo=SAST).astimezone(timezone.utc)
                except Exception:
                    pass

        # 저자
        author: Optional[str] = None
        if am:
            au = re.search(
                r'class="[^"]*author-link[^"]*"[^>]*>.*?<a[^>]*>([^<]+)</a>',
                am.group(1), re.IGNORECASE | re.DOTALL,
            )
            if au:
                author = html_lib.unescape(au.group(1)).strip() or None

        return title, body, published_at, author

    # --- Disqus 댓글 ---

    async def _fetch_disqus_comments(
        self, client: httpx.AsyncClient, post_url: str
    ) -> list:
        api = (
            "https://disqus.com/api/3.0/threads/listPosts.json"
            f"?forum={DISQUS_FORUM}"
            f"&thread:link={quote(post_url, safe='')}"
            "&limit=100"
            f"&api_key={DISQUS_API_KEY}"
        )
        try:
            resp = await client.get(
                api,
                headers={
                    "Referer": "https://disqus.com/",
                    "Accept": "application/json",
                    "User-Agent": GOOGLEBOT_UA,
                },
                timeout=20.0,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("code") != 0:
                return []
            return data.get("response") or []
        except Exception as e:
            logger.debug(f"Disqus API 실패 ({post_url}): {e}")
            return []

    @staticmethod
    def _parse_disqus_date(s: Optional[str]) -> Optional[datetime]:
        """'2026-05-20T07:31:35' (naive ISO, UTC 기준) → UTC datetime."""
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    # --- RSS 폴백 ---

    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[RawVOC]:
        out: List[RawVOC] = []
        for page in range(1, LIST_PAGES + 1):
            url = RSS_URL if page == 1 else f"{RSS_URL}?paged={page}"
            try:
                resp = await client.get(
                    url,
                    headers={
                        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                        "Referer": BASE_URL + "/",
                    },
                )
                if resp.status_code != 200:
                    break
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                if channel is None:
                    break
                NS = {"content": "http://purl.org/rss/1.0/modules/content/",
                      "dc": "http://purl.org/dc/elements/1.1/"}
                added = 0
                for item in channel.findall("item"):
                    link = (item.findtext("link") or "").strip()
                    title = (item.findtext("title") or "").strip()
                    if not link or not title:
                        continue
                    body = self._strip_html(
                        item.findtext("content:encoded", default="", namespaces=NS)
                        or item.findtext("description")
                        or ""
                    )[:4000]
                    full = f"{title}\n{body}".strip()
                    if len(full) < 20:
                        continue
                    pub_raw = item.findtext("pubDate")
                    pub = None
                    if pub_raw:
                        try:
                            dt = parsedate_to_datetime(pub_raw)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=SAST)
                            pub = dt.astimezone(timezone.utc)
                        except Exception:
                            pub = None
                    out.append(RawVOC(
                        external_id=hashlib.md5(link.encode()).hexdigest()[:16],
                        content=full,
                        source_url=link,
                        author_name=(item.findtext("dc:creator", namespaces=NS) or None),
                        published_at=pub,
                        country_code="ZA",
                        meta={"source": "rss", "kind": "post"},
                    ))
                    added += 1
                if added == 0:
                    break
                await self._random_delay()
            except Exception as e:
                logger.debug(f"RSS page={page} 실패: {e}")
                break
        # 키워드 필터
        return [v for v in out if self._is_galaxy_related(v)]

    # --- helpers ---

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

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)
