# 리콜/규제 DB 수집기 — US CPSC 공식 리콜 API의 Samsung 제품 결함·리콜
"""
Recalls 크롤러 — 규제기관 공식 리콜 데이터 = "시장 불량"의 정본(authoritative).

- US CPSC(saferproducts.gov REST API): Samsung/Galaxy 리콜. 화재·감전 등 공식 위해정보.
  갤럭시 노트7 발화 리콜(2016) 등. RecallDate=원 발표일, 위해(Hazard)/시정(Remedy) 포함.
- 저용량·고신호: 리뷰/댓글과 달리 권위 있는 결함 확정 신호. worker 부담 거의 없음.

플랫폼 코드: recalls  (alembic 0026 platforms row)
"""
import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

_CPSC = "https://www.saferproducts.gov/RestWebServices/Recall"
_TERMS = ["Samsung", "Galaxy"]


class RecallsCrawler(BaseCrawler):
    """US CPSC 공식 리콜 API 로 Samsung 제품 리콜을 수집."""

    MIN_DELAY = 0.5
    MAX_DELAY = 1.0

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("recalls", product_code=product_code, job_id=job_id)

    async def crawl(self) -> List[RawVOC]:
        seen: set = set()
        out: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            for term in _TERMS:
                out += await self._cpsc(client, term, seen)
                await self._random_delay()
        logger.info("리콜 수집 완료 — CPSC %d건", len(out))
        return out

    async def _cpsc(self, client, term, seen) -> List[RawVOC]:
        try:
            r = await client.get(_CPSC, params={"format": "json", "RecallTitle": term})
            if r.status_code != 200:
                logger.warning("CPSC %s → %s", term, r.status_code)
                return []
            recalls = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("CPSC %s 실패: %r", term, e)
            return []
        res: List[RawVOC] = []
        for rc in recalls:
            rid = str(rc.get("RecallID") or rc.get("RecallNumber") or "")
            title = (rc.get("Title") or "").strip()
            if not rid or not title:
                continue
            uid = hashlib.md5(f"recall#cpsc#{rid}".encode()).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)
            # 본문 = 제목 + 설명 + 위해(Hazard) — VOC 의미 극대화
            desc = self._strip(rc.get("Description") or "")
            hazards = "; ".join(h.get("Name", "") for h in (rc.get("Hazards") or []) if h.get("Name"))
            parts = [title]
            if desc:
                parts.append(desc)
            if hazards:
                parts.append(f"[위해: {hazards}]")
            content = "\n".join(parts)
            res.append(RawVOC(
                external_id=uid,
                content=content,
                source_url=rc.get("URL") or f"https://www.saferproducts.gov/RestWebServices/Recall?RecallID={rid}",
                author_name="US CPSC",
                published_at=self._parse_date(rc.get("RecallDate")),
                country_code="US",
                meta={"source": "cpsc_recall", "recall_number": rc.get("RecallNumber"),
                      "hazards": hazards},
            ))
        return res

    def _strip(self, html: str) -> str:
        import re
        return re.sub(r"<[^>]+>", "", html).strip()

    def _parse_date(self, text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
