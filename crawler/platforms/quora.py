"""Quora — 영문 QA 토픽 (Galaxy / iPhone / Android).

R4 K3 차단 실태 (2026-06-09 측정)
================================
모든 접근 경로가 Cloudflare managed challenge 로 차단됨:

  endpoint                          status  bypass
  --------------------------------  ------  ----------------------------------
  /                                 403     Sec-CH-UA-* JS challenge 필수
  /topic/Samsung-Galaxy             403     동일
  /sitemap.xml, /sitemap/topic.xml  403     동일
  /What-do-you-think-of-...-S24     403     개별 Q 페이지도 challenge
  Googlebot / Bingbot UA            403     UA spoof 무력
  Wayback Machine (archive.org)     403     캡처 부재 (available=null)
  Google search snippet             N/A     SERP 클릭 시 challenge 페이지로 진입

차단 우회 옵션
==============
1) Playwright headless + Cloudflare turnstile solver — 운영 비용↑·약관 위반 위험
2) ScraperAPI / Bright Data 같은 상용 프록시 — 유료, 본 라운드 정책상 키 없음
3) 비공식 미러 (예: quoradb.com) — 부정확, 라이선스 회색

본 라운드 결정
==============
파일을 graceful 스켈레톤으로 등록만 해두고 실제 fetch 는 차단 감지 시 빈 결과
반환.  이후 정책 변경(브라우저 자동화 도입 또는 상용 프록시 키 확보) 시
crawl() 내부 _fetch() 만 교체하면 바로 라이브 가동된다.

- BaseCrawler / RawVOC / is_mx_relevant 만 사용 (다른 collector 와 동형).
- 첫 호출에서 / 403 감지 즉시 graceful return.  retry 없음 (예의·OOM 방어).
- audit 가능하도록 stats(blocked / fetched / kept) 노출.

플랫폼 코드: quora  (DB platforms row id=106 으로 등록).
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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402
from nlp.mx_keywords import is_mx_relevant  # noqa: E402

logger = logging.getLogger(__name__)


# 접근 시도할 topic 페이지 — 영문 메인 모바일 토픽.
TOPICS: List[str] = [
    "Samsung-Galaxy",
    "iPhone",
    "Android-1",
    "Smartphones",
]

# 프로브 URL (가장 가벼운 차단 감지용) — 차단 시 모든 topic 시도 skip.
PROBE_URL = "https://www.quora.com/"

# 결과 상한 (향후 라이브화 대비).
MAX_POSTS = 120


# @lat: QuoraCrawler — Cloudflare 차단 시 graceful skeleton.
class QuoraCrawler(BaseCrawler):
    """Quora 영문 QA collector — 현재 Cloudflare 차단으로 빈 결과 반환.

    `_fetch_topic` 만 향후 브라우저 자동화 / 프록시 백엔드로 교체하면
    그대로 가동된다.
    """

    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    def __init__(self, platform_code: str = "quora", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.stats = {
            "probe_status": None,
            "topics_attempted": 0,
            "topics_blocked": 0,
            "fetched": 0,
            "kept": 0,
            "blocked": False,
            "reason": None,
        }

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # 1) Cloudflare 차단 프로브.  / 접근이 challenge 면 모든 topic skip.
            try:
                probe = await client.get(PROBE_URL)
                self.stats["probe_status"] = probe.status_code
                if probe.status_code != 200 or "Just a moment" in probe.text[:2000]:
                    self.stats["blocked"] = True
                    self.stats["reason"] = (
                        f"cloudflare_challenge (probe HTTP {probe.status_code})"
                    )
                    logger.warning(
                        f"  quora 차단 — probe HTTP {probe.status_code}, "
                        "topic fetch skip (graceful)."
                    )
                    return raw_vocs
            except Exception as e:
                self.stats["blocked"] = True
                self.stats["reason"] = f"probe_exception: {type(e).__name__}: {e}"
                logger.warning(f"  quora probe 실패: {e} — skip")
                return raw_vocs

            # 2) (라이브 경로) probe 통과 시 topic fan-out — 현재 도달 불가.
            for topic in TOPICS:
                self.stats["topics_attempted"] += 1
                try:
                    items = await self._fetch_topic(client, topic)
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code if e.response is not None else 0
                    self.stats["topics_blocked"] += 1
                    logger.warning(f"  quora topic={topic} HTTP {code} — skip")
                    continue
                except Exception as e:
                    self.stats["topics_blocked"] += 1
                    logger.warning(f"  quora topic={topic} 실패: {e}")
                    continue

                self.stats["fetched"] += len(items)
                for it in items:
                    voc = self._to_rawvoc(it, topic)
                    if voc is None:
                        continue
                    if not is_mx_relevant(voc.content):
                        continue
                    raw_vocs.append(voc)
                    self.stats["kept"] += 1
                    if len(raw_vocs) >= MAX_POSTS:
                        break
                await self._random_delay()
                if len(raw_vocs) >= MAX_POSTS:
                    break

        logger.info(
            f"quora 수집 완료: {len(raw_vocs)}건 "
            f"(blocked={self.stats['blocked']} reason={self.stats['reason']})"
        )
        return raw_vocs

    # --- 분리된 fetch 슬롯: 향후 Playwright/프록시 백엔드로 교체 ----------
    async def _fetch_topic(
        self, client: httpx.AsyncClient, topic: str
    ) -> List[dict]:
        """Topic 페이지에서 질문 목록 수집 — 현재 Cloudflare 차단으로 미동작.

        반환 스펙 (향후):
          [{"qid": str, "title": str, "answer_text": str, "url": str,
            "created_at": str, "author": str}, ...]
        """
        url = f"https://www.quora.com/topic/{topic}"
        resp = await client.get(url)
        resp.raise_for_status()
        # 라이브 파서는 BeautifulSoup HTML 또는 Quora 내부 GraphQL 응답 의존.
        # 본 라운드에서는 도달 불가 → 빈 목록.
        return []

    def _to_rawvoc(self, item: dict, topic: str) -> Optional[RawVOC]:
        qid = item.get("qid")
        title = (item.get("title") or "").strip()
        body = (item.get("answer_text") or "").strip()
        url = (item.get("url") or "").strip()
        if not qid or not title or not url:
            return None

        content = title if not body else f"{title}\n\n{body}"
        published: Optional[datetime] = None
        created_at = item.get("created_at")
        if created_at:
            try:
                s = created_at.replace("Z", "+00:00") if isinstance(created_at, str) else None
                if s:
                    published = datetime.fromisoformat(s)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
            except Exception:
                published = None

        return RawVOC(
            external_id=hashlib.md5(f"quora::{qid}".encode()).hexdigest()[:16],
            content=content,
            source_url=url,
            author_name=item.get("author") or None,
            published_at=published,
            country_code=None,
            meta={"source": "quora_topic", "topic": topic, "qid": qid},
        )


# 단독 실행: python -m platforms.quora
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    crawler = QuoraCrawler()
    vocs = asyncio.run(crawler.crawl())
    print(f"\n=== quora dry run ===")
    print(f"vocs: {len(vocs)}")
    print(f"stats: {crawler.stats}")
