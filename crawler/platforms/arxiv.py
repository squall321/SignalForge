"""arXiv 검색 API — mobile/wearable 학술 논문 (rate limit 낮음).

검색: cat:cs.HC OR cs.CY OR cs.MM OR cs.NI OR eess.SP AND keyword (samsung/galaxy/iphone/smartphone/wearable)
API 응답: Atom XML. abstract 본문 풍부 (>500자, mx_rich 상승 기여).

rate limit 정책: arXiv 공식 권장 — 1 query / 3초.  본 collector 는 단일 query 1회 호출.
"""
from __future__ import annotations
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional
import logging
import re

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

# **https 를 써야 한다.** http 는 301 로 리다이렉트되는데, 리다이렉트를 안 따르는
# 클라이언트에서는 빈 본문이 와서 XML 파싱이 터진다(실측 2026-09-15).
ARXIV_API = "https://export.arxiv.org/api/query"
SEARCH_QUERY = (
    "(cat:cs.HC OR cat:cs.CY OR cat:cs.MM OR cat:cs.NI OR cat:eess.SP) AND "
    "(all:\"samsung\" OR all:\"galaxy\" OR all:\"iphone\" OR all:\"smartphone\" "
    "OR all:\"wearable\" OR all:\"foldable\")"
)
MAX_RESULTS = 50


def _date_window() -> tuple:
    """(after, before) 를 arXiv 형식(YYYYMMDDHHMM)으로. 없으면 ('', '').

    env 는 'YYYY-MM-DD' 또는 'YYYY-MM-DDTHH:MM:SS' 를 받는다 — 다른 백필과
    표기를 맞춰 혼동을 줄인다.
    """
    def _fmt(v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        digits = re.sub(r"[^0-9]", "", v)
        if len(digits) < 8:
            return ""
        return (digits[:12] + "0000")[:12]

    return (_fmt(os.getenv("ARXIV_AFTER", "")),
            _fmt(os.getenv("ARXIV_BEFORE", "")))
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class ArxivCrawler(BaseCrawler):
    """arXiv 학술 논문 수집 (cs.HC / cs.CY / cs.MM mobile/wearable)."""

    def __init__(self, platform_code: str = "arxiv", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        # 역사 백필이 기간 창을 주면 submittedDate 범위를 얹는다.
        # arXiv 문법: submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]
        query = SEARCH_QUERY
        after, before = _date_window()
        if after or before:
            lo = after or "199101010000"
            hi = before or "299901010000"
            query = f"{SEARCH_QUERY} AND submittedDate:[{lo} TO {hi}]"
            logger.info("  arXiv 기간 창 [%s ~ %s]", lo, hi)

        params = {
            "search_query": query,
            "start": 0,
            "max_results": MAX_RESULTS,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        async with self._make_httpx_client() as client:
            try:
                resp = await client.get(ARXIV_API, params=params)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
            except Exception as e:
                logger.warning(f"  arXiv fetch 실패: {e}")
                return raw_vocs

            for entry in root.findall(f"{ATOM_NS}entry"):
                try:
                    eid = entry.findtext(f"{ATOM_NS}id", default="").strip()
                    title = entry.findtext(f"{ATOM_NS}title", default="").strip()
                    summary = entry.findtext(f"{ATOM_NS}summary", default="").strip()
                    published = entry.findtext(f"{ATOM_NS}published", default="").strip()
                    # 첫 저자
                    first_author = entry.find(f"{ATOM_NS}author/{ATOM_NS}name")
                    author_name = first_author.text.strip() if first_author is not None and first_author.text else None

                    if not eid or not summary:
                        continue

                    # 본문 = 제목 + abstract (보통 1000-2000자)
                    content = f"{title}\n\n{summary}"
                    if not is_mx_relevant(content):
                        continue

                    pub_dt: Optional[datetime] = None
                    if published:
                        try:
                            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        except Exception:
                            pub_dt = None

                    raw_vocs.append(RawVOC(
                        external_id=hashlib.sha1(eid.encode()).hexdigest()[:16],
                        content=content,
                        source_url=eid,
                        author_name=author_name,
                        published_at=pub_dt,
                        country_code=None,
                        meta={"source": "arxiv_atom", "arxiv_id": eid.rsplit("/", 1)[-1]},
                    ))
                except Exception as e:
                    logger.warning(f"  arXiv entry 처리 실패: {e}")

        logger.info(f"arXiv 수집 완료: {len(raw_vocs)}/{MAX_RESULTS}건 (MX 필터)")
        return raw_vocs
