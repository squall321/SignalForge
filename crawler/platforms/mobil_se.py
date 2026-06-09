"""
Mobil.se 크롤러 — httpx + tagg 페이지 + JSON-LD NewsArticle

mobil.se (스웨덴 모바일 전문지, sv-SE, Square Publishing/CloudFront 운영) 의
Samsung/Galaxy 관련 기사 본문 수집.

전략
  - 댓글 시스템 없음 (kommentar/disqus/comments 0건) → 본문 전용.
  - 정규 태그 경로는 /tagg/<slug> (영어 /tag/ 가 아닌 스웨덴어 /tagg/).
    /tagg/samsung 한 페이지에 ItemList JSON-LD 로 정확히 50개 기사 노출.
    /tagg/samsung/2 같은 페이지네이션 URL 은 200을 돌려주지만 동일 50건 →
    pagination 미동작으로 판단하고 단일 페이지로 충분히 채울 수 있음.
  - 보조 시드로 카테고리 페이지 (nyheter, tips-och-tricks, produkttester,
    reportage, jamforande-tester) 의 최신글에서 samsung 키워드 일치분도 함께
    수집. 단, /tagg/samsung 만으로 50건 확보가 가능하므로 보조 시드는 부족
    시에만 사용.
  - 기사 페이지는 `<section class="main article k5a-article">` 본문 + JSON-LD
    NewsArticle (headline / description / datePublished / author) 보유.
    datePublished 는 UTC 명시 (예: 2026-06-01T19:22:41.000Z). naive 폴백은
    스웨덴 로컬 CET/CEST 가정 후 UTC 변환.
  - CloudFront/Varnish 캐시 사이트라 동일 UA/Referer 로도 안정. 403/Bot
    차단은 미관측. 그래도 안전을 위해 Firefox UA + Accept-Language sv-SE 명시.
  - LIST_PAGES=12 / MAX_POSTS=150 contract 준수 (실제 가용 ~50 이지만 변수
    형식만 유지).
"""
import asyncio
import hashlib
import html as html_lib
import json
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

BASE_URL = "https://www.mobil.se"

# 주 시드 (/tagg/samsung) + 보조 카테고리 시드 (samsung 키워드만 통과)
SEED_URLS = [
    f"{BASE_URL}/tagg/samsung",
    f"{BASE_URL}/nyheter",
    f"{BASE_URL}/tips-och-tricks",
    f"{BASE_URL}/produkttester",
    f"{BASE_URL}/jamforande-tester",
    f"{BASE_URL}/reportage",
]

LIST_PAGES = 12
MAX_POSTS = 150

# 기사 URL 패턴: /<섹션>/<슬러그>/<숫자id>
ARTICLE_URL_RE = re.compile(
    r"https?://www\.mobil\.se/"
    r"(?:nyheter|tips-och-tricks|produkttester|reportage|kronikor|"
    r"jamforande-tester|guider|kronika|test)"
    r"/[a-z0-9-]+/(\d{6,8})(?:/)?$"
)
ARTICLE_ID_FROM_URL = re.compile(r"/(\d{6,8})(?:/)?$")

# 스웨덴 로컬 (CET=+01, CEST=+02). naive 폴백용 단순화: CET 고정.
# datePublished 는 거의 항상 UTC(...Z) 라 폴백 진입 가능성 낮음.
CET = timezone(timedelta(hours=1))

GALAXY_KEYWORDS = [
    r"\bsamsung\b", r"\bgalaxy\b",
    r"\bs2[3-9]\b", r"\bs3[0-9]\b",
    r"\bz\s*fold\b", r"\bz\s*flip\b",
    r"\bgalaxy\s+(fold|flip|ultra|tab|buds|watch|ring)\b",
    r"\bone\s*ui\b", r"\boneui\b", r"\bexynos\b", r"\bbixby\b",
]
GALAXY_PATTERN = re.compile(r"|".join(GALAXY_KEYWORDS), re.IGNORECASE)


