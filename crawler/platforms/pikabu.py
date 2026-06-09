"""Pikabu (pikabu.ru, 러시아 종합 게시판) — 검색 페이지 HTML 스크래핑.

배경
====
4PDA 와 함께 러시아 voc 보강 (4PDA = 모바일 전문, Pikabu = 일반 사용자
다수 → 일상 사용 후기/불만이 더 풍부).  Reddit 유사한 카드형 피드 UI.

차단/우회 (Data Grow R4 Discovery 실증)
======================================
- 메인 (/), /new.rss : DDoS-Guard 403.
- /search?q=<query>  : 200 OK 통과.  쿠키 없이 첫 요청 성공.
- 응답 인코딩 : windows-1251 → httpx 가 자동 디코드 (resp.text).
- 본문 구조  : <article class="story" data-story-id="...">
                 > a.story__title-link  (제목 + URL)
                 > div.story-block_type_text  (본문 텍스트 블록, n개)
                 > a.user__nick           (작성자)
                 > time[datetime]         (게시 시각, ISO8601 +03:00)

쿼리
====
러시아어 모델명 영문 표기가 표준 (галакси/айфон 표기도 존재하지만
영문이 다수).  4PDA 와 중복 회피 위해 더 일상적 키워드 선택.

키 의존성: 없음.
플랫폼 코드: ``pikabu``  (DB platforms id=106, region='RU').
MX 필터: ``nlp.mx_keywords.is_mx_relevant`` 강제 적용.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)


SEARCH_URL = "https://pikabu.ru/search"

# 5 쿼리.  영문 모델명 (러시아 사용자 표준 표기).
# Galaxy 일반 + 폴드/플립 + iPhone (비교 voc) + Samsung (브랜드 전반).
QUERIES: List[str] = [
    "Galaxy",
    "Samsung",
    "Galaxy Fold",
    "Galaxy Flip",
    "iPhone",
]

# Pikabu 검색은 페이지당 약 10 story 노출.  5 쿼리 × 10 = 50 정도.
# MAX_POSTS 상한 보수 (NLP 단계 부하 방지, 4PDA 와 합쳐 RU 트래픽 견제).
MAX_POSTS = 80
MIN_CONTENT_LEN = 30  # 너무 짧은 본문 (제목만 등) 제외


def _ua() -> str:
    """단일 UA 고정 — DDoS-Guard 가 UA 회전을 의심하지 않도록."""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """ISO8601 with offset (e.g. 2026-06-09T12:35:09+03:00) → aware UTC datetime."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.strip())
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _to_int(text: Optional[str]) -> int:
    """'1,2K' / '12' 같은 표기 → int.  실패 시 0."""
    if not text:
        return 0
    t = text.strip().replace(",", ".").lower()
    try:
        if t.endswith("k"):
            return int(float(t[:-1]) * 1000)
        if t.endswith("m"):
            return int(float(t[:-1]) * 1_000_000)
        return int(float(t))
    except Exception:
        return 0


