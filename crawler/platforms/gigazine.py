"""
Gigazine 크롤러 — httpx + RSS(최신 30) + 상세 HTML 본문 추출

gigazine.net (일본 IT/문화 뉴스 사이트, 일본어) 의 Samsung / Galaxy 관련 기사
본문 수집. gizmodo_jp(검색 기반)의 보완 — Gigazine 은 자체 검색이 사실상
빈 결과를 돌려주며 (`/news/search/?q=...` 도 홈 라우팅) , `/news/T-samsung/`
이나 `/news/tag/samsung/` 같은 태그 URL 도 모두 홈으로 라우팅되어 사용 불가.

따라서 Sammobile/Tecnoblog 와 같이 메인 RSS 를 1차 후보 소스로 쓰고,
일자 archive `/news/YYYYMMDD/` 페이지를 보조로 활용한다.

전략
  - 메인 RSS `/news/rss_2.0/` (30건, JST `pubDate`) 수집 → Galaxy/Samsung
    키워드 (영문 + 일본어 サムスン/ギャラクシー) 로 1차 필터.
  - RSS 의 `<description>` 은 발췌(첫 1~2문장) 만 있음. 본문 전문을 위해
    각 기사 상세 HTML 을 fetch → `<p class="preface">` 단락들만 모음.
  - 추가 후보 확보를 위해 오늘 기준 최근 N 일의 `/news/YYYYMMDD/` archive
    페이지에서도 `/news/YYYYMMDD-slug/` 링크를 모은다 (RSS 와 중복 제거).
    archive 페이지는 제목/리스트 뿐이라 본문은 동일하게 상세 HTML 에서.
  - 댓글 시스템은 정적 HTML 에 노출되지 않음 (`<!-- comments_no_login -->`
    마커만 있고 실제 코멘트는 별도 JS 로드) → 1 기사 = 1 VOC.
  - 시간: RFC822 `Mon, 01 Jun 2026 22:00:00 +0900` 또는 JSON-LD
    `2026-06-01T13:32:00+09:00` 모두 tz 명시 → UTC 변환. naive 면 JST 가정.
  - 봇 회피: 표준 UA 풀(rotation). RSS 403 시 Firefox UA 로 재시도.
"""
import asyncio
import hashlib
import html as html_lib
import json
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

BASE_URL = "https://gigazine.net"
RSS_URL  = f"{BASE_URL}/news/rss_2.0/"

# 일본 표준시 (JST, UTC+9). DST 없음.
JST = timezone(timedelta(hours=9))

# RSS 는 최신 30 건만 노출 → archive 일자 페이지로 후보 확장.
# LIST_PAGES=12 → archive 12 일치 + RSS 1 회.
LIST_PAGES = 12
MAX_POSTS  = 150

# 본문 전체 컷오프 (장문 longform 방지)
MAX_BODY_CHARS = 4000

# WordPress 와 무관하지만 RSS 표준 네임스페이스만 사용
NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# Firefox UA 폴백 — 일부 환경에서 Chrome UA 차단 시 사용.
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)

# 일본어 + 영문 키워드. Gigazine 은 Samsung/Galaxy 관련 표기를 한자/카타카나/
# 영문 혼용한다. 어느 한 곳이라도 매치되면 통과.
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "サムスン", "ギャラクシー",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
    "フォールド", "フリップ", "ウルトラ",
    "バッズ", "ウォッチ",
]

# 상세 페이지의 본문은 `<p class="preface">...</p>` 단락 시퀀스.
# (한 기사 안에서 여러 번 등장하며, 사이에 <img>/<a>/<script> 가 끼어 있다.)
PREFACE_RE = re.compile(
    r'<p\s+class="preface"[^>]*>(.*?)</p>',
    flags=re.DOTALL | re.IGNORECASE,
)

# Archive 일자 페이지에서 추출할 기사 슬러그 패턴 — 8자리 일자-slug
ARTICLE_LINK_RE = re.compile(
    r'href="(/news/\d{8}-[a-z0-9][a-z0-9\-]*/)"',
    flags=re.IGNORECASE,
)

# 상세 HTML 내 JSON-LD NewsArticle (datePublished 보정용)
JSONLD_RE = re.compile(
    r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
    flags=re.DOTALL | re.IGNORECASE,
)


class GigazineCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "gigazine", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[tuple] = []  # (url, pubdate_hint, source)
        seen_urls: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "ja,en-US;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 메인 RSS — 최신 30건 메타 + 제목/발췌 키워드 1차 필터
            rss_items = await self._fetch_rss(client)
            logger.info(f"  Gigazine RSS: {len(rss_items)}건 수신")

            rss_filtered: List[dict] = []
            for it in rss_items:
                snippet = f"{it.get('title','')}\n{it.get('summary','')}"
                if self._keyword_hit(snippet):
                    rss_filtered.append(it)
            logger.info(
                f"  Gigazine RSS 키워드 매치: {len(rss_filtered)}/{len(rss_items)}"
            )

            for it in rss_filtered:
                url = it["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                candidates.append((url, it.get("pubdate"), "rss", it))

            # 2) Archive 보조 — RSS 가 30 건뿐이라 며칠치 archive 로 보강.
            #    오늘부터 LIST_PAGES 일 전까지 (어제 포함).
            today = datetime.now(tz=JST).date()
            for i in range(LIST_PAGES):
                day = today - timedelta(days=i)
                yyyymmdd = day.strftime("%Y%m%d")
                try:
                    arch_urls = await self._fetch_archive(client, yyyymmdd)
                    new_cnt = 0
                    for u in arch_urls:
                        if u in seen_urls:
                            continue
                        seen_urls.add(u)
                        candidates.append((u, None, "archive", None))
                        new_cnt += 1
                    logger.info(
                        f"  Gigazine archive {yyyymmdd}: {new_cnt} 신규 "
                        f"(전체 {len(arch_urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Gigazine archive {yyyymmdd} 실패: {e}")

            # 3) 후보 본문 fetch → 키워드 재검증 + 본문 추출
            results: List[RawVOC] = []
            for (url, pub_hint, source, meta_hint) in candidates:
                if len(results) >= MAX_POSTS:
                    break
                try:
                    voc = await self._fetch_article(client, url, pub_hint, source, meta_hint)
                    if voc and self._keyword_hit(voc.content):
                        results.append(voc)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  Gigazine article 실패 [{url}]: {e}")

        results.sort(
            key=lambda v: v.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = results[:MAX_POSTS]
        logger.info(
            f"Gigazine 수집 완료: {len(result)}건 (후보 {len(candidates)})"
        )
        return result

    # --------------------------------------------------------------------
    # RSS
    # --------------------------------------------------------------------
    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[dict]:
        resp = await client.get(
            RSS_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code == 403:
            # Firefox UA 폴백
            logger.info("  Gigazine RSS 403 → Firefox UA 폴백")
            resp = await client.get(
                RSS_URL,
                headers={
                    "User-Agent": FIREFOX_UA,
                    "Referer": BASE_URL + "/",
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                },
            )
        if resp.status_code != 200:
            logger.warning(f"  Gigazine RSS HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[dict]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Gigazine RSS 파싱 실패: {e}")
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        items: List[dict] = []
        for item in channel.findall("item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue
                # HTML 인스턴스 디코딩 (`&#45;` → `-`)
                link = html_lib.unescape(link)
                desc_raw = item.findtext("description") or ""
                summary = self._strip_html(desc_raw)
                pubdate = self._parse_rss_date(item.findtext("pubDate") or "")
                # dc:date 가 더 정확한 경우가 있어 보조 확인
                if not pubdate:
                    dc_date = item.findtext("dc:date", default="", namespaces=NS)
                    pubdate = self._parse_iso_date(dc_date)
                subject = item.findtext("dc:subject", default="", namespaces=NS) or ""
                items.append({
                    "title": title,
                    "url": link,
                    "summary": summary,
                    "pubdate": pubdate,
                    "subject": subject.strip(),
                })
            except Exception as e:
                logger.debug(f"Gigazine RSS item 실패: {e}")
        return items

    # --------------------------------------------------------------------
    # Archive
    # --------------------------------------------------------------------
    async def _fetch_archive(
        self, client: httpx.AsyncClient, yyyymmdd: str
    ) -> List[str]:
        url = f"{BASE_URL}/news/{yyyymmdd}/"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return []
        raw = resp.text
        # 동일 일자 슬러그만 추출 (다른 날짜는 사이드 추천일 가능성 → 제외).
        prefix = f"/news/{yyyymmdd}-"
        urls = set()
        for m in ARTICLE_LINK_RE.finditer(raw):
            path = m.group(1)
            if path.startswith(prefix):
                urls.add(BASE_URL + path)
        return sorted(urls)

    # --------------------------------------------------------------------
    # Article
    # --------------------------------------------------------------------
    async def _fetch_article(
        self,
        client: httpx.AsyncClient,
        url: str,
        pub_hint: Optional[datetime],
        source: str,
        meta_hint: Optional[dict],
    ) -> Optional[RawVOC]:
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return None
        html = resp.text

        # 제목 — og:title 우선, 그 다음 <h1 class="title">
        title = self._extract_meta(html, "og:title") or self._extract_h1_title(html)
        if not title:
            return None

        # 본문 — preface 단락 합치기
        paras: List[str] = []
        for m in PREFACE_RE.finditer(html):
            chunk = self._strip_html(m.group(1))
            if chunk and len(chunk) > 1:
                paras.append(chunk)
        body = "\n".join(paras).strip()

        # 일부 단축 기사는 preface 가 거의 비어있을 수 있어 og:description 보강
        if len(body) < 50:
            desc = self._extract_meta(html, "og:description") or ""
            body = (body + "\n" + desc).strip()

        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]

        full_content = f"{title}\n{body}".strip()
        if len(full_content) < 20:
            return None

        # 발행 시각 — JSON-LD datePublished 가 가장 정확. 없으면 pub_hint.
        published_at = self._extract_published(html) or pub_hint

        # 안정 ID — URL 의 일자-slug 부분.
        slug = self._extract_slug(url) or hashlib.md5(url.encode()).hexdigest()[:12]
        external_id = hashlib.md5(f"{url}#{slug}".encode()).hexdigest()[:16]

        subject = (meta_hint or {}).get("subject", "")
        cats = [s.strip() for s in subject.split(",") if s.strip()]

        return RawVOC(
            external_id=external_id,
            content=full_content,
            source_url=url,
            author_name=None,                # Gigazine 은 익명/편집부 통일
            published_at=published_at,
            comments_count=0,                # 정적 HTML 에 코멘트 카운트 없음
            country_code="JP",
            meta={
                "slug": slug,
                "source": source,            # 'rss' or 'archive'
                "categories": cats[:10],
            },
        )

    # --------------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------------
    @staticmethod
    def _extract_slug(url: str) -> Optional[str]:
        m = re.search(r"/news/(\d{8}-[a-z0-9][a-z0-9\-]*)/?$", url, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _extract_meta(html: str, prop: str) -> Optional[str]:
        # <meta property="og:title" content="..." />  (또는 name=)
        m = re.search(
            rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]+)"',
            html, flags=re.IGNORECASE,
        )
        if m:
            return html_lib.unescape(m.group(1)).strip() or None
        return None

    @staticmethod
    def _extract_h1_title(html: str) -> Optional[str]:
        m = re.search(
            r'<h1\s+class="title"[^>]*>(.*?)</h1>',
            html, flags=re.DOTALL | re.IGNORECASE,
        )
        if m:
            text = re.sub(r"<[^>]+>", "", m.group(1))
            text = html_lib.unescape(text).strip()
            return text or None
        return None

    def _extract_published(self, html: str) -> Optional[datetime]:
        for m in JSONLD_RE.finditer(html):
            blob = m.group(1).strip()
            try:
                data = json.loads(blob)
            except Exception:
                continue
            # NewsArticle 객체 단일 or 배열
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                if obj.get("@type") not in ("NewsArticle", "Article", "WebPage"):
                    continue
                dp = obj.get("datePublished") or obj.get("uploadDate")
                if dp:
                    parsed = self._parse_iso_date(dp)
                    if parsed:
                        return parsed
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

    @staticmethod
    def _keyword_hit(text: str) -> bool:
        if not text:
            return False
        lower = text.lower()
        return any(kw in lower for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _parse_rss_date(text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 22:00:00 +0900' → UTC."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_iso_date(text: str) -> Optional[datetime]:
        """ISO-8601 '2026-06-01T13:32:00+09:00' / 'Z' → UTC."""
        if not text:
            return None
        s = text.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
