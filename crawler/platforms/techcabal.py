"""
TechCabal 크롤러 — httpx + WordPress REST API (en, 나이지리아 IT 미디어)

techcabal.com (나이지리아/범아프리카 IT, 영문, WordPress) 의 Samsung/Galaxy
관련 기사 본문 수집.

전략
  - WP REST /wp-json/wp/v2/posts 직타가 가장 안정적이며 200 응답.
    HTML 카테고리/태그 페이지도 200 이지만 Next.js SSR 가 아닌 클래식 WP
    테마라 본문이 HTML 에 있다. 다만 REST 가 content.rendered 를 통째로
    제공하므로 굳이 HTML 파싱 안 함.
  - 후보 수집 경로 (중복 제거 후 합산):
      1) tag id=1587 (Samsung, count=58) — /wp/v2/posts?tags[]=1587
      2) search=samsung — 추가 본문 매칭 (제목/본문)
      3) search=galaxy
  - 'X-WP-Total' 헤더가 0 으로 보고되지만 (필터 플러그인 영향) 실제 데이터는
    정상 반환. per_page=50, 페이지네이션은 빈 리스트가 나올 때까지 진행.
  - 본문은 처음 fetch 시 content.rendered 함께 받음 → 추가 호출 없음.
  - 댓글: 사이트 전역 comment_status='closed'. /wp/v2/comments 도 'rest_no_route'
    (404) 로 비공개. 본문 한 건 = 한 VOC. 향후 활성화되면 _fetch_comments
    추가하면 됨.
  - 시간: WordPress date_gmt 는 UTC (YYYY-MM-DDTHH:MM:SS naive). UTC 부여.
    누락 시 date(naive)는 WAT(+1) 가정 → UTC 변환.
  - 키워드 필터: 본문/제목에 samsung/galaxy/one ui 등 매칭. 'samsung tag'
    경유는 신뢰. search 경유는 본문 키워드 재확인.
"""
import asyncio
import hashlib
import html as html_lib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://techcabal.com"
API_BASE = f"{BASE_URL}/wp-json/wp/v2"

# 후보 풀
SAMSUNG_TAG_ID = 1587          # /wp/v2/tags?slug=samsung → count=58
SEARCH_TERMS = ["samsung", "galaxy"]

# WP REST 페이지네이션
PER_PAGE = 50
LIST_PAGES = 12                 # tag/search 당 최대 페이지
MAX_POSTS = 150

# 나이지리아 표준시 — WAT (UTC+1, DST 없음). date_gmt 가 있으면 미사용.
WAT = timezone(timedelta(hours=1))

GALAXY_KEYWORD_RE = re.compile(
    r"(samsung|galaxy|one ?ui|exynos|bixby|s2[3-7]|note ?\d{1,2}|"
    r"fold ?\d?|flip ?\d?|tab ?s\d|buds|watch ?\d?)",
    re.IGNORECASE,
)


class TechCabalCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "techcabal", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()        # WP post id 중복 방지
        seen_external_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers.update({
                "Accept-Language": "en-NG,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": BASE_URL + "/",
                "Accept": "application/json, */*;q=0.8",
            })

            # 1) Samsung 태그 (count=58, 가장 정밀)
            tag_posts = await self._list_posts(
                client,
                params={"tags[]": str(SAMSUNG_TAG_ID)},
                label=f"tag={SAMSUNG_TAG_ID}",
                require_keyword=False,   # 태그 신뢰
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
            logger.info(f"  TechCabal tag=samsung: {len(items)}건")

            # 2) Search 보강 (samsung / galaxy)
            for term in SEARCH_TERMS:
                pre = len(items)
                s_posts = await self._list_posts(
                    client,
                    params={"search": term},
                    label=f"search={term}",
                    require_keyword=True,
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
                    f"  TechCabal search={term}: +{len(items) - pre} 신규"
                )

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"TechCabal 수집 완료: {len(result)}건")
        return result

    async def _list_posts(
        self,
        client: httpx.AsyncClient,
        params: dict,
        label: str,
        require_keyword: bool,
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
                    # rest_post_invalid_page_number — 끝
                    break
                if resp.status_code != 200:
                    logger.debug(
                        f"  TechCabal {label} page={page} HTTP {resp.status_code}"
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
                logger.debug(f"  TechCabal {label} page={page} 실패: {e}")
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
            author_name=None,         # _embed=author 가 _fields 와 함께 안 옴
            published_at=published_at,
            country_code="NG",
            meta={
                "post_id": pid,
                "comment_status": post.get("comment_status"),
                "source": "wp_rest",
            },
        )

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
        naive_is_utc=True 면 UTC, False 면 WAT(+1) → UTC 변환."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if naive_is_utc:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.replace(tzinfo=WAT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
