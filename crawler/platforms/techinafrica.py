"""
Tech In Africa 크롤러 — httpx + WordPress REST API + 검색 RSS 폴백

techinafrica.com (케냐/범아프리카 IT 영문, WordPress 6.6.5, Cloudflare) 의
Samsung/Galaxy 관련 기사 본문 수집.

전략
  - 메인 사이트는 Cloudflare 가 떠 있으나, 일반 UA 로도 wp-json / feed 모두
    HTTP 200 으로 응답. JS 챌린지 없음.
  - 후보 수집 경로 (중복 제거 후 합산):
      1) tag id=1697 (Samsung, count=9) — /wp-json/wp/v2/posts?tags[]=1697
      2) search=samsung — /wp-json/wp/v2/posts?search=samsung
      3) search=galaxy  — /wp-json/wp/v2/posts?search=galaxy
      4) 폴백: /?s=samsung&feed=rss2 — WP REST 가 403 일 때 검색 RSS
  - 본문은 content.rendered 에 전문 포함 → 추가 호출 없음.
  - 댓글: 사이트 전반 #respond (댓글 0), /wp/v2/comments 빈 배열. 본문 한 건
    = 한 VOC. (참고로 techcabal 동일 정책)
  - 시간: WordPress date_gmt 가 UTC naive. UTC 부여. 누락 시 date(naive) 는
    EAT(UTC+3) 케냐 표준시 가정 → UTC 변환.
  - 키워드 필터: 본문/제목 매칭. samsung 태그 경유는 신뢰. search 경유는 본문
    키워드 재확인 (M-KOPA, smartphone financing 등 잡음 컷).
"""
import hashlib
import html as html_lib
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

BASE_URL = "https://www.techinafrica.com"
API_BASE = f"{BASE_URL}/wp-json/wp/v2"
SEARCH_RSS_URL = f"{BASE_URL}/?s={{term}}&feed=rss2"

# 검증된 Samsung 태그 ID (count=9)
SAMSUNG_TAG_ID = 1697
SEARCH_TERMS = ["samsung", "galaxy"]

# WP REST 페이지네이션 — Samsung 태그가 작아 5~6 페이지면 충분
PER_PAGE = 50
LIST_PAGES = 12
MAX_POSTS = 150

# 케냐 표준시 EAT (UTC+3, DST 없음). date_gmt 있으면 미사용.
EAT = timezone(timedelta(hours=3))

# WordPress RSS 네임스페이스 (폴백 경로용)
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

GALAXY_KEYWORD_RE = re.compile(
    r"(samsung|galaxy|one ?ui|exynos|bixby|s2[3-7]|note ?\d{1,2}|"
    r"fold ?\d?|flip ?\d?|tab ?s\d|buds|watch ?\d?)",
    re.IGNORECASE,
)


class TechInAfricaCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "techinafrica", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()                # WP post id
        seen_external_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers.update({
                "Accept-Language": "en-KE,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": BASE_URL + "/",
                "Accept": "application/json, */*;q=0.8",
            })

            # 1) Samsung 태그 (신뢰 — 필터 없이 수용)
            tag_posts = await self._list_posts(
                client,
                params={"tags[]": str(SAMSUNG_TAG_ID)},
                label=f"tag={SAMSUNG_TAG_ID}",
            )
            for p in tag_posts:
                pid = p.get("id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                voc = self._parse_post(p)
                if voc and voc.external_id not in seen_external_ids:
                    seen_external_ids.add(voc.external_id)
                    items.append(voc)
            logger.info(f"  TechInAfrica tag=samsung: {len(items)}건")

            # 2) Search 보강 (samsung / galaxy) — 본문 키워드 재확인
            wp_total = len(items)
            for term in SEARCH_TERMS:
                pre = len(items)
                s_posts = await self._list_posts(
                    client,
                    params={"search": term},
                    label=f"search={term}",
                )
                for p in s_posts:
                    pid = p.get("id")
                    if pid in seen_ids:
                        continue
                    voc = self._parse_post(p)
                    if not voc:
                        continue
                    if not GALAXY_KEYWORD_RE.search(voc.content):
                        continue
                    if voc.external_id in seen_external_ids:
                        continue
                    seen_ids.add(pid)
                    seen_external_ids.add(voc.external_id)
                    items.append(voc)
                logger.info(
                    f"  TechInAfrica search={term}: +{len(items) - pre} 신규"
                )

            # 3) WP REST 가 전혀 못 가져왔으면 검색 RSS 폴백 (Firefox UA 로 재시도)
            if len(items) == 0:
                logger.warning("  TechInAfrica WP REST 0건 → 검색 RSS 폴백")
                client.headers["User-Agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                    "Gecko/20100101 Firefox/125.0"
                )
                for term in SEARCH_TERMS:
                    rss_items = await self._fetch_search_rss(client, term)
                    for v in rss_items:
                        if v.external_id in seen_external_ids:
                            continue
                        if not GALAXY_KEYWORD_RE.search(v.content):
                            continue
                        seen_external_ids.add(v.external_id)
                        items.append(v)
                    logger.info(
                        f"  TechInAfrica RSS search={term}: 누적 {len(items)}건"
                    )

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"TechInAfrica 수집 완료: {len(result)}건")
        return result

    # --- WP REST ---

    async def _list_posts(
        self,
        client: httpx.AsyncClient,
        params: dict,
        label: str,
    ) -> List[dict]:
        """WP /wp/v2/posts 페이지네이션 — content/title 함께 받아 추가 호출 없음."""
        out: List[dict] = []
        for page in range(1, LIST_PAGES + 1):
            try:
                q = dict(params)
                q.update({
                    "per_page": PER_PAGE,
                    "page": page,
                    "_fields": "id,date,date_gmt,link,title,content,comment_status",
                })
                resp = await client.get(f"{API_BASE}/posts", params=q)
                if resp.status_code == 400:
                    # rest_post_invalid_page_number — 페이지 끝
                    break
                if resp.status_code != 200:
                    logger.debug(
                        f"  TechInAfrica {label} page={page} HTTP {resp.status_code}"
                    )
                    break
                data = resp.json()
                if not isinstance(data, list) or not data:
                    break
                out.extend(data)
                if len(data) < PER_PAGE:
                    break
                await self._random_delay()
            except Exception as e:
                logger.debug(f"  TechInAfrica {label} page={page} 실패: {e}")
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

        if len(body) > 4000:
            body = body[:4000]

        full = f"{title}\n{body}".strip() if body else title
        if len(full) < 30:
            return None

        published_at = self._parse_dt(
            post.get("date_gmt"), naive_is_utc=True
        ) or self._parse_dt(post.get("date"), naive_is_utc=False)

        external_id = hashlib.md5(
            f"{link}#post-{pid}".encode("utf-8")
        ).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full,
            source_url=link,
            author_name=None,
            published_at=published_at,
            country_code="KE",
            meta={
                "post_id": pid,
                "comment_status": post.get("comment_status"),
                "source": "wp_rest",
            },
        )

    # --- 검색 RSS 폴백 ---

    async def _fetch_search_rss(
        self, client: httpx.AsyncClient, term: str
    ) -> List[RawVOC]:
        url = SEARCH_RSS_URL.format(term=term)
        try:
            resp = await client.get(
                url,
                headers={
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                    "Referer": BASE_URL + "/",
                },
            )
            if resp.status_code != 200:
                logger.debug(
                    f"  TechInAfrica RSS search={term} HTTP {resp.status_code}"
                )
                return []
            return self._parse_rss(resp.text)
        except Exception as e:
            logger.debug(f"  TechInAfrica RSS search={term} 실패: {e}")
            return []

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"TechInAfrica RSS 파싱 실패: {e}")
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

                content_enc = item.findtext(
                    "content:encoded", default="", namespaces=NS
                )
                body = self._strip_html(content_enc)
                if not body:
                    body = self._strip_html(item.findtext("description") or "")
                if len(body) > 4000:
                    body = body[:4000]

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 30:
                    continue

                published_at = self._parse_rss_date(item.findtext("pubDate") or "")
                author = item.findtext(
                    "dc:creator", default="", namespaces=NS
                ).strip() or None

                external_id = hashlib.md5(
                    f"{link}#post-{post_id}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="KE",
                    meta={
                        "post_id": post_id,
                        "source": "rss_search",
                    },
                ))
            except Exception as e:
                logger.debug(f"TechInAfrica RSS item 파싱 실패: {e}")

        return results

    # --- helpers ---

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        if not guid:
            return None
        m = re.search(r"[?&]p=(\d+)", guid)
        if m:
            return m.group(1)
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
    def _parse_dt(value: Optional[str], naive_is_utc: bool) -> Optional[datetime]:
        """WordPress 'YYYY-MM-DDTHH:MM:SS' (naive) 파싱.
        naive_is_utc=True 면 UTC, False 면 EAT(+3) → UTC 변환."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if naive_is_utc:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.replace(tzinfo=EAT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_rss_date(text: str) -> Optional[datetime]:
        """RFC822 'Fri, 29 May 2026 21:38:01 +0000' → UTC. naive 면 EAT 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=EAT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
