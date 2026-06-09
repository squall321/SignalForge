"""
Ausdroid 크롤러 — httpx + WordPress JSON API + RSS 폴백

ausdroid.net (호주 안드로이드 매체, WordPress) 의 Samsung/Galaxy 태그 글을 수집.
사이트 root 는 카지노 리디렉션이 걸려 있지만 /tag/<slug>/feed/ 와
/wp-json/wp/v2/posts 는 정상(200)으로 응답한다 (Cloudflare 통과).

전략
  - 1차: WP REST API (/wp-json/wp/v2/posts?tags=<id>) 로 페이지네이션 수집.
    Samsung 태그(id=13501) 105건 + Galaxy 관련 태그(galaxy-s24/s25/watch/oneui) 병합.
    content.rendered 가 HTML 본문 전체 → 평문화 후 사용 (가장 풍부).
  - 2차: RSS (/tag/<slug>/feed/) 로 보강. 새 글이 우선 RSS 에 노출되므로 안전망.
  - 댓글: WP comments REST 404 + 본문 페이지의 commentCount=0 다수 → 본문만 수집.
  - 시각: WordPress `date_gmt` (UTC naive) → tz=UTC 부여. RSS pubDate 는 RFC822.
  - region=AU (호주 시각 보강 필요 시 AEST=+10:00).
"""
import hashlib
import html as html_lib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Tuple
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://ausdroid.net"

# WP REST API — Samsung 태그 id 13501 (확인됨). Galaxy 관련 slug 들도 병합.
# (slug → 태그 RSS 폴백용 / WP API 는 우선 알려진 id 로 수집)
AUSDROID_TAG_IDS: List[Tuple[int, str]] = [
    (13501, "samsung"),
]

# RSS 폴백 + 보강 — 동일 컨텐츠가 다른 태그로도 노출
AUSDROID_TAG_SLUGS: List[str] = [
    "samsung",
    "galaxy-s24",
    "galaxy-s25",
    "galaxy-watch",
    "oneui",
]

# WP REST 페이지당 100건 (max), 최대 N 페이지
JSON_PER_PAGE = 100
JSON_MAX_PAGES = 3  # tag=samsung 약 105건 → 2페이지면 충분, 여유 1
# 최종 처리 캡
MAX_POSTS = 150

