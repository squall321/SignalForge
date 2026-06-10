"""
Clien 크롤러 — httpx + BeautifulSoup
clien.net 모바일/스마트폰 게시판에서 삼성 Galaxy 관련 VOC 수집
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

# Clien 크롤링 대상 게시판
CLIEN_BOARDS = [
    ("use",      "사용기"),      # 사용기 (전자기기 전반)
    ("cm_andro", "안드로메당"),  # 안드로이드/Galaxy 전용
    ("news",     "새로운소식"), # 뉴스/정보
]

BASE_URL = "https://www.clien.net"

KST = timezone(timedelta(hours=9))

# 상세 페이지에서 본문/댓글을 수집할 최대 게시물 수 (최신순)
# BACKFILL_MAX_POSTS 가 설정되면 그 값(>=1)으로 cap 해제
def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(min_value, v)


MAX_POSTS = _env_int("CLIEN_MAX_POSTS", 150)
# 목록 스캔 페이지 수 (0-indexed range 상한)
# BACKFILL_PAGES 환경변수로 옛 글 백카탈로그 수집 시 50~100 까지 확장
LIST_PAGES = _env_int("CLIEN_BACKFILL_PAGES", 12)

# 게시판 → 페이지 URL 패턴
BOARD_LIST_URL = "{base}/service/board/{board}?&od=T31&po={page}"

# 제품 관련 검색 키워드
GALAXY_KEYWORDS = [
    # Galaxy 2025-26 + 구세대
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "S울트라", "Ultra",
    # 경쟁사 (시기별 비교용 — 커뮤니티 폰 게시판 대상)
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: ClienCrawler — [[crawler#Platform Strategy]] 참조.
class ClienCrawler(BaseCrawler):
    # Harvest 3 트랙 A: 단일 UA + 짧은 sleep → 24h voc 36건 (정상 200~500).
    # 매 요청 UA/Accept-Language 회전 + sleep 3~6s 로 봇 패턴 회피.
    MIN_DELAY = 3.0
    MAX_DELAY = 6.0
    # 보드 페이지 사이 추가 jitter (목록 fetch 빠르게 도는 패턴 완화)
    BOARD_PAGE_EXTRA_JITTER = 1.5

    def __init__(self, platform_code: str = "clien", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in CLIEN_BOARDS:
                for page in range(0, LIST_PAGES):  # 최근 N페이지
                    try:
                        posts = await self._fetch_board_page(client, board_code, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(f"  Clien {board_name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                        # 보드 페이지 추가 jitter
                        import asyncio as _asyncio
                        import random as _random
                        await _asyncio.sleep(_random.uniform(0, self.BOARD_PAGE_EXTRA_JITTER))
                    except Exception as e:
                        logger.warning(f"  Clien {board_name} p{page} 실패: {e}")

            # 최신순으로 정렬 후 상위 MAX_POSTS건만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Clien 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  Clien 상세 수집 실패 ({post.source_url}): {e}")

        # 2026-06-10 r6 Stage 3a: MX 통합 키워드 필터 강제 (Data Clean 1-5 정책 통일 — clien 만 누락이었음)
        from nlp.mx_keywords import is_mx_relevant
        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Clien 수집 완료: {len(raw_vocs)}/{before}건 (MX 필터 적용, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        """게시물 상세 페이지에서 본문 + 댓글을 RawVOC로 변환"""
        # Harvest 3 트랙 A: 매 호출 UA 회전 + Accept/Referer 보강
        resp = await self.fetch_with_rotated_ua(
            client, post.source_url,
            extra_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{BASE_URL}/",
            },
        )
        if resp is None:
            raise httpx.HTTPError(f"clien detail fetch returned None: {post.source_url}")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_url = post.source_url
        title = post.content  # 리스트에서 받은 제목

        # 본문
        body_el = soup.select_one(".post_content") or soup.select_one(".post_article")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 댓글 파싱
        comment_rows = soup.select(".comment_row")
        comment_vocs: List[RawVOC] = []
        idx = 0
        for row in comment_rows:
            row_classes = row.get("class") or []
            # 삭제/차단 댓글 스킵
            if "blocked" in row_classes or "deleted" in row_classes:
                continue

            view_el = row.select_one(".comment_view")
            if not view_el:
                continue
            ctext = view_el.get_text("\n", strip=True)
            if not ctext or len(ctext) < 5:
                # 이미지/스티커 전용 댓글 등 본문 없는 댓글 스킵
                continue

            idx += 1
            # 안정적 댓글 ID 우선 (주기 재크롤 시 중복 방지). 없으면 순번 fallback.
            csn = row.get("data-comment-sn") or f"i{idx}"

            author_el = row.select_one(".nickname")
            cauthor = author_el.get_text(strip=True) if author_el else "익명"
            if not cauthor:
                cauthor = "익명"

            date_el = row.select_one(".comment_time .timestamp")
            cdate = self._parse_clien_date(date_el.get_text(strip=True)) if date_el else None

            symph_el = row.select_one(".comment_content_symph")
            try:
                clikes = int(re.sub(r"[^\d]", "", symph_el.get_text(strip=True)) or 0) if symph_el else 0
            except ValueError:
                clikes = 0

            comment_vocs.append(RawVOC(
                external_id=hashlib.md5(f"{post_url}#c{csn}".encode()).hexdigest()[:16],
                content=ctext,
                source_url=post_url,
                author_name=cauthor,
                published_at=cdate,
                likes_count=clikes,
                country_code="KR",
            ))

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post.published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        logger.info(
            f"  Clien 상세 {post_url.split('/')[-1].split('?')[0]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    async def _fetch_board_page(self, client: httpx.AsyncClient, board_code: str, page: int) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_code, page=page)
        # Harvest 3 트랙 A: 매 호출 UA 회전 + Accept/Referer 보강
        resp = await self.fetch_with_rotated_ua(
            client, url,
            extra_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{BASE_URL}/",
            },
        )
        if resp is None:
            raise httpx.HTTPError(f"clien list fetch returned None: {url}")
        resp.raise_for_status()
        return self._parse_board_list(resp.text, board_code)

    def _parse_board_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select(".list_item:not(.notice):not(.hongbo)"):
            try:
                link_el = item.select_one("a.list_subject")
                if not link_el:
                    continue
                # span.subject_fixed 가 실제 제목, span.category 는 게시판 분류
                title_span = link_el.select_one("span.subject_fixed") or link_el.select_one("span[data-role='list-title-text']")
                title = title_span.get("title", "") or title_span.get_text(strip=True) if title_span else link_el.get_text(strip=True)
                if not title:
                    continue

                href = link_el.get("href", "")
                # href에 쿼리스트링 포함돼 있으므로 그대로 사용
                post_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                # 날짜: .list_time > span.time > span.timestamp 에 전체 날짜
                date_el = item.select_one(".list_time .timestamp")
                date_text = date_el.get_text(strip=True) if date_el else ""
                published_at = self._parse_clien_date(date_text)

                # 작성자
                author_el = item.select_one(".nickname em")
                if not author_el:
                    author_el = item.select_one(".nickname")
                author = author_el.get_text(strip=True) if author_el else "익명"

                # 댓글/추천 수
                comment_el = item.select_one(".list_reply")
                comment_count_text = comment_el.get_text(strip=True) if comment_el else "0"
                try:
                    comment_count = int(re.sub(r"[^\d]", "", comment_count_text) or 0)
                except ValueError:
                    comment_count = 0

                like_el = item.select_one(".list_sympathy")
                like_count_text = like_el.get_text(strip=True) if like_el else "0"
                try:
                    like_count = int(re.sub(r"[^\d]", "", like_count_text) or 0)
                except ValueError:
                    like_count = 0

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=like_count,
                    comments_count=comment_count,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"Clien 게시물 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        """Galaxy 관련 게시물 필터링"""
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_clien_date(self, text: str):
        """'2026-05-15 14:32:01', '2026-05-15 14:32', '14:37' (오늘) 파싱"""
        text = text.strip()
        try:
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).astimezone(timezone.utc)
            elif re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
                return datetime.strptime(text[:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST).astimezone(timezone.utc)
            elif re.match(r"\d{4}-\d{2}-\d{2}", text):
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=KST).astimezone(timezone.utc)
            elif re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                t = datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
                return t
        except Exception:
            pass
        return None
