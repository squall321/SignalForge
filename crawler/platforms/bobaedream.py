"""
보배드림 크롤러 — httpx + BeautifulSoup
bobaedream.co.kr 자유게시판에서 Galaxy 관련 VOC 수집
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

# 보배드림은 IT/모바일 전용 게시판이 없음 → 자유게시판(freeb) 중심으로 수집
# (newcar/national 등 자동차 게시판은 Galaxy 글 빈도 낮아 제외)
BOBAE_BOARDS = [
    ("freeb", "자유게시판"),
]

BASE_URL = "https://www.bobaedream.co.kr"

# 상세 페이지 수집 최대 게시물 수
MAX_POSTS = 80
# 목록 스캔 페이지 수 (1-indexed)
LIST_PAGES = 5

BOARD_LIST_URL = "{base}/list?code={board}&page={page}"

# 제품 관련 검색 키워드 (clien.py와 동일 — Galaxy + 경쟁사)
GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "S울트라", "Ultra",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


class BobaeDreamCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "bobaedream", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in BOBAE_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_board_page(client, board_code, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(f"  보배드림 {board_name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  보배드림 {board_name} p{page} 실패: {e}")

            # 최신순 정렬 후 상위 MAX_POSTS건만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"보배드림 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  보배드림 상세 수집 실패 ({post.source_url}): {e}")

        # MX 필터 적용 (Data Clean 2 / D1)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"보배드림 수집 완료: {len(raw_vocs)}건 (MX 필터 적용 {before_n}→{len(raw_vocs)}, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_code: str, page: int
    ) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_code, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text)

    def _parse_board_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        # tbody > tr 중 공지/best가 아닌 일반 게시물만 사용
        # (class="notice"/"best"/"mall"는 제외 — 노출 위치 고정으로 중복 카운트 방지)
        table = soup.find("table")
        if not table:
            return results
        tbody = table.find("tbody")
        if not tbody:
            return results

        for tr in tbody.find_all("tr"):
            try:
                classes = tr.get("class") or []
                if any(c in classes for c in ("notice", "best", "mall")):
                    continue

                link_el = tr.select_one("a.bsubject")
                if not link_el:
                    continue

                title = (link_el.get("title") or link_el.get_text(strip=True)).strip()
                if not title:
                    continue

                href = link_el.get("href", "")
                # href에 &bm=1 등 부가 쿼리 포함 → 정규화하여 code+No만 사용
                m = re.search(r"code=([^&]+)&No=(\d+)", href)
                if not m:
                    continue
                code, no = m.group(1), m.group(2)
                post_url = f"{BASE_URL}/view?code={code}&No={no}"

                # 작성자
                author_el = tr.select_one("td.author02 span.author")
                author = author_el.get_text(strip=True) if author_el else "익명"
                if not author:
                    author = "익명"

                # 날짜 (목록은 'MM/DD' 또는 'HH:MM' 형식)
                date_el = tr.select_one("td.date")
                date_text = date_el.get_text(strip=True) if date_el else ""
                published_at = self._parse_list_date(date_text)

                # 추천수
                recomm_el = tr.select_one("td.recomm")
                like_count = self._safe_int(recomm_el.get_text(strip=True) if recomm_el else "0")

                # 댓글수 (span.Comment strong.totreply)
                cmt_el = tr.select_one("span.Comment strong.totreply")
                comment_count = self._safe_int(cmt_el.get_text(strip=True) if cmt_el else "0")

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
                logger.debug(f"보배드림 목록 행 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        """상세 페이지 → 본문 + 댓글 RawVOC 변환"""
        resp = await client.get(post.source_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_url = post.source_url
        title = post.content  # 목록에서 받은 제목 그대로 사용

        # 본문
        body_el = soup.select_one(".bodyCont")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 상세 페이지에서 더 정확한 게시 시각 추출 (목록은 MM/DD만 있음)
        published_at = post.published_at
        count_group = soup.select_one(".writerProfile .countGroup")
        if count_group:
            ctext = count_group.get_text(" ", strip=True)
            detail_dt = self._parse_detail_date(ctext)
            if detail_dt:
                published_at = detail_dt

        # 댓글 파싱: ul#cmt_reply > li > dl
        comment_vocs: List[RawVOC] = []
        idx = 0
        for li in soup.select("ul#cmt_reply > li"):
            # 삭제/차단 댓글 스킵
            li_cls = li.get("class") or []
            if "deleted" in li_cls or "blocked" in li_cls:
                continue

            body_dd = li.select_one('dd[id^="small_cmt_"]')
            if not body_dd:
                continue
            ctext = body_dd.get_text("\n", strip=True)
            if not ctext or len(ctext) < 2:
                continue

            idx += 1
            # 댓글 ID는 id="small_cmt_370310" 형식 — 안정적 식별자
            cmt_id_raw = body_dd.get("id", "")
            cmt_no_match = re.search(r"(\d+)", cmt_id_raw)
            cmt_id = cmt_no_match.group(1) if cmt_no_match else f"i{idx}"

            author_el = li.select_one("dt span.name span.author") or li.select_one("dt span.name")
            cauthor = author_el.get_text(strip=True) if author_el else "익명"
            if not cauthor:
                cauthor = "익명"

            date_el = li.select_one("dt span.date")
            cdate = self._parse_comment_date(date_el.get_text(strip=True)) if date_el else None

            # 댓글 추천 (updownbox > "추천 N")
            up_el = li.select_one(".updownbox dd.first a")
            clikes = 0
            if up_el:
                m = re.search(r"(\d+)", up_el.get_text(strip=True))
                if m:
                    clikes = int(m.group(1))

            comment_vocs.append(RawVOC(
                external_id=hashlib.md5(f"{post_url}#c{cmt_id}".encode()).hexdigest()[:16],
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
            published_at=published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        no_id = post_url.split("No=")[-1]
        logger.info(
            f"  보배드림 상세 No={no_id}: 본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _safe_int(text: str) -> int:
        try:
            return int(re.sub(r"[^\d]", "", text) or 0)
        except ValueError:
            return 0

    def _parse_list_date(self, text: str):
        """목록 페이지 날짜: 'MM/DD' (당해년도) 또는 'HH:MM' (당일)"""
        text = (text or "").strip()
        if not text:
            return None
        now = datetime.now(KST)
        try:
            if re.match(r"\d{2}:\d{2}$", text):
                return datetime.strptime(text, "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}/\d{2}$", text):
                mm, dd = text.split("/")
                return datetime(now.year, int(mm), int(dd), tzinfo=KST).astimezone(timezone.utc)
            if re.match(r"\d{4}\.\d{2}\.\d{2}$", text):
                return datetime.strptime(text, "%Y.%m.%d").replace(tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    def _parse_detail_date(self, text: str):
        """상세 페이지의 'YYYY.MM.DD (요일) HH:MM' 패턴 추출"""
        m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s*\([^)]+\)\s*(\d{2}):(\d{2})", text)
        if not m:
            return None
        try:
            y, mo, d, h, mi = map(int, m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_comment_date(self, text: str):
        """댓글 날짜: 'YY.MM.DD HH:MM' 또는 'YYYY.MM.DD HH:MM'"""
        text = (text or "").strip()
        if not text:
            return None
        try:
            m = re.match(r"(\d{2,4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", text)
            if m:
                y, mo, d, h, mi = m.groups()
                y_int = int(y)
                if y_int < 100:
                    y_int += 2000
                return datetime(y_int, int(mo), int(d), int(h), int(mi), tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            pass
        return None
