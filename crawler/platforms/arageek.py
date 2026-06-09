"""
Arageek 크롤러 — httpx + WordPress REST API (ar-SA, UAE 배포)

arageek.com (아랍어 종합 매거진, ar, UAE/Dubai 호스팅) 의 Samsung/Galaxy
관련 기사 본문 + 댓글 수집.

전략
  - 프런트는 Next.js (404 흔함). 백엔드 WordPress (backend.arageek.com) 의
    REST API 직타가 가장 안정적.
  - /wp-json/wp/v2/search?type=post&search=<term> 는 17~334건/term, 결과는
    {id, title, url, type, subtype} 만 반환. subtype 으로 어떤 커스텀 포스트
    타입 (tech / news / gadgets) 인지 판별.
  - 'tech' 가 뉴스/오피니언, 'gadgets' 는 제품 스펙 카드 (텍스트 부족) →
    'tech' / 'news' / 'post' 만 본문 수집.
  - 검색어:
      1) "samsung"   (영문 그대로 본문에 자주 등장)
      2) "galaxy"
      3) "سامسونج"  (Samsung 의 아랍어 표기)
      4) "جالكسي"   (Galaxy 의 아랍어 표기)
    중복 제거(URL 기준) 후 합산.
  - 본문은 /wp/v2/<subtype>?include=<ids>&_fields=...&_embed=author 로 일괄
    수집 (10개씩 분할, REST GET 한 번에 부하 낮춤).
  - 댓글은 /wp/v2/comments?post=<id>&per_page=50. 게시당 평균 0~1건이라 대상
    포스트 모두에 대해 단발 호출. 봇 스팸(jiliuu.sbs 등) URL 댓글은 필터.
  - 시간: WordPress date_gmt 는 이미 UTC (YYYY-MM-DDTHH:MM:SS). naive 파싱
    후 tz=UTC 부여. UAE 로컬은 date(+04:00) GST 이지만 GMT 가 있으므로 사용
    안 함. 본문 누락 시 date(naive)에 GST(+04:00) → UTC 변환.
  - 키워드 필터: 본문이 영문 'samsung'/'galaxy' 또는 아랍어 'سامسونج'/'جالكسي'
    중 하나라도 포함하면 통과. tech 외 카테고리/태그 정보가 없으므로 본문
    매칭에 의존.
"""
import asyncio
import hashlib
import html as html_lib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple, Dict
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.arageek.com"
API_BASE = "https://backend.arageek.com/wp-json/wp/v2"

SEARCH_TERMS = [
    "samsung",
    "galaxy",
    "سامسونج",   # Samsung in Arabic
    "جالكسي",    # Galaxy in Arabic
]

# 본문이 있는 포스트 타입 (gadgets 는 스펙 카드 → 본문 짧음, 제외)
CONTENT_SUBTYPES = {"tech", "news", "post"}

# WordPress REST 가 한 번에 받는 ID 수 (include 파라미터)
INCLUDE_BATCH = 10

# 검색 페이지네이션 — 페이지당 30, term 당 4페이지 = 120건
SEARCH_PER_PAGE = 30
SEARCH_PAGES = 4

# 댓글 페이징
COMMENTS_PER_PAGE = 50

MAX_POSTS = 150

# UAE 표준시 — GST(+04:00). date_gmt 가 있으면 미사용.
GST = timezone(timedelta(hours=4))

GALAXY_KEYWORD_RE = re.compile(
    r"(samsung|galaxy|one ?ui|exynos|bixby|سامسونج|جالكسي|جالاكسي)",
    re.IGNORECASE,
)

# 댓글 스팸 도메인 (sbs/casino/gambling 류는 봇)
SPAM_URL_RE = re.compile(
    r"(\.sbs|casino|gambling|jiliuu|sportsbet|betting|crypto-?signal)",
    re.IGNORECASE,
)


class ArageekCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "arageek", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_urls: set = set()
        seen_external_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers.update({
                "Accept-Language": "ar-AE,ar;q=0.9,en-US;q=0.7,en;q=0.6",
                "Accept-Encoding": "gzip, deflate",
                "Referer": BASE_URL + "/",
                "Accept": "application/json, */*;q=0.8",
            })

            # 1) Search → (id, subtype, url) 후보 풀
            candidates: List[Tuple[int, str, str]] = []
            cand_seen: set = set()
            for term in SEARCH_TERMS:
                page_hits = await self._search_term(client, term)
                new = 0
                for pid, subtype, url in page_hits:
                    if pid in cand_seen:
                        continue
                    if subtype not in CONTENT_SUBTYPES:
                        continue
                    cand_seen.add(pid)
                    candidates.append((pid, subtype, url))
                    new += 1
                logger.info(
                    f"  Arageek search '{term}': {len(page_hits)} hits / {new} 신규 후보"
                )
                await self._random_delay()

            logger.info(f"  Arageek 후보 총: {len(candidates)} (subtype filtered)")

            # 2) subtype 별로 묶어 /wp/v2/<subtype>?include=... 일괄 fetch
            by_sub: Dict[str, List[Tuple[int, str]]] = {}
            for pid, sub, url in candidates:
                by_sub.setdefault(sub, []).append((pid, url))

            posts_data: Dict[int, dict] = {}
            for sub, lst in by_sub.items():
                for i in range(0, len(lst), INCLUDE_BATCH):
                    batch = lst[i:i + INCLUDE_BATCH]
                    ids = ",".join(str(p[0]) for p in batch)
                    try:
                        url = (
                            f"{API_BASE}/{sub}?include={ids}"
                            f"&_fields=id,date,date_gmt,link,title,content,excerpt,comment_status"
                            f"&_embed=author"
                            f"&per_page={INCLUDE_BATCH}"
                        )
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            logger.debug(
                                f"  Arageek {sub} batch HTTP {resp.status_code}"
                            )
                            continue
                        data = resp.json()
                        if not isinstance(data, list):
                            continue
                        for p in data:
                            pid = p.get("id")
                            if pid:
                                posts_data[pid] = p
                    except Exception as e:
                        logger.debug(f"  Arageek batch fetch 실패: {e}")
                    await self._random_delay()

            logger.info(f"  Arageek 본문 fetch: {len(posts_data)}건")

            # 3) Filter + RawVOC 변환 + 댓글 수집
            for pid, post in posts_data.items():
                try:
                    voc = self._parse_post(post)
                    if voc is None:
                        continue
                    if voc.source_url in seen_urls:
                        continue
                    if not self._is_galaxy_related(voc):
                        continue
                    if voc.external_id in seen_external_ids:
                        continue
                    seen_urls.add(voc.source_url)
                    seen_external_ids.add(voc.external_id)
                    items.append(voc)

                    # 댓글 수집 (comment_status='open' 인 게시글만)
                    if post.get("comment_status") == "open":
                        comments = await self._fetch_comments(client, pid, voc.source_url)
                        for cvoc in comments:
                            if cvoc.external_id in seen_external_ids:
                                continue
                            seen_external_ids.add(cvoc.external_id)
                            items.append(cvoc)
                except Exception as e:
                    logger.debug(f"  Arageek post {pid} 파싱 실패: {e}")

        # 정렬 + 캡
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"Arageek 수집 완료: {len(result)}건 (후보 {len(items)})"
        )
        return result

    async def _search_term(
        self, client: httpx.AsyncClient, term: str
    ) -> List[Tuple[int, str, str]]:
        out: List[Tuple[int, str, str]] = []
        for page in range(1, SEARCH_PAGES + 1):
            try:
                url = (
                    f"{API_BASE}/search"
                    f"?search={httpx.QueryParams({'q': term})['q']}"
                    f"&per_page={SEARCH_PER_PAGE}&page={page}&type=post"
                )
                # httpx 가 자동 인코딩 처리: params 로 전달
                resp = await client.get(
                    f"{API_BASE}/search",
                    params={
                        "search": term,
                        "per_page": SEARCH_PER_PAGE,
                        "page": page,
                        "type": "post",
                    },
                )
                if resp.status_code == 400:
                    # rest_post_invalid_page_number — 페이지 끝 도달
                    break
                if resp.status_code != 200:
                    logger.debug(
                        f"  Arageek search '{term}' page={page} HTTP {resp.status_code}"
                    )
                    break
                data = resp.json()
                if not isinstance(data, list) or not data:
                    break
                for it in data:
                    pid = it.get("id")
                    subtype = it.get("subtype") or it.get("type")
                    url_ = it.get("url") or ""
                    if pid and subtype:
                        out.append((pid, subtype, url_))
                if len(data) < SEARCH_PER_PAGE:
                    break
            except Exception as e:
                logger.debug(f"  Arageek search '{term}' page={page} 실패: {e}")
                break
        return out

    def _parse_post(self, post: dict) -> Optional[RawVOC]:
        pid = post.get("id")
        if not pid:
            return None
        link = (post.get("link") or "").strip()
        if not link:
            return None
        title = self._strip_html(post.get("title", {}).get("rendered", ""))
        body_html = post.get("content", {}).get("rendered", "") or ""
        body = self._strip_html(body_html)
        if not body:
            excerpt = post.get("excerpt", {}).get("rendered", "") or ""
            body = self._strip_html(excerpt)

        if len(body) > 4000:
            body = body[:4000]

        full = f"{title}\n{body}".strip() if body else title
        if len(full) < 20:
            return None

        published_at = self._parse_dt(
            post.get("date_gmt"), naive_is_utc=True
        ) or self._parse_dt(post.get("date"), naive_is_utc=False)

        author = None
        emb = post.get("_embedded", {}) or {}
        authors = emb.get("author") or []
        if authors and isinstance(authors, list):
            author = (authors[0].get("name") or "").strip() or None

        external_id = hashlib.md5(
            f"{link}#post-{pid}".encode("utf-8")
        ).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full,
            source_url=link,
            author_name=author,
            published_at=published_at,
            country_code="AE",
            meta={
                "post_id": pid,
                "subtype": post.get("type") or "tech",
                "source": "wp_rest",
            },
        )

    async def _fetch_comments(
        self, client: httpx.AsyncClient, post_id: int, post_url: str
    ) -> List[RawVOC]:
        out: List[RawVOC] = []
        try:
            resp = await client.get(
                f"{API_BASE}/comments",
                params={
                    "post": post_id,
                    "per_page": COMMENTS_PER_PAGE,
                    "orderby": "date",
                    "order": "asc",
                    "_fields": "id,post,parent,author_name,author_url,date,date_gmt,content",
                },
            )
            if resp.status_code != 200:
                return out
            data = resp.json()
            if not isinstance(data, list):
                return out
            for c in data:
                cid = c.get("id")
                if not cid:
                    continue
                author_url = (c.get("author_url") or "")
                # 봇 스팸 제거 (author_url 도메인이 베팅/카지노/.sbs 등)
                if SPAM_URL_RE.search(author_url):
                    continue
                body_html = c.get("content", {}).get("rendered", "") or ""
                body = self._strip_html(body_html)
                if SPAM_URL_RE.search(body):
                    continue
                if len(body) < 5:
                    continue
                if len(body) > 2000:
                    body = body[:2000]

                published_at = self._parse_dt(
                    c.get("date_gmt"), naive_is_utc=True
                ) or self._parse_dt(c.get("date"), naive_is_utc=False)
                author = (c.get("author_name") or "").strip() or None

                external_id = hashlib.md5(
                    f"{post_url}#c{cid}".encode("utf-8")
                ).hexdigest()[:16]

                out.append(RawVOC(
                    external_id=external_id,
                    content=body,
                    source_url=f"{post_url}#comment-{cid}",
                    author_name=author,
                    published_at=published_at,
                    country_code="AE",
                    meta={
                        "post_id": post_id,
                        "comment_id": cid,
                        "parent": c.get("parent") or 0,
                        "source": "wp_rest_comment",
                    },
                ))
        except Exception as e:
            logger.debug(f"  Arageek comments post={post_id} 실패: {e}")
        return out

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

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
    def _parse_dt(value: Optional[str], naive_is_utc: bool) -> Optional[datetime]:
        """WordPress 'YYYY-MM-DDTHH:MM:SS' (naive) 파싱.
        naive_is_utc=True 면 UTC, False 면 GST(+04:00) → UTC 변환."""
        if not value:
            return None
        try:
            # naive ISO 형식 (예: '2026-03-30T14:00:26')
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if naive_is_utc:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.replace(tzinfo=GST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
