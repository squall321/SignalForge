"""
Kompas Tekno 크롤러 — httpx + HTML/JSON-LD + 댓글 API 기반

tekno.kompas.com (인도네시아 최대 매체 Kompas.com 의 IT 섹션, Bahasa
Indonesia) 의 Samsung/Galaxy 관련 기사 본문 + 댓글 수집.

전략
  - /tag/samsung 은 www.kompas.com/tag/samsung 으로 통합 redirect.
    Chrome UA 로 정상 200, 페이지네이션 ?page=N (최대 ~368 페이지).
  - 각 목록에서 `tekno.kompas.com/read/YYYY/MM/DD/<post_id>/<slug>` 형식의
    article URL 추출. 본문은 `<div class="read__content">` 컨테이너 내 <p>.
  - JSON-LD `NewsArticle` 블록에 headline / datePublished / author 명시.
  - 댓글은 페이지 HTML 에는 없고 별도 API:
        https://apiscomment.kompas.com/list?urlpage=<article_url>&json&limit=N
    응답: {result:{komentar:[{comment_id, comment_text, comment_time,
    user_fullname, num_like, type, ...}], total}} — type=='sticker' 인
    이모지/짤 댓글은 텍스트가 없으므로 제외.
  - 시간: JSON-LD datePublished 가 ISO8601 with +07:00 (WIB) → UTC 변환.
    댓글 comment_time 은 UNIX epoch (서버 측 WIB 가 아닌 UTC 기준 정수).
  - RSS 는 모든 경로 404. 폴백은 indeks.kompas.com?site=tekno 의 최근 글.
    그 경로도 막힐 경우 Firefox UA 로 재시도.
"""
import asyncio
import hashlib
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL    = "https://tekno.kompas.com"
TAG_URL     = "https://www.kompas.com/tag/samsung"
COMMENT_API = "https://apiscomment.kompas.com/list"
INDEKS_URL  = "https://indeks.kompas.com/?site=tekno"

LIST_PAGES = 12          # ?page=1..12 (각 약 15건)
MAX_POSTS  = 150
COMMENT_LIMIT = 30       # 글당 댓글 최대 수집 수

# 인도네시아 서부 표준시 (WIB, UTC+7). 인니는 DST 없음.
WIB = timezone(timedelta(hours=7))

# Firefox UA — 403 폴백용 (BaseCrawler USER_AGENTS 와 별개)
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "z fold", "z flip", "fold", "flip", "ultra",
    "galaxy buds", "galaxy watch", "galaxy tab", "galaxy ring",
    "one ui", "oneui", "exynos", "bixby",
    # 인니어 표기 (대부분 영문 그대로 사용하나 안전망)
    "ponsel lipat",  # 폴더블 = "ponsel lipat" 자주 등장
]

# /read/YYYY/MM/DD/<post_id>/<slug>
POST_URL_RE = re.compile(
    r"https://tekno\.kompas\.com/read/\d{4}/\d{2}/\d{2}/(\d{6,12})/[a-z0-9\-]+/?"
)
JSONLD_RE   = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
# <div class="read__content"> ... <div class="read__related|kompasidRec|read__tag">
READ_CONTENT_RE = re.compile(
    r'<div[^>]*class="read__content[^"]*"[^>]*>(.*?)'
    r'(?=<div[^>]*class="(?:read__related|kompasidRec|read__tag|paging))',
    re.DOTALL | re.IGNORECASE,
)


class KompasCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "kompas", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "id-ID,id;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )

            # 1) 목록 — Samsung 태그 페이지네이션
            urls: list[str] = await self._collect_list_urls(client)
            if not urls:
                logger.warning("Kompas tag 페이지 비어있음 → indeks 폴백")
                urls = await self._fallback_indeks(client)

            # 2) 상세 페이지 + 댓글 → RawVOC
            for url in urls[: MAX_POSTS * 2]:
                pid_match = POST_URL_RE.match(url)
                if not pid_match:
                    continue
                post_id = pid_match.group(1)
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                try:
                    article = await self._fetch_article(client, url, post_id)
                    if not article:
                        continue
                    if not self._is_galaxy_related(article):
                        continue
                    items.append(article)
                    # 댓글 추가 (각 댓글이 별도 VOC)
                    comments = await self._fetch_comments(client, url, post_id)
                    items.extend(comments)
                    if len(items) >= MAX_POSTS:
                        break
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  Kompas article {url} 실패: {e}")

        # 최신순 정렬
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"Kompas 수집 완료: {len(result)}건 (후보 {len(items)})")
        return result

    async def _collect_list_urls(self, client: httpx.AsyncClient) -> list[str]:
        urls: list[str] = []
        for page in range(1, LIST_PAGES + 1):
            list_url = TAG_URL if page == 1 else f"{TAG_URL}?page={page}"
            try:
                found = await self._fetch_list_page(client, list_url)
                new = [u for u in found if u not in urls]
                urls.extend(new)
                logger.info(
                    f"  Kompas list page={page}: {len(new)} 신규 URL "
                    f"(누적 {len(urls)})"
                )
                if not new:
                    break
                await self._random_delay()
            except Exception as e:
                logger.warning(f"  Kompas list page={page} 실패: {e}")
        return urls

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, url: str
    ) -> List[str]:
        resp = await client.get(url, headers={"Referer": "https://www.kompas.com/"})
        if resp.status_code == 403:
            logger.info(f"  Kompas list 403 → Firefox UA 재시도 {url}")
            resp = await client.get(
                url,
                headers={
                    "Referer": "https://www.kompas.com/",
                    "User-Agent": FIREFOX_UA,
                },
            )
        if resp.status_code != 200:
            logger.debug(f"Kompas list HTTP {resp.status_code} {url}")
            return []
        html = resp.text
        seen_local: set = set()
        result: list[str] = []
        for m in POST_URL_RE.finditer(html):
            # 슬러그 포함 전체 URL 정확히 보존
            u = m.group(0).rstrip("/")
            if u in seen_local:
                continue
            seen_local.add(u)
            result.append(u)
        return result

    async def _fallback_indeks(self, client: httpx.AsyncClient) -> list[str]:
        """tag/samsung 막혔을 때 최근 Tekno 인덱스에서 URL 수집 후 키워드 필터."""
        try:
            resp = await client.get(INDEKS_URL, headers={"User-Agent": FIREFOX_UA})
            if resp.status_code != 200:
                return []
            html = resp.text
            seen_local: set = set()
            out: list[str] = []
            for m in POST_URL_RE.finditer(html):
                u = m.group(0).rstrip("/")
                if u in seen_local:
                    continue
                seen_local.add(u)
                # URL 슬러그에 키워드 들어있을 때만 후보
                if any(kw in u.lower() for kw in ("samsung", "galaxy", "fold", "flip")):
                    out.append(u)
            return out
        except Exception as e:
            logger.warning(f"Kompas indeks 폴백 실패: {e}")
            return []

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str, post_id: str
    ) -> Optional[RawVOC]:
        resp = await client.get(url, headers={"Referer": TAG_URL})
        if resp.status_code == 403:
            resp = await client.get(
                url,
                headers={"Referer": TAG_URL, "User-Agent": FIREFOX_UA},
            )
        if resp.status_code != 200:
            return None
        html = resp.text

        # JSON-LD NewsArticle
        title, published_at, author, description = "", None, None, ""
        for block in JSONLD_RE.findall(html):
            try:
                d = json.loads(block.strip())
            except Exception:
                continue
            objs = d if isinstance(d, list) else [d]
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                t = obj.get("@type")
                if t in ("NewsArticle", "Article"):
                    title = (obj.get("headline") or "").strip()
                    description = (obj.get("description") or "").strip()
                    dp = obj.get("datePublished")
                    if dp:
                        published_at = self._parse_iso(dp)
                    a = obj.get("author") or {}
                    if isinstance(a, dict):
                        author = (a.get("name") or "").strip() or None
                    elif isinstance(a, list) and a:
                        first = a[0]
                        if isinstance(first, dict):
                            author = (first.get("name") or "").strip() or None
                    break
            if title:
                break

        # 본문 — read__content 내부 <p>
        body = ""
        m = READ_CONTENT_RE.search(html)
        if m:
            inner = m.group(1)
            paras = re.findall(r"<p[^>]*>(.*?)</p>", inner, re.DOTALL)
            cleaned = []
            for p in paras:
                t = self._strip_html(p)
                if not t or len(t) < 12:
                    continue
                # 광고/추천/구독 라인 제외
                if any(skip in t for skip in (
                    "Baca juga", "Baca berita", "Saksikan breaking news",
                    "Editor: ", "Tag", "Berita Terkait",
                )):
                    continue
                cleaned.append(t)
            body = " ".join(cleaned)

        if not body and description:
            body = description

        if len(body) > 4000:
            body = body[:4000]

        full_content = f"{title}\n{body}".strip() if body else title
        if len(full_content) < 20:
            return None

        external_id = hashlib.md5(f"{url}#post".encode()).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="ID",
            meta={
                "post_id": post_id,
                "title": title,
                "source": "html",
                "kind": "article",
            },
        )

    async def _fetch_comments(
        self, client: httpx.AsyncClient, post_url: str, post_id: str
    ) -> List[RawVOC]:
        """apiscomment.kompas.com → 댓글 → 각각 RawVOC."""
        try:
            resp = await client.get(
                COMMENT_API,
                params={"urlpage": post_url, "json": "", "limit": COMMENT_LIMIT},
                headers={"Referer": post_url, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception as e:
            logger.debug(f"Kompas comments {post_url} 실패: {e}")
            return []

        komentar = (data.get("result") or {}).get("komentar") or []
        out: List[RawVOC] = []
        for c in komentar:
            try:
                # 이미지/스티커 댓글은 텍스트가 URL 이라 제외
                if (c.get("type") or "").lower() == "sticker":
                    continue
                text = self._strip_html(c.get("comment_text") or "").strip()
                if len(text) < 5:
                    continue
                cid = str(c.get("comment_id") or "")
                if not cid:
                    continue
                ts = c.get("comment_time")
                published = None
                if isinstance(ts, (int, float)) and ts > 0:
                    published = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                external_id = hashlib.md5(
                    f"{post_url}#c{cid}".encode()
                ).hexdigest()[:16]
                out.append(RawVOC(
                    external_id=external_id,
                    content=text[:2000],
                    source_url=post_url,
                    author_name=(c.get("user_fullname") or "").strip() or None,
                    published_at=published,
                    likes_count=int(c.get("num_like") or 0),
                    country_code="ID",
                    meta={
                        "post_id": post_id,
                        "comment_id": cid,
                        "kind": "comment",
                        "num_dislike": int(c.get("num_dislike") or 0),
                    },
                ))
            except Exception as e:
                logger.debug(f"Kompas comment 파싱 실패: {e}")
        return out

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
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        if any(kw in text for kw in GALAXY_KEYWORDS):
            return True
        title = (voc.meta.get("title") or "").lower()
        return any(kw in title for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _parse_iso(text: str) -> Optional[datetime]:
        """ISO8601 ('2026-06-01T18:06:00+07:00') → UTC.
        naive 일 경우 WIB (UTC+7) 가정."""
        if not text:
            return None
        try:
            t = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=WIB)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def _main():
        c = KompasCrawler()
        vocs = await c.crawl()
        print(f"\n수집: {len(vocs)}건")
        for v in vocs[:5]:
            print(f"- [{v.external_id}] {v.meta.get('kind')} | {v.author_name} @ {v.published_at}")
            print(f"  {v.source_url}")
            print(f"  {v.content[:140]}…")
    asyncio.run(_main())