class MobilSeCrawler(BaseCrawler):
    MIN_DELAY = 1.2
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "mobil_se", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_urls: set = set()
        seen_external_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers.update({
                "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.6",
                "Accept-Encoding": "gzip, deflate",
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })

            # 1) 시드별로 ItemList 또는 본문 링크 수집
            candidate_urls: List[str] = []
            cand_seen: set = set()
            for seed in SEED_URLS:
                urls = await self._collect_from_seed(client, seed)
                new = 0
                for u in urls:
                    if u in cand_seen:
                        continue
                    cand_seen.add(u)
                    candidate_urls.append(u)
                    new += 1
                logger.info(
                    f"  Mobil.se 시드 {seed.split('/')[-1] or 'root'}: "
                    f"{len(urls)} URL / {new} 신규"
                )
                if len(candidate_urls) >= MAX_POSTS:
                    break
                await self._random_delay()

            logger.info(f"  Mobil.se 후보 URL: {len(candidate_urls)}건")

            # 2) 각 기사 fetch → JSON-LD + 본문 추출 → 키워드 필터
            for url in candidate_urls[:MAX_POSTS * 2]:  # 키워드 필터로 떨어질 분을 고려해 여유
                if url in seen_urls:
                    continue
                try:
                    voc = await self._fetch_article(client, url)
                    if voc is None:
                        continue
                    if not self._is_galaxy_related(voc):
                        continue
                    if voc.external_id in seen_external_ids:
                        continue
                    seen_urls.add(url)
                    seen_external_ids.add(voc.external_id)
                    items.append(voc)
                    if len(items) >= MAX_POSTS:
                        break
                except Exception as e:
                    logger.debug(f"  Mobil.se 기사 파싱 실패 {url}: {e}")
                await self._random_delay()

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"Mobil.se 수집 완료: {len(result)}건")
        return result

    async def _collect_from_seed(
        self, client: httpx.AsyncClient, seed_url: str
    ) -> List[str]:
        """시드 페이지에서 기사 URL 후보 수집.
        JSON-LD ItemList 가 있으면 그것을 우선 사용 (정확도 ↑),
        없으면 a[href] 정규식 매칭으로 폴백."""
        try:
            resp = await client.get(seed_url)
            if resp.status_code != 200:
                logger.debug(f"  Mobil.se seed HTTP {resp.status_code}: {seed_url}")
                return []
            html = resp.text
        except Exception as e:
            logger.debug(f"  Mobil.se seed fetch 실패 {seed_url}: {e}")
            return []

        urls: List[str] = []

        # ItemList 우선
        for ld in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL,
        ):
            try:
                data = json.loads(ld.strip())
            except Exception:
                continue
            blocks = data if isinstance(data, list) else [data]
            for b in blocks:
                main = b.get("mainEntity") if isinstance(b, dict) else None
                if isinstance(main, dict) and main.get("@type") == "ItemList":
                    for it in main.get("itemListElement", []) or []:
                        item = (it or {}).get("item") if isinstance(it, dict) else None
                        if isinstance(item, dict):
                            u = item.get("url") or ""
                            if u and ARTICLE_URL_RE.match(u):
                                urls.append(u)

        # a[href] 폴백 (ItemList 가 없는 카테고리 페이지)
        if not urls:
            for m in re.finditer(
                r'href="(/(?:nyheter|tips-och-tricks|produkttester|reportage|'
                r'kronikor|jamforande-tester|guider)/[a-z0-9-]+/\d{6,8})"',
                html,
            ):
                urls.append(BASE_URL + m.group(1))
            for m in re.finditer(
                r'href="(https://www\.mobil\.se/(?:nyheter|tips-och-tricks|'
                r'produkttester|reportage|kronikor|jamforande-tester|guider)'
                r'/[a-z0-9-]+/\d{6,8})"',
                html,
            ):
                urls.append(m.group(1))

        # 중복 제거 (순서 유지)
        seen: set = set()
        deduped: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[RawVOC]:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.debug(f"  Mobil.se article HTTP {resp.status_code}: {url}")
                return None
            html = resp.text
        except Exception as e:
            logger.debug(f"  Mobil.se article fetch 실패 {url}: {e}")
            return None

        # JSON-LD NewsArticle 추출
        meta: Dict = {}
        for ld in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL,
        ):
            try:
                data = json.loads(ld.strip())
            except Exception:
                continue
            blocks = data if isinstance(data, list) else [data]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("@type") in ("NewsArticle", "Article"):
                    meta = b
                    break
            if meta:
                break

        if not meta:
            return None

        title = self._clean_text(meta.get("headline") or "")
        description = self._clean_text(meta.get("description") or "")

        # 본문 추출: section.k5a-article 안의 <p>, <h2>, <h3>
        body_text = self._extract_body(html)

        # 본문이 비면 description 만이라도 채움
        if not body_text:
            body_text = description

        # 본문이 너무 짧으면 description 합쳐서 보완
        full_parts: List[str] = []
        if title:
            full_parts.append(title)
        if description and description not in body_text:
            full_parts.append(description)
        if body_text:
            full_parts.append(body_text)
        full = "\n".join(full_parts).strip()

        if len(full) < 30:
            return None
        if len(full) > 6000:
            full = full[:6000]

        published_at = self._parse_dt(meta.get("datePublished"))
        author = None
        authors = meta.get("author")
        if isinstance(authors, list) and authors:
            a0 = authors[0]
            if isinstance(a0, dict):
                author = self._clean_text(a0.get("name") or "") or None
        elif isinstance(authors, dict):
            author = self._clean_text(authors.get("name") or "") or None

        # external_id: URL 끝자리 숫자 ID 우선, 없으면 md5 hash
        m = ARTICLE_ID_FROM_URL.search(url)
        if m:
            external_id = f"post-{m.group(1)}"
        else:
            external_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full,
            source_url=meta.get("url") or url,
            author_name=author,
            published_at=published_at,
            country_code="SE",
            meta={
                "section": meta.get("articleSection") or "",
                "keywords": meta.get("keywords") or "",
                "source": "jsonld_article",
            },
        )

    @staticmethod
    def _extract_body(html: str) -> str:
        """section.k5a-article 안의 p/h2/h3 텍스트만 추출."""
        m = re.search(
            r'<section[^>]+class="[^"]*k5a-article[^"]*"[^>]*>(.*?)</section>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return ""
        block = m.group(1)
        # script/style/figure/aside/.image 제거
        block = re.sub(
            r"<(script|style|figure|aside|picture)[^>]*>.*?</\1>", " ",
            block, flags=re.DOTALL | re.IGNORECASE,
        )
        parts: List[str] = []
        for tag in re.finditer(
            r"<(p|h2|h3|li)[^>]*>(.*?)</\1>", block,
            re.DOTALL | re.IGNORECASE,
        ):
            txt = MobilSeCrawler._clean_text(tag.group(2))
            # 'Relaterade artiklar' 같은 푸터성 텍스트 컷
            if not txt:
                continue
            if txt.lower().startswith("relaterade artikl"):
                break
            parts.append(txt)
        return "\n".join(parts).strip()

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_PATTERN.search(text))

    @staticmethod
    def _clean_text(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        return re.sub(r"\s+", " ", no_tags).strip()

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        """ISO8601 datetime → UTC. Z 또는 offset 명시 정상 처리.
        naive 일 경우 CET 가정 후 UTC 변환."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CET)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
