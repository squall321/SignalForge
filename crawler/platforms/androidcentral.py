"""
AndroidCentral 크롤러 — httpx + BeautifulSoup(lxml)
forums.androidcentral.com (XenForo) 의 Samsung Galaxy 시리즈 서브포럼에서
스레드 본문 + 댓글을 수집.

www.androidcentral.com 본체는 봇 차단(403)이라 forums.* 서브도메인만 사용.
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

BASE_URL = "https://forums.androidcentral.com"

# Galaxy 최신 라인업 위주 서브포럼 (XenForo node_id 기반 URL)
AC_FORUMS = [
    ("samsung-galaxy-s26-series.1941",       "Galaxy S26"),
    ("samsung-galaxy-s25-series.1927",       "Galaxy S25"),
    ("samsung-galaxy-s24-series.1917",       "Galaxy S24"),
    ("samsung-galaxy-z-fold-7.1932",         "Z Fold 7"),
    ("samsung-galaxy-z-fold-6.1922",         "Z Fold 6"),
    ("samsung-galaxy-z-flip-7.1933",         "Z Flip 7"),
    ("samsung-galaxy-z-flip-6.1921",         "Z Flip 6"),
    ("samsung-galaxy-a-series.1821",         "Galaxy A"),
    ("samsung-one-ui.1936",                  "One UI"),
]

# 목록 페이지 수 (포럼당 최근 N페이지 스캔)
LIST_PAGES = 2
# 상세 페이지로 본문+댓글 수집할 최대 게시물 수
MAX_POSTS = 150

# Galaxy 키워드 — 영문 위주 (글로벌 사이트)
GALAXY_KEYWORDS = [
    "galaxy", "samsung", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra", "buds", "watch", "tab",
    "one ui", "oneui", "exynos", "snapdragon",
]


class AndroidCentralCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "androidcentral", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for forum_path, forum_name in AC_FORUMS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_forum_page(client, forum_path, page)
                        # 갤럭시 서브포럼이라 거의 모두 관련 — 키워드 필터는 안전망
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(
                            f"  AndroidCentral {forum_name} p{page}: "
                            f"{len(filtered)}/{len(posts)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  AndroidCentral {forum_name} p{page} 실패: {e}"
                        )

            # 중복 URL 제거 (스티키 등으로 인한 중복)
            seen_url: set = set()
            uniq: List[RawVOC] = []
            for p in list_posts:
                if p.source_url in seen_url:
                    continue
                seen_url.add(p.source_url)
                uniq.append(p)

            # 최신 활동 순 정렬 → 상위 MAX_POSTS
            uniq.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = uniq[:MAX_POSTS]
            logger.info(
                f"AndroidCentral 리스트 {len(uniq)}건 중 상위 {len(target)}건 상세 수집"
            )

            raw_vocs: List[RawVOC] = []
            for post in target:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_thread_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(
                        f"  AndroidCentral 상세 실패 ({post.source_url}): {e}"
                    )

        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"AndroidCentral 수집 완료: {len(raw_vocs)}/{before} (MX 필터, 스레드 {len(target)}건)"
        )
        return raw_vocs

    # ----- 목록 페이지 -----
    async def _fetch_forum_page(
        self, client: httpx.AsyncClient, forum_path: str, page: int
    ) -> List[RawVOC]:
        if page == 1:
            url = f"{BASE_URL}/forums/{forum_path}/"
        else:
            url = f"{BASE_URL}/forums/{forum_path}/page-{page}"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        return self._parse_forum_list(resp.text)

    def _parse_forum_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "lxml")
        results: List[RawVOC] = []

        for item in soup.select("div.structItem--thread"):
            try:
                title_el = item.select_one(".structItem-title a[href*='/threads/']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if not title or not href:
                    continue
                # /threads/foo-bar.1075340/  → 표준화
                m = re.match(r"^(/threads/[^/]+/)", href)
                if not m:
                    continue
                thread_url = f"{BASE_URL}{m.group(1)}"

                author = item.get("data-author") or "anonymous"

                # 시작 날짜
                start_time = item.select_one(".structItem-startDate time")
                published_at = None
                if start_time and start_time.get("data-timestamp"):
                    try:
                        published_at = datetime.fromtimestamp(
                            int(start_time["data-timestamp"]), tz=timezone.utc
                        )
                    except ValueError:
                        pass

                # 최근 활동 timestamp — 정렬용 (실제 활동 신선도 반영)
                latest_time = item.select_one(".structItem-cell--latest time")
                latest_ts = None
                if latest_time and latest_time.get("data-timestamp"):
                    try:
                        latest_ts = datetime.fromtimestamp(
                            int(latest_time["data-timestamp"]), tz=timezone.utc
                        )
                    except ValueError:
                        pass

                # 댓글 수
                metas = item.select(".structItem-cell--meta dd")
                comments_count = 0
                if metas:
                    try:
                        comments_count = int(
                            re.sub(r"[^\d]", "", metas[0].get_text(strip=True)) or 0
                        )
                    except ValueError:
                        comments_count = 0

                uid = hashlib.md5(thread_url.encode()).hexdigest()[:16]
                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=thread_url,
                    author_name=author,
                    # 정렬 우선순위: 최근 활동 > 시작일
                    published_at=latest_ts or published_at,
                    comments_count=comments_count,
                    country_code="US",
                    meta={"start_date": published_at.isoformat() if published_at else None},
                ))
            except Exception as e:
                logger.debug(f"AndroidCentral 게시물 파싱 실패: {e}")

        return results

    # ----- 상세 페이지: 본문 + 댓글 -----
    async def _fetch_thread_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        resp = await client.get(post.source_url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        title = post.content
        post_url = post.source_url

        # 제목 보정
        title_el = soup.select_one("h1.p-title-value")
        if title_el:
            title = title_el.get_text(strip=True) or title

        articles = soup.select("article.message")
        if not articles:
            return []

        out: List[RawVOC] = []

        # 1) 첫 번째 메시지 = 본문
        first = articles[0]
        body_el = first.select_one(".message-body .bbWrapper") or first.select_one(".bbWrapper")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        starter_author = first.get("data-author") or post.author_name or "anonymous"
        starter_dt = self._parse_article_time(first)

        # post-XXXX (XenForo 안정 ID)
        first_pid = first.get("data-content") or hashlib.md5(post_url.encode()).hexdigest()[:8]
        body_uid = hashlib.md5(f"{post_url}#{first_pid}".encode()).hexdigest()[:16]

        out.append(RawVOC(
            external_id=body_uid,
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=starter_author,
            published_at=starter_dt or post.published_at,
            comments_count=max(0, len(articles) - 1),
            country_code="US",
        ))

        # 2) 나머지 메시지 = 댓글
        for art in articles[1:]:
            try:
                cbody_el = art.select_one(".message-body .bbWrapper") or art.select_one(".bbWrapper")
                if not cbody_el:
                    continue
                ctext = cbody_el.get_text("\n", strip=True)
                ctext = re.sub(r"\n{3,}", "\n\n", ctext).strip()
                if not ctext or len(ctext) < 5:
                    continue

                cauthor = art.get("data-author") or "anonymous"
                cdt = self._parse_article_time(art)

                # post-XXXX 안정 ID — 재크롤 시 중복 방지
                pid = art.get("data-content")
                if not pid:
                    # 폴백: article id (예: js-post-7242627)
                    pid = art.get("id") or f"i{len(out)}"

                cuid = hashlib.md5(f"{post_url}#{pid}".encode()).hexdigest()[:16]
                out.append(RawVOC(
                    external_id=cuid,
                    content=ctext,
                    source_url=post_url,
                    author_name=cauthor,
                    published_at=cdt,
                    country_code="US",
                ))
            except Exception as e:
                logger.debug(f"AndroidCentral 댓글 파싱 실패: {e}")

        logger.info(
            f"  AndroidCentral 상세 {post_url.rstrip('/').split('/')[-1]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(out)-1}건"
        )
        return out

    def _parse_article_time(self, art) -> datetime | None:
        """XenForo article 내 발행 시각 (data-timestamp 우선, ISO datetime 폴백)"""
        time_el = art.select_one("time")
        if not time_el:
            return None
        ts = time_el.get("data-timestamp")
        if ts:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except ValueError:
                pass
        iso = time_el.get("datetime")
        if iso:
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                pass
        return None

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        # 서브포럼 자체가 Galaxy 전용이므로, 키워드 없어도 통과 (포럼 = 시그널)
        # 하지만 명백한 잡스레드 필터 위해 None/공백만 차단
        if not text.strip():
            return False
        return True
