"""
Inside-Handy (현 inside-digital.de) 크롤러 — httpx + WordPress RSS

inside-handy.de 는 inside-digital.de 로 도메인 통합됨 (301 redirect).
독일어권 모바일/디지털 전문 매체 (areamobile 후속, WordPress + Newspaper 테마).

전략
  - 메인 RSS /feed (302 → /feed) 는 200, 페이지네이션 ?paged=N (1..12) 도 200.
    페이지당 10건 × 12 = 120 후보.
  - WP-JSON 은 403, /tag/* /category/* RSS 는 404 — 메인 RSS 만 사용.
  - RSS description 은 짧은 발췌만 (200~400자) — 본문 HTML (200 OK) 의
    div.td-post-content 를 fetch 해서 본문 강화. fetch 실패 시 description 만으로 fallback.
  - 댓글: 기사별 /feed 는 HTML 로 응답 (WP 댓글 RSS 비활성). slash:comments 도 부재.
    → 본문 한 건 = 한 VOC. 댓글 채집 불가.
  - 시간: RSS pubDate (RFC822 +0000 UTC) → UTC. naive 면 CET(UTC+1) 가정 (독일).
  - Samsung/Galaxy 키워드 필터 — 제목/본문/카테고리 어느 한 곳이라도 매칭.
  - 403 → Firefox UA 재시도. 그래도 실패 시 다음 페이지로 진행.
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

# 도메인 통합으로 새 도메인 사용 (구 도메인은 301 redirect)
BASE_URL = "https://www.inside-digital.de"
RSS_URL = f"{BASE_URL}/feed"

# RSS 페이지네이션 — 페이지당 10건. 12 × 10 = 120 후보
LIST_PAGES = 12
MAX_POSTS = 150

# 독일 표준시 (CET, UTC+1). RSS pubDate 는 +0000 으로 응답하지만 안전망.
CET = timezone(timedelta(hours=1))

# Firefox UA — 403 폴백
FIREFOX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
)

# WordPress RSS 네임스페이스
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "wfw":     "http://wellformedweb.org/CommentAPI/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

# 독일어/영문 환경 — Samsung/Galaxy 영문 표기는 동일
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
    "tizen", "knox",
]

# 본문 추출 정규식 — Newspaper 테마 td-post-content 블록.
# 중첩 <div> 가 있을 수 있으므로 종결 패턴은 단일 </div> 로 잡고,
# 첫 매칭 이후의 내용은 _strip_html 로 정리한다.
ARTICLE_BODY_RE = re.compile(
    r'<div[^>]*class="[^"]*td-post-content[^"]*"[^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)
# article 종결자가 없는 페이지 대비 — fallback: 다음 </main> 또는 </body> 까지
ARTICLE_BODY_FALLBACK_RE = re.compile(
    r'<div[^>]*class="[^"]*td-post-content[^"]*"[^>]*>(.*?)(?:</main>|</body>)',
    re.DOTALL | re.IGNORECASE,
)

# WordPress GUID post id 추출 — '?p=12345' 또는 '?post_type=deal&p=12345'
GUID_PID_RE = re.compile(r"[?&]p=(\d+)")


class InsideHandyCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "inside_handy", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "de-DE,de;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for page in range(1, LIST_PAGES + 1):
                try:
                    posts = await self._fetch_feed_page(client, page)
                    if not posts:
                        logger.info(
                            f"  InsideHandy RSS page={page}: 0건 → 종료"
                        )
                        break

                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    new_count = 0
                    for p in filtered:
                        if p.source_url in seen_links:
                            continue
                        seen_links.add(p.source_url)
                        # 본문 강화 — 기사 HTML fetch (실패해도 description 본문 유지)
                        await self._enrich_with_article_body(client, p)
                        items.append(p)
                        new_count += 1
                    logger.info(
                        f"  InsideHandy RSS page={page}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  InsideHandy page={page} 실패: {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"InsideHandy 수집 완료: {len(result)}건 (후보 {len(items)})"
        )
        return result

    async def _fetch_feed_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[RawVOC]:
        url = RSS_URL if page == 1 else f"{RSS_URL}?paged={page}"
        headers = {
            "Referer": BASE_URL + "/",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }
        resp = await client.get(url, headers=headers)
        # 403 → Firefox UA 폴백 (사용자 규칙)
        if resp.status_code == 403:
            logger.info(
                f"InsideHandy feed page={page} 403 → Firefox UA 폴백"
            )
            headers["User-Agent"] = FIREFOX_UA
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.debug(
                f"InsideHandy feed page={page} HTTP {resp.status_code}"
            )
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"InsideHandy RSS 파싱 실패: {e}")
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

                # 본문 — content:encoded 우선 (없으면 description)
                content_enc = item.findtext(
                    "content:encoded", default="", namespaces=NS
                )
                body = self._strip_html(content_enc)
                if not body:
                    desc_raw = item.findtext("description") or ""
                    body = self._strip_html(desc_raw)
                # description 끝 'Der Beitrag ... erschien zuerst auf inside digital .' 제거
                # _strip_html 이 모든 공백을 단일 스페이스로 합치므로 ' .' 패턴도 허용.
                body = re.sub(
                    r"\s*Der Beitrag\s+.+?\s+erschien zuerst auf\s+inside digital\s*\.?\s*$",
                    "",
                    body,
                ).strip()

                if len(body) > 4000:
                    body = body[:4000]

                full_content = (
                    f"{title}\n{body}".strip() if body else title
                )
                if len(full_content) < 20:
                    continue

                published_at = self._parse_rss_date(
                    item.findtext("pubDate") or ""
                )
                author = (
                    item.findtext("dc:creator", default="", namespaces=NS).strip()
                    or None
                )

                # slash:comments — 부재할 가능성 있음
                comments_count = 0
                try:
                    raw_c = item.findtext(
                        "slash:comments", default="0", namespaces=NS
                    )
                    comments_count = int((raw_c or "0").strip())
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
                    country_code="DE",
                    meta={
                        "post_id": post_id,
                        "categories": cats[:10],
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"InsideHandy item 파싱 실패: {e}")

        return results

    async def _enrich_with_article_body(
        self, client: httpx.AsyncClient, voc: RawVOC
    ) -> None:
        """기사 HTML 의 td-post-content 본문을 fetch 해서 voc.content 에 합친다.

        RSS description 은 발췌(200~400자) 위주라 본문 강화가 필요. 실패 시 무시.
        """
        try:
            resp = await client.get(
                voc.source_url,
                headers={
                    "Referer": BASE_URL + "/",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            if resp.status_code == 403:
                # Firefox UA 폴백 1회
                resp = await client.get(
                    voc.source_url,
                    headers={"User-Agent": FIREFOX_UA, "Referer": BASE_URL + "/"},
                )
            if resp.status_code != 200:
                voc.meta["article_fetch"] = f"http_{resp.status_code}"
                return
            body = self._extract_article_body(resp.text)
            if not body:
                voc.meta["article_fetch"] = "no_body"
                return
            # 기존 (title + desc) 와 합치되 중복 회피
            base = voc.content
            if body not in base:
                merged = f"{base}\n{body}"
                if len(merged) > 4500:
                    merged = merged[:4500]
                voc.content = merged
            voc.meta["article_fetch"] = "ok"
        except Exception as e:
            voc.meta["article_fetch"] = f"err:{type(e).__name__}"

    @classmethod
    def _extract_article_body(cls, html: str) -> str:
        if not html:
            return ""
        m = ARTICLE_BODY_RE.search(html) or ARTICLE_BODY_FALLBACK_RE.search(html)
        if not m:
            return ""
        return cls._strip_html(m.group(1))

    # --- helpers ---

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """WordPress GUID 에서 '?p=12345' 형태의 post id 추출."""
        if not guid:
            return None
        # HTML 엔티티 디코드 (&#038; 처리)
        decoded = html_lib.unescape(guid)
        m = GUID_PID_RE.search(decoded)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _strip_html(s) -> str:
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

    @staticmethod
    def _parse_rss_date(text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 17:26:00 +0000' → UTC.
        naive 일 경우 CET(UTC+1) 가정 (독일)."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CET)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
