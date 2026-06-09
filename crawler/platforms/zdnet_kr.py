"""
ZDNet Korea 크롤러 — 검색 페이지 + 기사 OG meta (한국, IT 뉴스)

zdnet.co.kr 은 한국 1세대 IT 매체 (메가뉴스). RSS (https://zdnet.co.kr/feed) 는
전체 카테고리(삼성 외 일반 뉴스 다수) 라 키워드 필터링 비효율.

전략
  - /search.html?word=galaxy + word=samsung 두 검색 결과 페이지에서 article id
    (view/?no=YYYYMMDDHHMMSS) 추출 — 페이지당 ~20건
  - 각 기사 페이지 → OG meta (og:title, og:description) +
    article:published_time meta 로 본문/시간 추출
  - external_id: no (= YYYYMMDDHHMMSS) → md5 16자
  - country_code="KR"
  - 댓글: 별도 시스템 없음 (zdnet 댓글 폐지). 본문 한 건 = 한 VOC.

회고
  - "그래픽카드(GPU)"의 'galaxy' 브랜드 (GALAX) 오탐 가능 — 키워드 필터에서
    'galaxy' 단독은 허용하되 'galax (brand)' 류는 negative hint 없이 진행
    (대부분 갤럭시 폰 관련 기사가 대다수)
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional, Set
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE = "https://zdnet.co.kr"
SEARCH_URL = f"{BASE}/search.html?word={{kw}}"
ARTICLE_URL = f"{BASE}/view/?no={{no}}"

# 키워드 (한글 + 영문 병행)
SEARCH_TERMS = ["galaxy", "갤럭시", "삼성"]

# 검색 1쿼리당 최대 기사 수
MAX_PER_TERM = 30
MAX_POSTS = 90

# 기사 ID 정규식 (view/?no=20260605100411)
ARTICLE_ID_RE = re.compile(r"view/\?no=(\d{14})")

# OG meta 추출
OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I
)
OG_DESC_RE = re.compile(
    r'<meta\s+property="og:description"\s+content="([^"]+)"', re.I
)
PUB_TIME_RE = re.compile(
    r'<meta\s+property="article:published_time"\s+content="([^"]+)"', re.I
)
AUTHOR_RE = re.compile(
    r'<meta\s+property="article:author"\s+content="([^"]+)"', re.I
)

# Galaxy/Samsung 키워드 (오탐 컷)
GALAXY_KEYWORD_RE = re.compile(
    r"(samsung|galaxy|갤럭시|삼성|폴드|플립|원ui|one ?ui|exynos|엑시노스)",
    re.I,
)


class ZDNetKoreaCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "zdnet_kr", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: Set[str] = set()

        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "ko-KR,ko;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )

            # 1) 검색 페이지에서 article id 수집
            all_ids: List[str] = []
            for term in SEARCH_TERMS:
                try:
                    ids = await self._collect_article_ids(client, term)
                    new = [i for i in ids if i not in seen_ids][:MAX_PER_TERM]
                    seen_ids.update(new)
                    all_ids.extend(new)
                    logger.info(
                        f"  ZDNet KR search '{term}': {len(ids)} 결과 / "
                        f"{len(new)} 신규"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  ZDNet KR search '{term}' 실패: {e}")

            # 2) 각 기사 → RawVOC
            for no in all_ids[:MAX_POSTS]:
                try:
                    voc = await self._fetch_article(client, no)
                    if voc is None:
                        continue
                    if not self._is_galaxy_related(voc):
                        continue
                    items.append(voc)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  ZDNet KR no={no} 실패: {e}")

        # 최신순 정렬
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        logger.info(f"ZDNet KR 수집 완료: {len(items)}건 (candidates {len(all_ids)})")
        return items

    # ---------- fetchers ----------

    async def _collect_article_ids(
        self, client: httpx.AsyncClient, term: str
    ) -> List[str]:
        url = SEARCH_URL.format(kw=term)
        resp = await client.get(url)
        # ZDNet 검색 페이지는 HTTP 404 를 반환하지만 본문은 정상 결과 페이지
        # (서버 측 라우팅 quirk). 본문만으로 ID 추출.
        if resp.status_code not in (200, 404):
            logger.debug(
                f"  ZDNet KR search '{term}' HTTP {resp.status_code}"
            )
            return []
        ids = ARTICLE_ID_RE.findall(resp.text)
        # 중복 제거 + 순서 유지
        seen: Set[str] = set()
        out: List[str] = []
        for i in ids:
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
        return out

    async def _fetch_article(
        self, client: httpx.AsyncClient, no: str
    ) -> Optional[RawVOC]:
        url = ARTICLE_URL.format(no=no)
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return self._parse_article(no, url, resp.text)

    # ---------- parsers ----------

    def _parse_article(
        self, no: str, url: str, html: str
    ) -> Optional[RawVOC]:
        title_m = OG_TITLE_RE.search(html)
        desc_m = OG_DESC_RE.search(html)
        pub_m = PUB_TIME_RE.search(html)
        author_m = AUTHOR_RE.search(html)

        title = self._unescape(title_m.group(1)) if title_m else ""
        desc = self._unescape(desc_m.group(1)) if desc_m else ""

        # 제목 trailing site 시그니처 제거
        title = re.sub(r"\s*[-|]\s*ZDNet\s*Korea\s*$", "", title, flags=re.I)
        title = title.strip()

        content = f"{title}\n{desc}".strip()
        if len(content) < 20:
            return None

        published_at = self._parse_iso(pub_m.group(1)) if pub_m else None
        author = author_m.group(1) if author_m else None

        external_id = hashlib.md5(f"zdnet_kr#{no}".encode()).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="KR",
            meta={
                "article_no": no,
                "source": "zdnet_kr_search",
            },
        )

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    @staticmethod
    def _unescape(s: str) -> str:
        import html as html_lib
        return html_lib.unescape(s or "").strip()

    @staticmethod
    def _parse_iso(text: Optional[str]) -> Optional[datetime]:
        """ISO 8601 'YYYY-MM-DDTHH:MM:SS+09:00' → UTC."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                # KST 가정
                from datetime import timedelta
                kst = timezone(timedelta(hours=9))
                dt = dt.replace(tzinfo=kst)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
