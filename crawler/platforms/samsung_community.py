# Samsung Members(community.samsung.com) 공식 커뮤니티 — Khoros LiQL REST API 로 실사용 버그/문의 글 수집
"""
Samsung Members (community.samsung.com) 크롤러 — Khoros/Lithium LiQL REST API 기반.

2026-06 경 검색/RSS/HTML 엔드포인트(/t5/.../searchpage, /bd-p, 메인)가 전부 403 봇차단되어
3개월간 수집 정지됐다. 반면 공개 REST API `/api/2.0/search?q=<LiQL>` 는 200 으로 열려 있어
이걸로 전면 교체. 기기·재현스텝이 담긴 공식 버그신고라 '구체적 불량 현상'의 고신호 소스.

- 지역: 한국(r1) + 미국(us) 호스트, 모델별 쿼리 fan-out.
- LiQL: SELECT ... FROM messages WHERE body MATCHES '<query>' ORDER BY post_time DESC.
- published_at = post_time(원 작성일), body 는 HTML 이라 태그 제거. MX 관련성 필터 유지.

플랫폼 코드: samsung_community
"""
import hashlib
import html
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402
from nlp.mx_keywords import is_mx_relevant  # noqa: E402

logger = logging.getLogger(__name__)

# 지역별 호스트 + 국가 코드 + 검색 키워드(모델 토큰은 KR/US 게시글 양쪽에 등장).
REGIONS = [
    {
        # r1 은 KR 전용이 아니라 다국어 국제 클러스터(태국어·포르투갈어 글 섞임) → 국가 미지정.
        "host": "https://r1.community.samsung.com",
        "country": None,
        "queries": ["Galaxy S25", "Galaxy S24", "Galaxy Z Fold", "Galaxy Z Flip",
                    "Galaxy Watch", "Galaxy Buds", "One UI"],
    },
    {
        "host": "https://us.community.samsung.com",
        "country": "US",
        "queries": ["Galaxy S25", "Galaxy S24", "Galaxy Z Fold", "Galaxy Z Flip",
                    "Galaxy Watch", "Galaxy Buds", "One UI"],
    },
]

LIMIT_PER_QUERY = 50
# LiQL — messages 검색. body MATCHES 로 전문검색, 최신순.
_LIQL = ("SELECT id, subject, body, post_time, view_href, author.login, kudos.sum(weight) "
         "FROM messages WHERE body MATCHES '{q}' ORDER BY post_time DESC LIMIT {n}")


class SamsungCommunityCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "samsung_community", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        out: List[RawVOC] = []
        seen: set = set()
        async with self._make_httpx_client() as client:
            for region in REGIONS:
                for q in region["queries"]:
                    await self._random_delay()
                    try:
                        out += await self._search(client, region["host"],
                                                  region["country"], q, seen)
                    except (httpx.HTTPError, ValueError) as e:
                        logger.warning("  Samsung %s %r 실패: %r", region["country"], q, e)
        before = len(out)
        out = [v for v in out if is_mx_relevant(v.content)]
        logger.info("Samsung Community 수집 완료: %d/%d (MX 필터)", len(out), before)
        return out

    async def _search(self, client, host: str, country: str, q: str, seen: set) -> List[RawVOC]:
        liql = _LIQL.format(q=q.replace("'", " "), n=LIMIT_PER_QUERY)
        url = f"{host}/api/2.0/search?q=" + urllib.parse.quote(liql)
        r = await client.get(url)
        if r.status_code != 200:
            logger.warning("  Samsung %s %r status=%s", country, q, r.status_code)
            return []
        items = ((r.json().get("data") or {}).get("items")) or []
        res: List[RawVOC] = []
        for it in items:
            mid = str(it.get("id") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            subject = (it.get("subject") or "").strip()
            body = self._strip_html(it.get("body") or "")
            content = (f"{subject}\n{body}".strip() if subject else body)
            if not content or len(content) < 5:
                continue
            res.append(RawVOC(
                external_id=hashlib.md5(f"samsung_community#{mid}".encode()).hexdigest()[:16],
                content=content,
                source_url=it.get("view_href") or host,
                author_name=((it.get("author") or {}).get("login")),
                published_at=self._parse_dt(it.get("post_time")),
                likes_count=self._kudos(it),
                country_code=country,
                meta={"source": "samsung_members", "subject": subject},
            ))
        logger.info("  Samsung %s %r: %d건", country, q, len(res))
        return res

    @staticmethod
    def _strip_html(h: str) -> str:
        t = re.sub(r"<[^>]+>", " ", h)
        return re.sub(r"\s+", " ", html.unescape(t)).strip()

    @staticmethod
    def _kudos(it: dict) -> int:
        k = it.get("kudos")
        if not isinstance(k, dict):
            return 0
        try:
            return int((k.get("sum") or {}).get("weight") or 0)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            return None
