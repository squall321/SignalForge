"""HackerOne Hacktivity — 보안 disclosed report (인증 불필요 익명 API).

Endpoint:  GET https://api.hackerone.com/v1/hackers/hacktivity
Params:    queryString=<keyword>&page[size]=N&page[number]=K

응답 (data: list[hacktivity_item]):
  attributes.title / url / disclosed_at / substate / severity_rating / cve_ids
                / vulnerability_information / total_awarded_amount
  relationships.program.data.attributes.name / handle
  relationships.report_generated_content.data.attributes.hacktivity_summary

본문 합성: 제목 + program + severity + hacktivity_summary + CVE.
rate limit 공식 미공개 → 5초 1요청 보수.

정렬: 익명 API는 `sort=-disclosed_at` (desc) 요청 시 401 → `sort=disclosed_at` (asc)
만 사용 가능. 따라서 전체 페이지를 끝까지 페이지네이션하여 최신 disclosure 가
포함된 마지막 페이지(들)를 우선 확보한다. K6 라운드(2026-06): MAX_PAGES 1→4
로 확장 + page_size 25→50 + 정렬 키 추가 + 클라이언트 측 desc 재정렬.

MX 필터는 모바일 보안 관련 reports 만 채택.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

HACKERONE_API = "https://api.hackerone.com/v1/hackers/hacktivity"

# Samsung / Galaxy / Apple iOS / Google Android / Pixel 중심.
QUERIES = ["samsung", "galaxy", "android", "pixel"]

PAGE_SIZE = 50
MAX_PAGES = 4  # 50 × 4 = 200 후보 / query × 4 query (조기 종료 조건 포함)
# 익명 API에서 -disclosed_at 정렬은 401. 따라서 asc 로 모든 페이지 수집 후
# 클라이언트에서 desc 정렬해 최신 보고서를 우선 노출.
SORT_KEY = "disclosed_at"


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _build_content(title: str, program: str, severity: Optional[str],
                   summary: str, cve_ids: List[str]) -> str:
    parts = [title.strip()]
    if program:
        parts.append(f"Program: {program}")
    if severity:
        parts.append(f"Severity: {severity}")
    if cve_ids:
        parts.append("CVE: " + ", ".join(cve_ids[:5]))
    if summary:
        parts.append("")
        parts.append(summary.strip())
    return "\n".join(parts)


class HackerOneCrawler(BaseCrawler):
    """HackerOne disclosed reports — Samsung/Galaxy/Android/Pixel 보안 인사이트."""

    MIN_DELAY = 4.0
    MAX_DELAY = 6.0

    def __init__(self, platform_code: str = "hackerone", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_ids: set = set()

        async with self._make_httpx_client() as client:
            for q in QUERIES:
                for page in range(1, MAX_PAGES + 1):
                    try:
                        items = await self._fetch_page(client, q, page)
                    except Exception as e:
                        logger.warning(f"  HackerOne q='{q}' page={page} 실패: {e}")
                        break
                    new_kept = 0
                    for it in items:
                        rid = it.get("id")
                        if not rid or rid in seen_ids:
                            continue
                        voc = self._to_rawvoc(it)
                        if voc is None:
                            continue
                        seen_ids.add(rid)
                        raw_vocs.append(voc)
                        new_kept += 1
                    logger.info(
                        f"  HackerOne q='{q}' p={page}: {new_kept} kept / {len(items)} hits (sort={SORT_KEY} asc)"
                    )
                    if len(items) < PAGE_SIZE:
                        break
                    await self._random_delay()
                await self._random_delay()

        # 익명 API는 asc 만 허용 → 클라이언트에서 latest-first 재정렬.
        raw_vocs.sort(
            key=lambda v: v.published_at.timestamp() if v.published_at else 0.0,
            reverse=True,
        )
        if raw_vocs:
            latest = raw_vocs[0].published_at
            oldest = raw_vocs[-1].published_at
            logger.info(
                f"HackerOne 수집 완료: {len(raw_vocs)}건 (MX 필터) — "
                f"latest={latest.date() if latest else 'None'} "
                f"oldest={oldest.date() if oldest else 'None'}"
            )
        else:
            logger.info("HackerOne 수집 완료: 0건 (MX 필터)")
        return raw_vocs

    async def _fetch_page(self, client: httpx.AsyncClient, q: str, page: int) -> List[dict]:
        params = {
            "queryString": q,
            "page[size]": PAGE_SIZE,
            "page[number]": page,
            "sort": SORT_KEY,  # asc — anon API limit. 클라이언트 측에서 desc 재정렬.
        }
        resp = await client.get(HACKERONE_API, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) or []

    def _to_rawvoc(self, item: dict) -> Optional[RawVOC]:
        try:
            rid = str(item.get("id") or "").strip()
            attrs = item.get("attributes") or {}
            title = (attrs.get("title") or "").strip()
            url = (attrs.get("url") or "").strip()
            disclosed = attrs.get("disclosed_at")
            severity = attrs.get("severity_rating")
            cve_ids = attrs.get("cve_ids") or []
            vuln_info = (attrs.get("vulnerability_information") or "").strip()

            rel = item.get("relationships") or {}
            prog_attrs = (((rel.get("program") or {}).get("data") or {}).get("attributes") or {})
            program = (prog_attrs.get("name") or "").strip()

            rgc_attrs = (((rel.get("report_generated_content") or {}).get("data") or {}).get("attributes") or {})
            summary = (rgc_attrs.get("hacktivity_summary") or "").strip()

            body = vuln_info or summary
            content = _build_content(title, program, severity, body, cve_ids)
            if not title or not url:
                return None
            if not is_mx_relevant(content):
                return None

            return RawVOC(
                external_id=hashlib.sha1(f"h1_{rid}".encode()).hexdigest()[:16],
                content=content,
                source_url=url,
                author_name=None,
                published_at=_parse_iso(disclosed),
                country_code=None,
                meta={
                    "source": "hackerone_api",
                    "report_id": rid,
                    "program": program,
                    "severity": severity,
                    "cve_ids": cve_ids,
                    "awarded": attrs.get("total_awarded_amount"),
                },
            )
        except Exception as e:
            logger.warning(f"  HackerOne entry 처리 실패: {e}")
            return None
