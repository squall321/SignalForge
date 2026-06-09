"""
Gizmodo Japan 크롤러 — httpx + Next.js __NEXT_DATA__ (JSON)

www.gizmodo.jp 은 Next.js SSG/ISR 사이트.
  - /tag/galaxy/ 같은 태그 페이지는 404.
  - /search?q=Galaxy 검색 페이지는 200 OK 이며 SSR 결과의
    `<script id="__NEXT_DATA__" type="application/json">` 안에
    pageProps.articles[] (각 글 메타) + pageInfo {currentPage, perPage, totalCount}
    형태로 검색 결과를 그대로 노출.
  - 상세 기사 페이지(/article/<slug>/) 도 __NEXT_DATA__ 의
    pageProps.data 에 title/released_at/tags/collaborators/body 가 들어있음.
    body 는 JSON-encoded Draft.js 포맷({blocks:[{type,text,...}],entityMap}).
  - 기사 댓글 시스템은 없음(Disqus/Coral 등 발견되지 않음) → 본문만 수집.

전략 (PhoneArena/MacRumors RSS-only 패턴 + JSON 파싱)
  - "Galaxy" + "Samsung" 두 키워드로 검색, 각 페이지 16건씩 LIST_PAGES 만큼 순회.
  - 검색은 SSR/JSON 이라 Cloudflare 우회 부담 적음, UA/Referer 만 일반 브라우저 흉내.
  - 후보 URL → 상세 페이지 fetch → Draft.js body 의 text 블록만 합쳐 본문화.
  - released_at 은 'YYYY-MM-DDTHH:MM:SSZ' 형식(이미 UTC).
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gizmodo.jp"
SEARCH_URL = "{base}/search?q={q}{page}"

# 두 키워드로 검색 후 URL 단위 중복 제거. 일본어/영문 모두 'Galaxy' / 'サムスン' 표기가
# 영문 라틴 그대로 들어가는 경우가 대부분이라 영문 키워드가 더 적중률 높음.
SEARCH_QUERIES = [
    "Galaxy",
    "Samsung",
]

# 페이지 수 — 검색 페이지당 16건. LIST_PAGES=12 → 키워드당 최대 192 후보 × 2.
LIST_PAGES = 12
# 최종 본문 수집 캡 (MacRumors/PhoneArena 와 동일)
MAX_POSTS = 150

# Draft.js block.text 합칠 때 컷오프 (지나치게 긴 longform 방지)
MAX_BODY_CHARS = 4000

# 제목/태그/본문 어디든 매치되면 OK. 일본어 표기 + 영문 모두 포함.
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "ギャラクシー", "サムスン",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra", "buds", "watch", "tab",
    "one ui", "oneui", "exynos", "bixby",
    "フォールド", "フリップ", "ウルトラ", "バッズ", "ウォッチ",
]


class GizmodoJPCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "gizmodo_jp", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # 일본어 사이트 — UTF-8 응답. 'br' 미지원이라 gzip/deflate 만 advertise.
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept-Language"] = "ja,en-US;q=0.9,en;q=0.8"

            # 1) 검색 키워드 × 페이지 순회로 메타 후보 수집
            for q in SEARCH_QUERIES:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        metas = await self._fetch_search(client, q, page)
                        if not metas:
                            # 마지막 페이지 도달 — 다음 키워드로
                            logger.info(
                                f"  GizmodoJP search [{q}] p{page}: 0건 (끝)"
                            )
                            break
                        filtered = [m for m in metas if self._is_galaxy_related(m)]
                        candidates.extend(filtered)
                        logger.info(
                            f"  GizmodoJP search [{q}] p{page}: {len(filtered)}/{len(metas)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  GizmodoJP search [{q}] p{page} 실패: {e}"
                        )

            # 2) URL 단위 중복 제거 (두 키워드에 동시 등장하는 글)
            seen: set = set()
            unique: List[RawVOC] = []
            for m in candidates:
                if m.source_url in seen:
                    continue
                seen.add(m.source_url)
                unique.append(m)

            # 3) 최신순 → 상위 MAX_POSTS 본문 보강
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = unique[:MAX_POSTS]
            logger.info(
                f"GizmodoJP 후보 {len(candidates)} → 고유 {len(unique)} → 본문 보강 {len(target)}건"
            )

            # 4) 상세 페이지 fetch → Draft.js body 텍스트 합치기
            enriched: List[RawVOC] = []
            for it in target:
                await self._random_delay()
                try:
                    body_text = await self._fetch_article_body(client, it.source_url)
                    if body_text:
                        # 제목은 메타 단계에서 이미 content 에 들어 있음
                        # → "title\n\nbody" 형태로 갱신
                        title = it.content.split("\n", 1)[0]
                        it.content = f"{title}\n\n{body_text}".strip()
                except Exception as e:
                    logger.debug(
                        f"  GizmodoJP 본문 보강 실패 ({it.source_url}): {e}"
                    )
                enriched.append(it)

        logger.info(f"GizmodoJP 수집 완료: {len(enriched)}건")
        return enriched

    # ---------- 검색 (메타 후보) ----------
    async def _fetch_search(
        self, client: httpx.AsyncClient, q: str, page: int
    ) -> List[RawVOC]:
        # page=1 은 page 파라미터 없이 호출 (정규 URL)
        page_q = "" if page == 1 else f"&page={page}"
        url = SEARCH_URL.format(base=BASE_URL, q=q, page=page_q)
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return []
        return self._parse_search(resp.text)

    def _parse_search(self, html: str) -> List[RawVOC]:
        data = self._extract_next_data(html)
        if not data:
            return []
        pp = data.get("props", {}).get("pageProps", {})
        articles = pp.get("articles") or []
        results: List[RawVOC] = []
        for a in articles:
            try:
                url = (a.get("url") or "").strip()
                title = (a.get("title") or "").strip()
                slug = (a.get("slug") or "").strip()
                if not url or not title:
                    continue

                released = a.get("released_at") or ""
                published_at = self._parse_iso(released)

                collaborators = a.get("collaborators") or []
                author = None
                if collaborators and isinstance(collaborators[0], dict):
                    author = collaborators[0].get("name")

                tags = a.get("tags") or []
                tag_names = [
                    t.get("name") for t in tags
                    if isinstance(t, dict) and t.get("name")
                ]

                # 안정 ID: URL 의 slug 부분 (UUID 형태 슬러그도 그대로 stable)
                stable = slug or hashlib.md5(url.encode()).hexdigest()[:12]
                external_id = hashlib.md5(f"{url}#{stable}".encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=title,  # 본문 보강 단계에서 덮어씀
                    source_url=url,
                    author_name=author,
                    published_at=published_at,
                    country_code="JP",
                    meta={"slug": stable, "tags": tag_names, "source": "search"},
                ))
            except Exception as e:
                logger.debug(f"GizmodoJP article meta 파싱 실패: {e}")
        return results

    # ---------- 상세 본문 ----------
    async def _fetch_article_body(
        self, client: httpx.AsyncClient, article_url: str
    ) -> Optional[str]:
        resp = await client.get(article_url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return None
        data = self._extract_next_data(resp.text)
        if not data:
            return None
        d = data.get("props", {}).get("pageProps", {}).get("data") or {}
        raw_body = d.get("body")
        if not raw_body:
            return None
        try:
            body_json = json.loads(raw_body)
        except (ValueError, TypeError):
            return None

        blocks = body_json.get("blocks") or []
        text_parts: List[str] = []
        for b in blocks:
            t = (b.get("text") or "").strip()
            if not t:
                continue
            # 광고/PR 라벨, "GIZMODO サイトリニューアル特別企画" 같은 짧은 트레일러는 통과
            # (필터링 과하면 본문 손실 → 길이만 기준으로 유지)
            text_parts.append(t)
        body_text = "\n".join(text_parts).strip()
        if not body_text:
            return None
        if len(body_text) > MAX_BODY_CHARS:
            body_text = body_text[:MAX_BODY_CHARS]
        return body_text

    # ---------- 필터/유틸 ----------
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        tags = " ".join(voc.meta.get("tags") or []).lower()
        haystack = f"{text} {tags}"
        if not haystack.strip():
            return False
        return any(kw.lower() in haystack for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _extract_next_data(html: str) -> Optional[dict]:
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_iso(text: str) -> Optional[datetime]:
        """'2026-05-28T01:30:00Z' / '2026-05-28T01:30:00.000Z' → UTC datetime"""
        if not text:
            return None
        try:
            t = text.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                # 사이트가 UTC 'Z' 형태로 주지만 fallback 도 UTC 로 가정
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
