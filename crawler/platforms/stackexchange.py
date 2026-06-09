"""
Stack Exchange 크롤러 — android.stackexchange.com REST API (인증 불필요)

- 검색: /2.3/search/advanced?order=desc&sort=activity&q=<q>&site=android&filter=withbody
- 답변:  /2.3/questions/<id>/answers?site=android&filter=withbody
- 코멘트: /2.3/questions/<id>/comments?site=android&filter=withbody

API quota: 키 없이 300/day. 응답에 backoff 필드가 있으면 그 초만큼 대기.
"""
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
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

SE_BASE = "https://api.stackexchange.com/2.3"
SE_SITE = "android"
SE_QUESTION_URL = "https://android.stackexchange.com/questions"

QUERY_TERMS = [
    "Galaxy S25",
    "Samsung Galaxy issue",
    "Z Fold",
    "Galaxy Watch problem",
    "Galaxy battery",
    "Galaxy camera",
]

MAX_QUESTIONS = 40
PAGESIZE = 30


class StackExchangeCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.0

    def __init__(self, platform_code: str = "stackexchange", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_qids: set = set()
        questions: List[dict] = []

        async with self._make_httpx_client() as client:
            # 1) 검색어별 질문 목록 수집
            for q in QUERY_TERMS:
                try:
                    hits = await self._search_questions(client, q)
                    logger.info(f"  SE search '{q}': {len(hits)}건")
                    for item in hits:
                        qid = item.get("question_id")
                        if not qid or qid in seen_qids:
                            continue
                        seen_qids.add(qid)
                        questions.append(item)
                except Exception as e:
                    logger.warning(f"  SE search '{q}' 실패: {e}")
                await self._random_delay()

            # 2) 최신순 정렬 후 상한 적용
            questions.sort(
                key=lambda i: i.get("last_activity_date") or 0,
                reverse=True,
            )
            target_questions = questions[:MAX_QUESTIONS]
            logger.info(
                f"SE 질문 {len(questions)}건 → 상위 {len(target_questions)}건 상세 수집"
            )

            # 3) 질문 본문 RawVOC + 답변 + 코멘트
            answer_total = 0
            comment_total = 0
            for q_item in target_questions:
                q_voc = self._question_to_voc(q_item)
                if q_voc:
                    raw_vocs.append(q_voc)

                qid = q_item.get("question_id")
                if not qid:
                    continue

                await self._random_delay()
                try:
                    answers = await self._fetch_answers(client, qid)
                    raw_vocs.extend(answers)
                    answer_total += len(answers)
                except Exception as e:
                    logger.warning(f"  SE 답변 수집 실패 (q={qid}): {e}")

                await self._random_delay()
                try:
                    comments = await self._fetch_comments(client, qid)
                    raw_vocs.extend(comments)
                    comment_total += len(comments)
                except Exception as e:
                    logger.warning(f"  SE 코멘트 수집 실패 (q={qid}): {e}")

        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"SE 수집 완료: 질문 {len(target_questions)}건 + 답변 {answer_total}건 + "
            f"코멘트 {comment_total}건 = {len(raw_vocs)}/{before} (MX 필터)"
        )
        return raw_vocs

    # ---------- API helpers ----------

    async def _se_get(
        self, client: httpx.AsyncClient, path: str, params: dict
    ) -> dict:
        url = f"{SE_BASE}{path}"
        merged = {"site": SE_SITE, **params}
        resp = await client.get(url, params=merged)
        resp.raise_for_status()
        payload = resp.json()
        # API quota / backoff 처리
        backoff = payload.get("backoff")
        if backoff:
            try:
                wait = float(backoff)
                logger.info(f"  SE backoff {wait}s 준수")
                await asyncio.sleep(wait)
            except Exception:
                pass
        return payload

    async def _search_questions(
        self, client: httpx.AsyncClient, query: str
    ) -> List[dict]:
        params = {
            "order": "desc",
            "sort": "activity",
            "q": query,
            "pagesize": PAGESIZE,
            "filter": "withbody",
        }
        payload = await self._se_get(client, "/search/advanced", params)
        return payload.get("items") or []

    async def _fetch_answers(
        self, client: httpx.AsyncClient, qid: int
    ) -> List[RawVOC]:
        params = {
            "order": "desc",
            "sort": "votes",
            "pagesize": 20,
            "filter": "withbody",
        }
        payload = await self._se_get(client, f"/questions/{qid}/answers", params)
        items = payload.get("items") or []
        return [v for v in (self._answer_to_voc(it, qid) for it in items) if v]

    async def _fetch_comments(
        self, client: httpx.AsyncClient, qid: int
    ) -> List[RawVOC]:
        params = {
            "order": "desc",
            "sort": "creation",
            "pagesize": 20,
            "filter": "withbody",
        }
        payload = await self._se_get(client, f"/questions/{qid}/comments", params)
        items = payload.get("items") or []
        return [v for v in (self._comment_to_voc(it, qid) for it in items) if v]

    # ---------- mapping ----------

    @staticmethod
    def _strip_html(html: str) -> str:
        if not html:
            return ""
        try:
            return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        except Exception:
            return html

    @staticmethod
    def _owner_name(item: dict) -> Optional[str]:
        owner = item.get("owner") or {}
        return owner.get("display_name")

    @staticmethod
    def _ts_to_dt(ts) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            return None

    def _question_to_voc(self, item: dict) -> Optional[RawVOC]:
        qid = item.get("question_id")
        if not qid:
            return None
        title = (item.get("title") or "").strip()
        body_md = item.get("body_markdown") or ""
        body = body_md.strip() if body_md else self._strip_html(item.get("body") or "")
        content = f"{title}\n{body}".strip() if body else title
        if not content:
            return None

        link = item.get("link") or f"{SE_QUESTION_URL}/{qid}"

        return RawVOC(
            external_id=hashlib.md5(f"se_q_{qid}".encode()).hexdigest()[:16],
            content=content,
            source_url=link,
            author_name=self._owner_name(item),
            published_at=self._ts_to_dt(item.get("creation_date")),
            likes_count=int(item.get("score") or 0),
            comments_count=int(item.get("answer_count") or 0),
            country_code="US",
            meta={"se_kind": "question", "qid": qid, "tags": item.get("tags") or []},
        )

    def _answer_to_voc(self, item: dict, qid: int) -> Optional[RawVOC]:
        aid = item.get("answer_id")
        if not aid:
            return None
        body_md = item.get("body_markdown") or ""
        body = body_md.strip() if body_md else self._strip_html(item.get("body") or "")
        if not body.strip():
            return None

        return RawVOC(
            external_id=hashlib.md5(f"se_a_{aid}".encode()).hexdigest()[:16],
            content=body,
            source_url=f"{SE_QUESTION_URL}/{qid}#{aid}",
            author_name=self._owner_name(item),
            published_at=self._ts_to_dt(item.get("creation_date")),
            likes_count=int(item.get("score") or 0),
            country_code="US",
            meta={"se_kind": "answer", "qid": qid, "aid": aid},
        )

    def _comment_to_voc(self, item: dict, qid: int) -> Optional[RawVOC]:
        cid = item.get("comment_id")
        if not cid:
            return None
        body_md = item.get("body_markdown") or ""
        body = body_md.strip() if body_md else self._strip_html(item.get("body") or "")
        if not body.strip():
            return None

        return RawVOC(
            external_id=hashlib.md5(f"se_c_{cid}".encode()).hexdigest()[:16],
            content=body,
            source_url=f"{SE_QUESTION_URL}/{qid}#comment{cid}",
            author_name=self._owner_name(item),
            published_at=self._ts_to_dt(item.get("creation_date")),
            likes_count=int(item.get("score") or 0),
            country_code="US",
            meta={"se_kind": "comment", "qid": qid, "cid": cid},
        )
