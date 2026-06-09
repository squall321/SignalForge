"""4chan /g/ (Technology) mobile/smartphone thread 크롤러.

API: https://a.4cdn.org/g/catalog.json — 전 catalog (board 단위, JSON)
     https://a.4cdn.org/g/thread/{no}.json — 단일 thread (OP + 댓글)

rate limit: 공식 1 req/s (User-Agent 권장). 익명, 키 불필요.
범위: 매 cycle catalog 1회 + mobile 키워드 매칭 thread 상위 N개 fetch.
필터: catalog 매칭 정규식(1차) + 본문 mx_keywords.is_mx_relevant(2차).
정제: HTML 태그/<wbr>/&gt;quote/&#039;엔터티 strip → plain text.
주의: 익명 채널 → 욕설/짧은 글 다수. content_len < 20 자 댓글 컷.
"""
from __future__ import annotations
import hashlib
import html as html_lib
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

CATALOG_URL = "https://a.4cdn.org/g/catalog.json"
THREAD_URL = "https://a.4cdn.org/g/thread/{no}.json"

# catalog 1차 매칭 (sub + com). 단어경계로 false positive 차단.
RELEVANCE_RE = re.compile(
    r"\b(mobile|phone|smartphone|android|iphone|galaxy|samsung|pixel|"
    r"fold(?:able)?|flip|oneplus|xiaomi|huawei|sgt|sqt|spg)\b",
    re.IGNORECASE,
)

MAX_THREADS = 15            # catalog 에서 매칭된 thread 중 fetch 상한
MIN_COMMENT_LEN = 20        # 짧은 익명 댓글 컷
MAX_POSTS_PER_THREAD = 80   # 한 thread 당 OP+댓글 상한 (인기 thread 300+ 방어)


def _clean_html(s: str) -> str:
    """4chan com HTML → plain text. <br>=개행, <wbr>=삭제, quote span/링크 strip, entity unescape."""
    if not s:
        return ""
    # <br> → \n, 그 외 태그 모두 제거
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<wbr\s*/?>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    # >quote prefix 보존 (Reddit 스타일 인용)
    return s.strip()


class FourchanGCrawler(BaseCrawler):
    """4chan /g/ Mobile thread crawler — 익명 채널 (스마트폰 일반론, /spg/, sgt 등)."""

    MIN_DELAY = 1.2
    MAX_DELAY = 2.0  # 공식 1 req/s 보수적 준수

    def __init__(self, platform_code: str = "fourchan_g", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # 1) catalog 1회
            try:
                resp = await client.get(CATALOG_URL)
                resp.raise_for_status()
                catalog = resp.json()
            except Exception as e:
                logger.warning(f"  4chan /g/ catalog 실패: {e}")
                return raw_vocs

            # 2) matching thread 추출
            matched: List[dict] = []
            for page in catalog if isinstance(catalog, list) else []:
                for th in page.get("threads", []) or []:
                    sub = th.get("sub", "") or ""
                    com = th.get("com", "") or ""
                    if RELEVANCE_RE.search(sub + " " + com):
                        matched.append(th)
            # replies 많은 thread 우선 (활동량)
            matched.sort(key=lambda t: t.get("replies", 0), reverse=True)
            targets = matched[:MAX_THREADS]
            logger.info(f"4chan /g/ catalog matched={len(matched)} fetch={len(targets)}")

            # 3) thread fan-out
            seen_ids: set = set()
            for th in targets:
                no = th.get("no")
                if not no:
                    continue
                await self._random_delay()
                try:
                    tr = await client.get(THREAD_URL.format(no=no))
                    tr.raise_for_status()
                    thread_data = tr.json()
                except Exception as e:
                    logger.warning(f"  4chan /g/ thread {no} 실패: {e}")
                    continue

                posts = (thread_data.get("posts") or [])[:MAX_POSTS_PER_THREAD]
                kept_thread = 0
                for p in posts:
                    pno = p.get("no")
                    if not pno or pno in seen_ids:
                        continue

                    sub_p = _clean_html(p.get("sub", "") or "")
                    com_p = _clean_html(p.get("com", "") or "")
                    content = (f"{sub_p}\n\n{com_p}" if sub_p else com_p).strip()
                    if not content or len(content) < MIN_COMMENT_LEN:
                        continue
                    if not is_mx_relevant(content):
                        continue

                    ts = p.get("time")
                    pub_dt: Optional[datetime] = None
                    if isinstance(ts, (int, float)):
                        try:
                            pub_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                        except Exception:
                            pub_dt = None

                    seen_ids.add(pno)
                    raw_vocs.append(RawVOC(
                        external_id=hashlib.sha1(f"4chan_g:{no}:{pno}".encode()).hexdigest()[:16],
                        content=content,
                        source_url=f"https://boards.4chan.org/g/thread/{no}#p{pno}",
                        author_name=None,  # 익명 보드
                        published_at=pub_dt,
                        country_code=None,
                        meta={
                            "source": "fourchan_g_api",
                            "thread_no": no,
                            "post_no": pno,
                            "is_op": pno == no,
                            "thread_sub": (th.get("sub") or "")[:120],
                        },
                    ))
                    kept_thread += 1
                logger.info(f"  4chan /g/ thread {no}: {kept_thread} kept / {len(posts)} posts")

        logger.info(f"4chan /g/ 수집 완료: {len(raw_vocs)}건 (MX 필터)")
        return raw_vocs