def parse_search_html(html: str, query: str) -> List[RawVOC]:
    """검색 결과 HTML → RawVOC 목록.

    BeautifulSoup 으로 ``article.story`` 단위 파싱.  본문은 다중
    ``div.story-block_type_text`` 의 텍스트를 공백 결합.  제목 + 본문이
    실제 voc 컨텐츠.
    """
    out: List[RawVOC] = []
    soup = BeautifulSoup(html, "html.parser")
    seen_ids: set = set()
    for art in soup.find_all("article", class_="story"):
        sid = art.get("data-story-id")
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)

        title_a = art.find("a", class_="story__title-link")
        title = title_a.get_text(strip=True) if title_a else ""
        href = title_a.get("href") if title_a else None
        if not href:
            # advert/promo 카드는 story__title-link 없음 → skip
            continue

        # text blocks (이미지/비디오 블록은 제외)
        blocks = art.find_all("div", class_="story-block_type_text")
        body = " ".join(b.get_text(" ", strip=True) for b in blocks)
        content = (title + "\n\n" + body).strip()
        if len(content) < MIN_CONTENT_LEN:
            continue

        # author
        auth_el = art.find(class_="user__nick")
        author = auth_el.get_text(strip=True) if auth_el else None

        # published_at
        time_el = art.find("time")
        pub = _parse_dt(time_el.get("datetime")) if time_el else None

        # rating (Pikabu 의 좋아요/싫어요 합산 표시) → likes 매핑
        rating_el = art.find(class_=lambda c: c and "story__rating-count" in c)
        likes = _to_int(rating_el.get_text() if rating_el else None)

        # comments
        cmts_el = art.find(class_="story__comments-link-count")
        comments = _to_int(cmts_el.get_text() if cmts_el else None)

        # canonical URL — 검색 추적 파라미터 제거
        clean_url = href.split("?")[0] if "?" in href else href

        out.append(RawVOC(
            external_id=hashlib.md5(f"pikabu::{sid}".encode()).hexdigest()[:16],
            content=content,
            source_url=clean_url,
            author_name=author,
            published_at=pub,
            likes_count=likes,
            comments_count=comments,
            shares_count=0,
            country_code="RU",
            meta={
                "platform": "pikabu",
                "query": query,
                "story_id": sid,
            },
        ))
    return out


class PikabuCrawler(BaseCrawler):
    """Pikabu 검색 페이지 HTML 스크래핑 — 5 쿼리 fan-out, MX 필터."""

    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    def __init__(self, platform_code: str = "pikabu", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.stats = {"fetched": 0, "per_query": {}, "blocked": []}

    def _client(self) -> httpx.AsyncClient:
        # 단일 client 세션 = DDoS-Guard 쿠키 자동 재사용.
        return httpx.AsyncClient(
            headers={
                "User-Agent": _ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            timeout=20.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_urls: set = set()

        async with self._client() as client:
            for q in QUERIES:
                if len(raw_vocs) >= MAX_POSTS:
                    break
                try:
                    resp = await client.get(SEARCH_URL, params={"q": q})
                    if resp.status_code != 200:
                        self.stats["blocked"].append(f"{q}:{resp.status_code}")
                        logger.warning(f"  pikabu {q!r} HTTP {resp.status_code} — skip")
                        continue
                    items = parse_search_html(resp.text, q)
                    new_n = 0
                    for v in items:
                        if v.source_url in seen_urls:
                            continue
                        seen_urls.add(v.source_url)
                        raw_vocs.append(v)
                        new_n += 1
                        if len(raw_vocs) >= MAX_POSTS:
                            break
                    self.stats["fetched"] += len(items)
                    self.stats["per_query"][q] = new_n
                    logger.info(f"  pikabu {q!r}: {len(items)}건 parse / {new_n}건 신규")
                except httpx.HTTPError as e:
                    self.stats["blocked"].append(f"{q}:net")
                    logger.warning(f"  pikabu {q!r} 네트워크 실패: {e}")
                except Exception as e:
                    logger.warning(f"  pikabu {q!r} 처리 실패: {e}")
                await self._random_delay()

        # MX 키워드 강제 필터 (Data Clean 4 정책).
        try:
            from nlp.mx_keywords import is_mx_relevant
            before = len(raw_vocs)
            raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
            logger.info(
                f"pikabu 수집 완료: {len(raw_vocs)}건 "
                f"(fetched {self.stats['fetched']} / mx_filter {before}→{len(raw_vocs)})"
            )
        except Exception as e:
            logger.warning(f"pikabu mx_filter skip: {e}")
            logger.info(f"pikabu 수집 완료: {len(raw_vocs)}건 (fetched {self.stats['fetched']})")

        return raw_vocs


# 단독 실행: python -m platforms.pikabu
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    crawler = PikabuCrawler()
    vocs = asyncio.run(crawler.crawl())
    print(f"\n=== pikabu dry run ===")
    print(f"vocs: {len(vocs)}")
    print(f"stats: {crawler.stats}")
    if vocs:
        s = vocs[0]
        print(f"sample[0]: url={s.source_url}")
        print(f"           content={s.content[:120]}...")
