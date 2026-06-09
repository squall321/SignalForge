"""
DonanımHaber 크롤러 — httpx + 카테고리 페이지네이션 + JSON-LD + 댓글 API

donanimhaber.com (터키 최대 IT 매체, TR, ASP.NET) 의 Samsung/Galaxy
관련 기사 본문과 독자 댓글 수집.

전략 (6 차 실패 정밀 재분석)
  - 메인 RSS (/rss/tum) 는 200 OK 이나 카테고리 RSS (/rss/kategori/*)
    및 태그 페이지 (/etiket/samsung) 는 모두 404 또는 JS lazy-load.
  - 모바일/스마트폰 카테고리 페이지 `/cep-telefonlari?sayfa=N` 가 안정 채널:
      페이지당 ~40 기사, p=2 부터 페이징 유효.
  - 메인 RSS 도 보강용 (50 최신, ~5 매칭) 으로 병용.
  - 상세 페이지 콘텐츠 추출은 HTML 파싱 대신 `<script type="application/ld+json">`
    내 NewsArticle.articleBody / headline / datePublished / author.name /
    commentCount 사용 — 가장 안정적.
  - 댓글: 상세 HTML 의 `comments-thread-<THREAD_ID>` 패턴으로 threadId 추출 후
    `/Api/Comments/LoadCommentContainer?threadId=...&forumId=...&editorId=...`
    JSON 호출 → HTML 파싱.
      * 각 댓글 = `<div id="comment-<CID>" data-id="<CID>" class="... yorum">` …
        `<span class="nick"><a>…</a>` 작성자
        `<span class="zaman" title="DD.M.YYYY HH:MM:SS">…</span>` 시각
        `<div class="mesaj"><table><tbody><tr><td>…</td></tr></tbody></table></div>` 본문
  - 인코딩: 모든 응답이 gzip — httpx 는 기본 자동 해제.
  - 봇 회피: Firefox UA + Referer + Accept-Encoding gzip + 한국어 X (tr-TR).
  - 시간: 메타 `datetime="2026-05-31T19:30:00+03:00"` 또는 articleBody 의 zaman
    title `DD.M.YYYY HH:MM:SS` (TRT, naive) → UTC 변환.
"""
import hashlib
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import logging
import xml.etree.ElementTree as ET

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.donanimhaber.com"
RSS_URL = f"{BASE_URL}/rss/tum/"
# 모바일 카테고리 — Samsung/Galaxy 풍부.
CAT_URL = f"{BASE_URL}/cep-telefonlari"

LIST_PAGES = 12
MAX_POSTS = 150
DETAIL_MAX = 60

# 터키 표준시 (TRT, UTC+3, DST 없음 — 2016 영구 도입)
TRT = timezone(timedelta(hours=3))

# DH 사이트는 Firefox UA 가 6 차 빌드에서 더 안정적이었음.
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
    "Gecko/20100101 Firefox/121.0"
)

# 터키어/영어 동일 표기.
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
    "a16", "a17", "a26", "a27", "a36", "a37", "a56", "a57",
]


class DonanimHaberCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "donanimhaber", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        # (url, hint) 후보
        candidates: List[Tuple[str, dict]] = []
        seen_urls: set = set()

        async with httpx.AsyncClient(
            headers={
                "User-Agent": FIREFOX_UA,
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
            http2=False,
        ) as client:
            # 1) 메인 RSS — 최신 50건 (모든 카테고리). 키워드 매칭만 통과.
            try:
                rss_posts = await self._fetch_rss(client)
                for url, hint in rss_posts:
                    if url in seen_urls:
                        continue
                    if not self._hint_matches(hint):
                        continue
                    seen_urls.add(url)
                    candidates.append((url, hint))
                logger.info(
                    f"  DonanımHaber RSS: 매칭 {len(candidates)} / 전체 {len(rss_posts)}"
                )
                await self._random_delay()
            except Exception as e:
                logger.warning(f"  DonanımHaber RSS 실패: {e}")

            # 2) 모바일 카테고리 페이지네이션 — Samsung/Galaxy 슬러그 우선.
            cat_before = len(candidates)
            for page in range(1, LIST_PAGES + 1):
                try:
                    urls = await self._fetch_category_page(client, page)
                    if not urls:
                        logger.info(f"  DonanımHaber cat page={page}: 0건 → 종료")
                        break
                    new_n = 0
                    for url in urls:
                        if url in seen_urls:
                            continue
                        slug = url.rsplit("/", 1)[-1].lower()
                        # URL 슬러그 사전 필터 — 댓글 적은 비매칭 기사 스킵.
                        if not any(kw in slug for kw in GALAXY_KEYWORDS):
                            continue
                        seen_urls.add(url)
                        candidates.append((url, {"source": "category"}))
                        new_n += 1
                    logger.info(
                        f"  DonanımHaber cat page={page}: +{new_n} 후보 "
                        f"(전체 {len(urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(
                        f"  DonanımHaber cat page={page} 실패: {e}"
                    )
            logger.info(
                f"  DonanımHaber 카테고리 매칭: +{len(candidates) - cat_before}"
            )

            # 3) 상세 — 본문 + 댓글
            details = candidates[:DETAIL_MAX]
            results: List[RawVOC] = []
            for url, hint in details:
                try:
                    vocs = await self._fetch_detail(client, url, hint)
                    if vocs:
                        results.extend(vocs)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  DonanımHaber detail {url} 실패: {e}")

        results.sort(
            key=lambda v: v.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        results = results[:MAX_POSTS]
        # MX 통합 키워드 영구 필터 (Data Clean 4)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(results)
        results = [v for v in results if is_mx_relevant(v.content)]
        logger.info(f"DonanımHaber 수집 완료: {len(results)}건 (mx_filter {before_n}→{len(results)})")
        return results

    # ------------------------------------------------------------------
    # 메인 RSS
    # ------------------------------------------------------------------

    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[Tuple[str, dict]]:
        resp = await client.get(
            RSS_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"DonanımHaber RSS HTTP {resp.status_code}")
            return []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning(f"DonanımHaber RSS 파싱 실패: {e}")
            return []
        ch = root.find("channel")
        if ch is None:
            return []
        out: List[Tuple[str, dict]] = []
        for it in ch.findall("item"):
            link = (it.findtext("link") or "").strip()
            if not link or "--" not in link:
                continue
            title = (it.findtext("title") or "").strip()
            desc = (it.findtext("description") or "").strip()
            pub = self._parse_rfc822(it.findtext("pubDate") or "")
            out.append((link, {
                "title": title,
                "description": desc,
                "published_at": pub,
                "source": "rss",
            }))
        return out

    # ------------------------------------------------------------------
    # 카테고리 리스트 (HTML)
    # ------------------------------------------------------------------

    _RE_CAT_LINK = re.compile(
        r'href="(?:https://www\.donanimhaber\.com)?(/[a-z0-9][a-z0-9-]+--\d+)"'
    )

    async def _fetch_category_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[str]:
        url = CAT_URL if page == 1 else f"{CAT_URL}?sayfa={page}"
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
        # 절대/상대 모두 받아 정규화. 중복 제거.
        urls: List[str] = []
        seen: set = set()
        for m in self._RE_CAT_LINK.finditer(html):
            path = m.group(1)
            # forum 서브도메인 / 외부 링크는 _RE_CAT_LINK 의 호스트 제약에서 이미 배제.
            full = BASE_URL + path
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    # ------------------------------------------------------------------
    # 상세 페이지
    # ------------------------------------------------------------------

    _RE_LDJSON = re.compile(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL,
    )
    _RE_THREAD_ID = re.compile(
        r'(?:loadCommentContainer\(|comments-thread-)(\d+)(?:,\s*(\d+),\s*(\d+))?'
    )
    _RE_THREAD_FULL = re.compile(
        r'loadCommentContainer\((\d+),\s*(\d+),\s*(\d+)\)'
    )
    _RE_OG_TITLE = re.compile(
        r'<meta\s+property="og:title"\s+content="([^"]+)"'
    )
    _RE_TIME_DT = re.compile(
        r'<time[^>]*class="veri"[^>]*datetime="([^"]+)"'
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

        # 1) JSON-LD 에서 본문/메타 추출.
        article = self._extract_article_ld(html)
        if article is None:
            return []

        title = (article.get("headline") or "").strip()
        body = (article.get("articleBody") or "").strip()
        full_content = (f"{title}\n{body}").strip()
        if len(full_content) < 30:
            return []

        # 2) 키워드 매칭 검증 (이중 안전망).
        if not self._content_matches(full_content):
            return []

        # 3) 발행 시각 — JSON-LD datePublished 우선, 보조로 <time class="veri">.
        published_at = self._parse_iso(article.get("datePublished"))
        if published_at is None:
            tm = self._RE_TIME_DT.search(html)
            if tm:
                published_at = self._parse_iso(tm.group(1))
        if published_at is None:
            published_at = hint.get("published_at")

        # 4) 저자.
        author = None
        a = article.get("author")
        if isinstance(a, dict):
            author = (a.get("name") or "").strip() or None
        elif isinstance(a, list) and a:
            author = (a[0].get("name") if isinstance(a[0], dict) else None)

        # 5) 카테고리.
        section = (article.get("articleSection") or "").strip()

        # 6) 댓글 수, 외부 ID.
        cm_count = article.get("commentCount") or 0
        try:
            cm_count = int(cm_count)
        except (TypeError, ValueError):
            cm_count = 0

        # 7) 본문에서 게시물 numeric ID 추출 — URL `<slug>--<NID>`.
        nid_m = re.search(r"--(\d+)$", url)
        post_id = nid_m.group(1) if nid_m else hashlib.md5(url.encode()).hexdigest()[:8]

        body_voc = RawVOC(
            external_id=hashlib.md5(f"{url}#p{post_id}".encode()).hexdigest()[:16],
            content=full_content[:4000],
            source_url=url,
            author_name=author,
            published_at=published_at,
            comments_count=cm_count,
            country_code="TR",
            meta={
                "post_id": post_id,
                "section": section,
                "source": hint.get("source", "detail"),
            },
        )

        # 8) 댓글 API 호출.
        comment_vocs: List[RawVOC] = []
        if cm_count > 0:
            comment_vocs = await self._fetch_comments(client, html, url, published_at)

        logger.info(
            f"  DonanımHaber {url.rsplit('/', 1)[-1][:40]}: "
            f"본문 {len(body)}자 + 댓글 {len(comment_vocs)}/{cm_count}건"
        )
        return [body_voc] + comment_vocs

    def _extract_article_ld(self, html: str) -> Optional[dict]:
        """`<script type="application/ld+json">` 들에서 NewsArticle 노드를 찾는다."""
        for m in self._RE_LDJSON.finditer(html):
            raw = m.group(1).strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # NewsArticle 직접 / @graph 중첩 / 리스트 모두 대응.
            for node in self._iter_graph(data):
                t = node.get("@type")
                if t == "NewsArticle" or (isinstance(t, list) and "NewsArticle" in t):
                    return node
                if t == "Article" or (isinstance(t, list) and "Article" in t):
                    return node
        return None

    @staticmethod
    def _iter_graph(data):
        """JSON-LD 의 다양한 형태를 평탄화."""
        if isinstance(data, list):
            for it in data:
                yield from DonanimHaberCrawler._iter_graph(it)
            return
        if not isinstance(data, dict):
            return
        if "@graph" in data and isinstance(data["@graph"], list):
            for it in data["@graph"]:
                yield from DonanimHaberCrawler._iter_graph(it)
        yield data

    # ------------------------------------------------------------------
    # 댓글 API
    # ------------------------------------------------------------------

    _RE_COMMENT_BLOCK = re.compile(
        r'<div[^>]+id="comment-(?P<cid>\d+)"[^>]+class="[^"]*\byorum\b[^"]*"'
        r'(?P<body>.*?)(?=<div[^>]+id="comment-\d+"|<div\s+class="comments-toggle|$)',
        re.DOTALL,
    )
    _RE_COMMENT_NICK = re.compile(
        r'<span class="nick[^"]*"[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*(?:<span class="name">|</a>)',
        re.DOTALL,
    )
    _RE_COMMENT_ZAMAN = re.compile(
        r'<span class="zaman"[^>]*title="([^"]+)"'
    )
    _RE_COMMENT_MESAJ = re.compile(
        r'<div class="mesaj">(.*?)</div>\s*<div class="aksiyon"',
        re.DOTALL,
    )

    async def _fetch_comments(
        self, client: httpx.AsyncClient, article_html: str,
        post_url: str, fallback_dt: Optional[datetime],
    ) -> List[RawVOC]:
        m = self._RE_THREAD_FULL.search(article_html)
        if not m:
            return []
        thread_id, forum_id, editor_id = m.group(1), m.group(2), m.group(3)
        api = (
            f"{BASE_URL}/Api/Comments/LoadCommentContainer"
            f"?threadId={thread_id}&forumId={forum_id}&editorId={editor_id}"
        )
        try:
            resp = await client.get(
                api,
                headers={
                    "Referer": post_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                },
            )
        except Exception:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        if data.get("HasError") or not data.get("Data"):
            return []
        return self._parse_comment_html(data["Data"], post_url, fallback_dt)

    def _parse_comment_html(
        self, html: str, post_url: str, fallback_dt: Optional[datetime]
    ) -> List[RawVOC]:
        out: List[RawVOC] = []
        for m in self._RE_COMMENT_BLOCK.finditer(html):
            cid = m.group("cid")
            block = m.group("body")

            nm = self._RE_COMMENT_NICK.search(block)
            author = html_lib.unescape(nm.group(1).strip()) if nm else None

            tm = self._RE_COMMENT_ZAMAN.search(block)
            ts = self._parse_zaman_title(tm.group(1)) if tm else None
            if ts is None:
                ts = fallback_dt

            mm = self._RE_COMMENT_MESAJ.search(block)
            if not mm:
                continue
            text = self._strip_html(mm.group(1))
            if not text or len(text) < 5:
                continue

            out.append(RawVOC(
                external_id=hashlib.md5(
                    f"{post_url}#c{cid}".encode()
                ).hexdigest()[:16],
                content=text[:2000],
                source_url=post_url,
                author_name=author,
                published_at=ts,
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
        blob = f"{hint.get('title','')} {hint.get('description','')}".lower()
        return any(kw in blob for kw in GALAXY_KEYWORDS)

    def _content_matches(self, text: str) -> bool:
        blob = text.lower()
        return any(kw in blob for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _parse_rfc822(text: str) -> Optional[datetime]:
        """RFC822 → UTC. naive 이면 TRT 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_iso(text: Optional[str]) -> Optional[datetime]:
        """ISO8601 (TZ 포함 / 미포함) → UTC. naive 이면 TRT 가정."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_zaman_title(text: str) -> Optional[datetime]:
        """`<span class="zaman" title="31.5.2026 21:00:01">` → UTC.
        format: `D.M.YYYY HH:MM:SS` (TRT, naive)."""
        if not text:
            return None
        text = text.strip()
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=TRT)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        return None
