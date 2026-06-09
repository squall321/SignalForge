"""
ComputerBase 크롤러 — httpx + Atom feed + XenForo 댓글 (DE)

computerbase.de 는 독일 IT 매체이며 기사 페이지는 본문이, 부속 토론 스레드는
XenForo 포럼(/forum/threads/<slug>.<id>/) 으로 자동 연결된다. 즉 기사 본문 +
포럼 댓글이 함께 수집되면 정보밀도가 매우 높다.

접근성 (2026-05-31 확인)
  - /forum/forums/smartphones.146/ → 404 (해당 서브포럼 없음)
  - /thema/samsung/ → 301
  - / (홈) → 200, /rss/news.xml → 200, /news/samsung/index.atom → 200
  - /forum/threads/<slug>/ → 200 (XenForo, JS 없이 본문/댓글 모두 HTML 응답)
  - /artikel/.../<id>/ 와 /news/.../<id>/ 본문 페이지 → 200

전략 (MacRumors + Clien 패턴 혼합)
  1) /news/samsung/index.atom 에서 Samsung 카테고리 기사 후보 수집
  2) 전체 /rss/news.xml 추가 수집 후 Samsung/Galaxy 키워드로 필터 (스마트폰 외
     반도체/디스플레이 부문도 잡힘)
  3) 각 기사 본문 페이지에서 <article class="article-view"> 텍스트 추출
  4) 본문 내 .js-thread-link 로부터 포럼 스레드 URL 획득 → XenForo 페이지에서
     article.message--post (data-content="post-XXXX") 들을 댓글 VOC 로 변환
  5) 시각: Atom <published> 와 XenForo <time datetime="...+02:00"> 둘 다 이미
     TZ-aware → astimezone(timezone.utc) 만 적용.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.computerbase.de"

# Atom 피드 (모두 200 OK 확인됨)
#   ("path", "표시명", is_samsung_topic)
# is_samsung_topic=True 면 키워드 필터 없이 전건 통과 (이미 Samsung 카테고리),
# False 면 GALAXY_KEYWORDS 로 필터링.
CB_FEEDS = [
    ("/news/samsung/index.atom",     "Samsung",        True),
    ("/news/smartphones/index.atom", "Smartphones",    False),
    ("/rss/news.xml",                "All News",       False),
]

# 후보 → 본문 보강 상한
MAX_POSTS = 150

# 기사당 수집할 댓글 페이지 수 (XenForo 는 한 페이지에 20 댓글)
MAX_THREAD_PAGES = 2

# Samsung/Galaxy 관련 키워드 (독일어 매체 → 영문 제품명 그대로 사용됨)
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra", "buds", "watch",
    "one ui", "oneui", "exynos",
    "tizen", "bespoke",  # 가전 분야도 Samsung
]


class ComputerBaseCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "computerbase", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # 독일어 우선 — 봇 식별을 어렵게 함
            client.headers["Accept-Language"] = "de-DE,de;q=0.9,en;q=0.6"
            # brotli 디코더 없는 환경 회피
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 피드 수집
            for feed_path, feed_name, is_samsung in CB_FEEDS:
                try:
                    feed_items = await self._fetch_feed(client, feed_path)
                    if is_samsung:
                        filtered = feed_items
                    else:
                        filtered = [it for it in feed_items if self._is_galaxy_related(it)]
                    items.extend(filtered)
                    logger.info(
                        f"  ComputerBase {feed_name}: {len(filtered)}/{len(feed_items)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  ComputerBase {feed_name} 피드 실패: {e}")

            # 2) URL 단위 중복 제거 (여러 피드에 동일 기사가 등장)
            seen: set = set()
            unique: List[RawVOC] = []
            for it in items:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = unique[:MAX_POSTS]
            logger.info(
                f"ComputerBase 후보 {len(items)} → 고유 {len(unique)} → 본문/댓글 보강 {len(target)}건"
            )

            # 3) 본문 + 댓글 보강
            results: List[RawVOC] = []
            for art in target:
                await self._random_delay()
                try:
                    body, thread_url = await self._fetch_article(client, art.source_url)
                    if body and len(body) > len(art.content):
                        art.content = body
                    results.append(art)

                    # 댓글 (포럼 스레드) — 있으면 끝까지
                    if thread_url:
                        try:
                            comments = await self._fetch_thread_posts(
                                client, thread_url, art_published=art.published_at
                            )
                            results.extend(comments)
                        except Exception as e:
                            logger.debug(
                                f"  ComputerBase 댓글 실패 ({thread_url}): {e}"
                            )
                except Exception as e:
                    logger.debug(f"  ComputerBase 본문 실패 ({art.source_url}): {e}")
                    results.append(art)  # 최소 RSS summary 는 보존

        # MX 통합 키워드 영구 필터 (Data Clean 4)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(results)
        results = [v for v in results if is_mx_relevant(v.content)]
        logger.info(f"ComputerBase 수집 완료: {len(results)}건 (mx_filter {before_n}→{len(results)})")
        return results

    # ----- Atom 피드 -----
    async def _fetch_feed(self, client: httpx.AsyncClient, feed_path: str) -> List[RawVOC]:
        url = BASE_URL + feed_path
        resp = await client.get(url, headers={
            "Referer": BASE_URL + "/",
            "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        })
        resp.raise_for_status()
        return self._parse_atom(resp.text)

    def _parse_atom(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"ComputerBase Atom 파싱 실패: {e}")
            return []

        ns = {"a": "http://www.w3.org/2005/Atom"}
        results: List[RawVOC] = []

        for entry in root.findall("a:entry", ns):
            try:
                title_el = entry.find("a:title", ns)
                title = (title_el.text or "").strip() if title_el is not None else ""

                # <link rel="alternate" type="text/html" href="..."/>
                link = ""
                for ln in entry.findall("a:link", ns):
                    if ln.get("rel", "alternate") == "alternate":
                        link = ln.get("href") or ""
                        if link:
                            break
                if not title or not link:
                    continue

                summary_el = entry.find("a:summary", ns)
                summary_raw = (summary_el.text or "") if summary_el is not None else ""
                summary_raw = html_lib.unescape(summary_raw)
                summary = re.sub(r"<[^>]+>", " ", summary_raw)
                summary = re.sub(r"\s+", " ", summary).strip()

                pub = entry.findtext("a:published", "", ns) or entry.findtext("a:updated", "", ns)
                published_at = self._parse_iso8601(pub)

                author_el = entry.find("a:author/a:name", ns)
                author = (author_el.text or "").strip() if author_el is not None else None

                cats = [
                    c.get("term", "")
                    for c in entry.findall("a:category", ns)
                    if c.get("term")
                ]

                combined = f"{title}\n{summary}".strip()

                # 안정 ID: Atom <id> = "tag:computerbase.de,2026:artikel-97599"
                id_el = entry.find("a:id", ns)
                atom_id = (id_el.text or "").strip() if id_el is not None else ""
                stable = atom_id or link
                external_id = hashlib.md5(f"{link}#article".encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="DE",
                    meta={"categories": cats, "atom_id": atom_id, "kind": "article"},
                ))
            except Exception as e:
                logger.debug(f"ComputerBase entry 파싱 실패: {e}")

        return results

    # ----- 본문 + 댓글 링크 -----
    async def _fetch_article(
        self, client: httpx.AsyncClient, article_url: str
    ) -> tuple[Optional[str], Optional[str]]:
        """기사 본문 텍스트와 연결된 포럼 스레드 URL 반환."""
        resp = await client.get(article_url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code >= 400:
            return None, None
        soup = BeautifulSoup(resp.text, "lxml")

        # 본문 컨테이너: article.article-view (테스트/리뷰) 또는 article.article (뉴스)
        art_el = soup.select_one("article.article-view") or soup.select_one("article.article") or soup.select_one("article")
        body: Optional[str] = None
        if art_el:
            for trash in art_el.select(
                "script, style, aside, figure, "
                ".article-nav, .article__pic, .article__comments-link, "
                ".article-view__below-content-ad, .article-view__above-content-ad, "
                ".widget, .skim-block"
            ):
                trash.decompose()
            body_text = art_el.get_text("\n", strip=True)
            body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
            if body_text:
                body = body_text

        # 댓글 토론 스레드 링크 — a.js-thread-link 가 가장 안정적
        thread_url: Optional[str] = None
        link_el = soup.select_one("a.js-thread-link[href]")
        if link_el:
            href = link_el.get("href", "").strip()
            if href.startswith("/"):
                thread_url = BASE_URL + href
            elif href.startswith("http"):
                thread_url = href

        return body, thread_url

    # ----- XenForo 댓글 -----
    async def _fetch_thread_posts(
        self,
        client: httpx.AsyncClient,
        thread_url: str,
        art_published: Optional[datetime] = None,
    ) -> List[RawVOC]:
        """포럼 스레드의 댓글들을 RawVOC 로 반환. 1번 페이지는 보통 기사 봇 글이라 제외."""
        all_comments: List[RawVOC] = []

        for page in range(1, MAX_THREAD_PAGES + 1):
            url = thread_url if page == 1 else f"{thread_url.rstrip('/')}/page-{page}"
            try:
                resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
            except Exception as e:
                logger.debug(f"  ComputerBase thread page {page} 요청 실패: {e}")
                break
            if resp.status_code >= 400:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            posts = soup.select("article.message--post")
            if not posts:
                break

            for p in posts:
                # data-content="post-31494958" → 안정 ID
                pid = p.get("data-content") or ""
                pid_stable = pid or hashlib.md5(
                    p.get_text(" ", strip=True)[:200].encode()
                ).hexdigest()[:12]

                author = (p.get("data-author") or "").strip() or None

                # 본문: .message-body .bbWrapper
                body_el = p.select_one(".message-body .bbWrapper") or p.select_one(".message-body")
                if body_el is None:
                    continue
                # 인용/스크립트 제거 (인용은 유지하되 줄바꿈으로 정돈)
                for trash in body_el.select("script, style"):
                    trash.decompose()
                text = body_el.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) < 5:
                    continue

                # 시각
                time_el = p.select_one("time[datetime]")
                published_at: Optional[datetime] = None
                if time_el:
                    published_at = self._parse_iso8601(time_el.get("datetime", ""))

                # 1페이지의 첫 글은 보통 기사 봇 (시각이 기사와 동일) → 중복 회피 위해
                # external_id 만 다르므로 그대로 두면 됨. 그러나 정보 밀도엔 도움 안 됨.
                # 여기선 봇 글도 댓글 흐름 일부라 그대로 포함.

                external_id = hashlib.md5(
                    f"{thread_url}#c{pid_stable}".encode()
                ).hexdigest()[:16]

                all_comments.append(RawVOC(
                    external_id=external_id,
                    content=text,
                    source_url=f"{thread_url}#{pid_stable}",
                    author_name=author,
                    published_at=published_at,
                    country_code="DE",
                    meta={"kind": "comment", "thread_url": thread_url, "page": page},
                ))

            # 다음 페이지가 없으면 중단
            next_btn = soup.select_one(f'.pageNav-jump--next[href*="page-{page+1}"]') \
                        or soup.select_one(".pageNav-jump--next")
            if not next_btn:
                break

            # rate-limit
            import asyncio as _a
            await _a.sleep(0.8)

        return all_comments

    # ----- 필터/유틸 -----
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        cats = " ".join(voc.meta.get("categories") or []).lower()
        haystack = f"{text} {cats}"
        return any(kw in haystack for kw in GALAXY_KEYWORDS)

    def _parse_iso8601(self, text: str) -> Optional[datetime]:
        """'2026-05-30T08:00:00+02:00' or '2026-05-12T16:55:40+0200' → UTC datetime"""
        if not text:
            return None
        try:
            # +0200 형태는 fromisoformat 가 못 읽으니 +02:00 로 보정
            t = text.strip()
            m = re.match(r"^(.*[T ]\d{2}:\d{2}:\d{2})([+-])(\d{2})(\d{2})$", t)
            if m:
                t = f"{m.group(1)}{m.group(2)}{m.group(3)}:{m.group(4)}"
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                # CB 서버 시간 = 베를린 (CET/CEST). 안전하게 +1h 가정.
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