NS = {"dc": "http://purl.org/dc/elements/1.1/"}

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class AusdroidCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "ausdroid", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-AU,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) WP REST API 로 태그별 페이지네이션 수집
            for tag_id, tag_name in AUSDROID_TAG_IDS:
                for page in range(1, JSON_MAX_PAGES + 1):
                    try:
                        posts = await self._fetch_wp_json(client, tag_id, page)
                        if not posts:
                            break  # 다음 페이지 없음
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        items.extend(filtered)
                        logger.info(
                            f"  Ausdroid WP-JSON [{tag_name} p{page}]: {len(filtered)}/{len(posts)}건"
                        )
                        # 받은 페이지가 per_page 보다 작으면 마지막
                        if len(posts) < JSON_PER_PAGE:
                            break
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  Ausdroid WP-JSON [{tag_name} p{page}] 실패: {e}"
                        )
                        break  # 4xx 등 발생 시 다음 페이지로 진행 의미 없음

            # 2) RSS 폴백 — WP-JSON 캐시 미스 / 최신 글 보강
            for slug in AUSDROID_TAG_SLUGS:
                try:
                    posts = await self._fetch_rss(client, slug)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  Ausdroid RSS [{slug}]: {len(filtered)}/{len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Ausdroid RSS [{slug}] 실패: {e}")

        # link 단위 중복 제거 (WP-JSON 우선 — 본문 더 풍부)
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
            f"Ausdroid 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    # ---- WP REST API ----

    async def _fetch_wp_json(
        self, client: httpx.AsyncClient, tag_id: int, page: int
    ) -> List[RawVOC]:
        # content.rendered 필드는 서버 측 DB 과부하로 간헐적 500 → excerpt 만 사용.
        # RSS description 과 동일한 lead 가 들어와 본문으로 충분.
        url = (
            f"{BASE_URL}/wp-json/wp/v2/posts"
            f"?tags={tag_id}&per_page={JSON_PER_PAGE}&page={page}"
            f"&_fields=id,date_gmt,link,title,excerpt"
        )
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/json, */*;q=0.8",
            },
        )
        # page 가 데이터 끝을 초과하면 WP 는 400/404 를 줄 수 있음
        if resp.status_code in (400, 404):
            return []
        resp.raise_for_status()
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            logger.warning(f"Ausdroid WP-JSON 파싱 실패: {e}")
            return []
        if not isinstance(data, list):
            return []

        results: List[RawVOC] = []
        for item in data:
            try:
                voc = self._parse_wp_item(item)
                if voc:
                    results.append(voc)
            except Exception as e:
                logger.debug(f"Ausdroid WP item 파싱 실패: {e}")
        return results

    def _parse_wp_item(self, item: dict) -> Optional[RawVOC]:
        link = (item.get("link") or "").strip()
        if not link:
            return None
        post_id = item.get("id")
        title = self._strip_html((item.get("title") or {}).get("rendered", ""))
        # excerpt.rendered = 짧은 lead (RSS description 과 동일 수준)
        excerpt_html = (item.get("excerpt") or {}).get("rendered", "")
        body = self._strip_html(excerpt_html, limit=4000)
        if not title and not body:
            return None

        # date_gmt: 'YYYY-MM-DDTHH:MM:SS' (naive UTC)
        published_at = self._parse_wp_date(item.get("date_gmt"))

        external_id = hashlib.md5(
            f"{link}#{post_id}".encode()
        ).hexdigest()[:16]

        content = f"{title}\n{body}".strip() if body else title

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=link,
            author_name=None,  # author 는 id 만 → 별도 API 필요. 생략.
            published_at=published_at,
            country_code="AU",
            meta={"post_id": post_id, "source": "wp-json"},
        )

    # ---- RSS ----

    async def _fetch_rss(
        self, client: httpx.AsyncClient, slug: str
    ) -> List[RawVOC]:
        url = f"{BASE_URL}/tag/{slug}/feed/"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Ausdroid RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                guid = (item.findtext("guid") or link).strip()
                desc_raw = item.findtext("description") or ""
                desc = self._strip_html(desc_raw, limit=4000)

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                # guid 가 ?p=NNNN 형태 → 안정적 post id 추출
                m = re.search(r"\?p=(\d+)", guid)
                post_id = m.group(1) if m else hashlib.md5(link.encode()).hexdigest()[:8]

                external_id = hashlib.md5(
                    f"{link}#{post_id}".encode()
                ).hexdigest()[:16]

                content = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="AU",
                    meta={"post_id": post_id, "guid": guid, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"Ausdroid RSS item 파싱 실패: {e}")

        return results

    # ---- helpers ----

    def _strip_html(self, html_text: str, limit: int = 0) -> str:
        if not html_text:
            return ""
        # WP 본문은 <p>/<h3>/<img>... 등 풍부 → BS4 로 텍스트만
        soup = BeautifulSoup(html_text, "html.parser")
        # 푸터 "The post ... appeared first on" 류 제거 (RSS description 에 흔함)
        for a in soup.find_all("a"):
            if a.get_text(strip=True).lower().startswith("the post "):
                a.decompose()
        text = soup.get_text(" ", strip=True)
        text = html_lib.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        # 자주 등장하는 footer 문구 제거
        text = re.sub(
            r"The post .+? appeared first on .+?\.?$", "", text
        ).strip()
        if limit and len(text) > limit:
            text = text[:limit]
        return text

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_wp_date(self, text: Optional[str]) -> Optional[datetime]:
        """WordPress date_gmt 'YYYY-MM-DDTHH:MM:SS' (naive UTC) → aware UTC."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Wed, 22 Jan 2025 18:00:17 +0000' → UTC datetime."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
