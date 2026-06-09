"""
Hipertextual 크롤러 — httpx + WordPress REST API + RSS 보강 (스페인어, ES/LATAM)

hipertextual.com (스페인어 IT 매거진, ES 본거지 + LATAM 독자, Newspack/WordPress
호스팅) 의 Samsung/Galaxy 관련 기사 본문 수집.

전략
  - 카테고리/태그 HTML 페이지(/tag/samsung 등)는 HTTP 410(Gone) 응답.
    프런트가 Newspack 으로 마이그레이션되며 태그 라우팅이 제거된 듯.
  - 백엔드 WP REST API 는 정상 동작:
      /wp-json/wp/v2/posts?search=samsung  → x-wp-total: 2740 (137 페이지)
      /wp-json/wp/v2/posts?search=galaxy   → x-wp-total: 1710 ( 86 페이지)
    `tags=<id>` 파라미터는 무시되며 (Newspack 캐싱이 query string 일부만 인식),
    `search` 가 가장 신뢰성 있는 필터. tags id 도 1차 후보로 시도 후 폴백.
  - RSS (/feed/) 는 200 으로 응답하며 dc:creator + categoryS + pubDate 제공.
    REST 본문에 없는 저자 정보를 RSS 매핑으로 보강 (link 기준 join).
  - 403/410 발생 시 Firefox UA 로 재시도, 그래도 실패 시 RSS 만으로 폴백.
  - 댓글: 모든 게시글이 comment_status="closed" → 댓글 수집 없음.
    본문 한 건 = 한 VOC.
  - 시간: WP date_gmt 는 naive UTC (YYYY-MM-DDTHH:MM:SS). tz=UTC 부여.
    RSS pubDate 는 RFC822 +0000. naive 면 CEST(+02:00) 가정.
  - 키워드 필터: title+content 에 galaxy/samsung 류 매칭 필요. search 결과라도
    "Super Mario Galaxy" 같은 오탐 컷.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Tuple
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://hipertextual.com"
API_BASE = f"{BASE_URL}/wp-json/wp/v2"
RSS_URL = f"{BASE_URL}/feed/"

# 검색 페이지네이션 — 페이지당 20, term 당 N페이지 = 후보 풀
SEARCH_PER_PAGE = 20
LIST_PAGES = 12          # term 당 최대 페이지 (240 후보/term)
MAX_POSTS = 150          # 최종 처리 캡

# 본문에 검색하는 쿼리어 (스페인어 + 영문 동일)
SEARCH_TERMS = [
    "samsung",
    "galaxy",
    "one ui",
]

# 스페인 표준시(CEST/CET). WP date_gmt 가 있으면 미사용.
CEST = timezone(timedelta(hours=2))  # 여름 (3-10월)

# Firefox UA — 403/410 시 폴백
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)

# Galaxy/Samsung 관련 키워드 (스페인어 환경에서도 영문 동일)
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab",
    "one ui", "oneui", "exynos", "bixby",
]

# Super Mario Galaxy, Galaxy(영화) 등 오탐 차단용 negative 키워드
NEGATIVE_HINTS = [
    "super mario galaxy", "guardianes de la galaxia",
    "galaxy quest", "samsung j5 antiguo",  # 예시
]


class HipertextualCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "hipertextual", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()
        seen_external_ids: set = set()

        # RSS 사이드 인덱스: link → (author, categories, pubDate_str)
        rss_aux: Dict[str, Tuple[Optional[str], List[str], Optional[str]]] = {}

        async with self._make_httpx_client() as client:
            client.headers.update({
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": BASE_URL + "/",
            })

            # 1) RSS 보강 인덱스 구축 (저자 정보 + 최신 글 시드)
            try:
                rss_aux = await self._build_rss_index(client)
                logger.info(f"  Hipertextual RSS aux: {len(rss_aux)} 항목")
            except Exception as e:
                logger.warning(f"  Hipertextual RSS aux 실패: {e}")

            # 2) REST search 로 Samsung/Galaxy 후보 수집
            rest_ok = False
            for term in SEARCH_TERMS:
                try:
                    posts = await self._search_posts(client, term)
                    rest_ok = rest_ok or bool(posts)
                    new = 0
                    for p in posts:
                        voc = self._parse_post(p, rss_aux)
                        if voc is None:
                            continue
                        if not self._is_galaxy_related(voc):
                            continue
                        if voc.source_url in seen_links:
                            continue
                        if voc.external_id in seen_external_ids:
                            continue
                        seen_links.add(voc.source_url)
                        seen_external_ids.add(voc.external_id)
                        items.append(voc)
                        new += 1
                    logger.info(
                        f"  Hipertextual search '{term}': {len(posts)} 결과 / "
                        f"{new} 신규 수집"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Hipertextual search '{term}' 실패: {e}")

            # 3) REST 가 전부 실패한 경우 RSS-only 폴백
            if not rest_ok and rss_aux:
                logger.warning("  Hipertextual REST 전부 실패 → RSS 폴백")
                for link, (author, cats, pubdate) in rss_aux.items():
                    voc = self._rss_to_voc(link, author, cats, pubdate)
                    if voc is None or not self._is_galaxy_related(voc):
                        continue
                    if voc.external_id in seen_external_ids:
                        continue
                    seen_external_ids.add(voc.external_id)
                    items.append(voc)

        # 최신순 정렬 → 상위 MAX_POSTS
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"Hipertextual 수집 완료: {len(result)}건 "
            f"(후보 {len(items)} / RSS aux {len(rss_aux)})"
        )
        return result

    # ---------- WP REST ----------

    async def _search_posts(
        self, client: httpx.AsyncClient, term: str
    ) -> List[dict]:
        """`search=` 쿼리로 페이지네이션. 403/410 발생 시 Firefox UA 재시도."""
        out: List[dict] = []
        for page in range(1, LIST_PAGES + 1):
            try:
                resp = await client.get(
                    f"{API_BASE}/posts",
                    params={
                        "search": term,
                        "per_page": SEARCH_PER_PAGE,
                        "page": page,
                        "_fields": "id,date_gmt,date,link,title,content,excerpt,categories,comment_status",
                    },
                )
                if resp.status_code in (403, 410):
                    # Firefox UA 로 1회 재시도
                    resp = await client.get(
                        f"{API_BASE}/posts",
                        params={
                            "search": term,
                            "per_page": SEARCH_PER_PAGE,
                            "page": page,
                            "_fields": "id,date_gmt,date,link,title,content,excerpt,categories,comment_status",
                        },
                        headers={"User-Agent": FIREFOX_UA},
                    )
                if resp.status_code == 400:
                    # rest_post_invalid_page_number — 페이지 끝
                    break
                if resp.status_code != 200:
                    logger.debug(
                        f"  Hipertextual search '{term}' page={page} "
                        f"HTTP {resp.status_code}"
                    )
                    break
                data = resp.json()
                if not isinstance(data, list) or not data:
                    break
                out.extend(data)
                if len(data) < SEARCH_PER_PAGE:
                    break
            except Exception as e:
                logger.debug(
                    f"  Hipertextual search '{term}' page={page} 실패: {e}"
                )
                break
        return out

    # ---------- RSS ----------

    async def _build_rss_index(
        self, client: httpx.AsyncClient
    ) -> Dict[str, Tuple[Optional[str], List[str], Optional[str]]]:
        """RSS 에서 link → (dc:creator, categories, pubDate) 매핑 구축."""
        resp = await client.get(
            RSS_URL,
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code in (403, 410):
            resp = await client.get(
                RSS_URL,
                headers={
                    "User-Agent": FIREFOX_UA,
                    "Accept": "application/rss+xml, application/xml;q=0.9",
                },
            )
        resp.raise_for_status()
        return self._parse_rss_index(resp.text)

    @staticmethod
    def _parse_rss_index(
        xml_text: str,
    ) -> Dict[str, Tuple[Optional[str], List[str], Optional[str]]]:
        out: Dict[str, Tuple[Optional[str], List[str], Optional[str]]] = {}
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return out
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        for item in root.findall(".//item"):
            link = (item.findtext("link") or "").strip()
            if not link:
                continue
            creator = (item.findtext("dc:creator", namespaces=ns) or "").strip() or None
            cats = [
                (c.text or "").strip()
                for c in item.findall("category")
                if c.text
            ]
            pubdate = (item.findtext("pubDate") or "").strip() or None
            out[link] = (creator, cats, pubdate)
        return out

    def _rss_to_voc(
        self,
        link: str,
        author: Optional[str],
        cats: List[str],
        pubdate: Optional[str],
    ) -> Optional[RawVOC]:
        """RSS-only 폴백용 — title+description 만으로 RawVOC 생성."""
        # 본문이 없으므로 title 만으로 키워드 매칭. 폴백 경로이므로 최소 정보.
        # link 의 slug 가 사실상 title 역할.
        slug = link.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        content = slug.strip()
        if not content:
            return None
        # external_id 은 REST 와 일관되게 link 기반 (재크롤 시 충돌 방지)
        external_id = hashlib.md5(
            f"{link}#post".encode("utf-8")
        ).hexdigest()[:16]
        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=link,
            author_name=author,
            published_at=self._parse_rss_date(pubdate),
            country_code="ES",
            meta={
                "categories": cats[:10],
                "source": "rss_fallback",
            },
        )

    # ---------- Post 파싱 ----------

    def _parse_post(
        self,
        post: dict,
        rss_aux: Dict[str, Tuple[Optional[str], List[str], Optional[str]]],
    ) -> Optional[RawVOC]:
        pid = post.get("id")
        link = (post.get("link") or "").strip()
        if not pid or not link:
            return None

        title = self._strip_html(
            (post.get("title") or {}).get("rendered", "")
        )
        body_html = (post.get("content") or {}).get("rendered", "") or ""
        body = self._strip_html(body_html)
        if not body:
            body = self._strip_html(
                (post.get("excerpt") or {}).get("rendered", "")
            )

        if len(body) > 4000:
            body = body[:4000]

        full = f"{title}\n{body}".strip() if body else title
        if len(full) < 20:
            return None

        # 시간: date_gmt 가 우선 (naive UTC), 없으면 date (naive CEST)
        published_at = self._parse_wp_dt(
            post.get("date_gmt"), naive_is_utc=True
        ) or self._parse_wp_dt(post.get("date"), naive_is_utc=False)

        # 저자: RSS 인덱스에서 보강
        rss_meta = rss_aux.get(link)
        author = rss_meta[0] if rss_meta else None
        cats_text = rss_meta[1] if rss_meta else []

        # external_id 안정성: link + '#post' → md5 → 16자
        external_id = hashlib.md5(
            f"{link}#post".encode("utf-8")
        ).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full,
            source_url=link,
            author_name=author,
            published_at=published_at,
            country_code="ES",
            meta={
                "post_id": pid,
                "categories_rss": cats_text[:10],
                "categories_id": post.get("categories") or [],
                "source": "wp_rest",
            },
        )

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        # negative hint 우선 컷 (Super Mario Galaxy 등)
        if any(neg in text for neg in NEGATIVE_HINTS):
            # samsung 표기가 함께 있으면 통과시킴
            if "samsung" not in text:
                return False
        # 카테고리(RSS) 도 포함해서 검사
        cats_blob = " ".join(voc.meta.get("categories_rss") or []).lower()
        blob = text + " " + cats_blob
        return any(kw in blob for kw in GALAXY_KEYWORDS)

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
    def _parse_wp_dt(value: Optional[str], naive_is_utc: bool) -> Optional[datetime]:
        """WordPress 'YYYY-MM-DDTHH:MM:SS' (naive) 파싱.
        naive_is_utc=True 면 UTC, False 면 CEST(+02:00) 가정."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if naive_is_utc:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.replace(tzinfo=CEST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_rss_date(text: Optional[str]) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 19:18:56 +0000' → UTC.
        naive 일 경우 CEST(+02:00) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CEST)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
