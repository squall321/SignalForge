"""
SLRClub 크롤러 — httpx + BeautifulSoup
slrclub.com 자유게시판 / 이슈토론방 / PC게시판에서 갤럭시·스마트폰 카메라 VOC 수집.

특이사항
- 페이지네이션이 절대 페이지번호(예: page=800430) 방식이라
  첫 페이지(파라미터 없음)에서 "다음 페이지" 링크의 page 번호를 동적 추출 후 감소.
- 댓글은 JS로 ajax 로드 → POST /bbs/comment_db/load.php (JSON).
- 본문 컨테이너: #userct, 작성일은 td.date span[title] 의 한글 포맷.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.slrclub.com"
KST = timezone(timedelta(hours=9))

# SLRClub은 카메라 사이트지만 자유게시판/인기글에서 휴대폰 카메라/갤럭시 토론이 활발.
# 'discuss'(이슈토론방)는 비로그인 접근 차단되므로 제외.
SLR_BOARDS = [
    ("free",          "자유게시판"),
    ("hot_article",   "인기글"),
    ("samsung_forum", "삼성포럼"),
]

# 목록 스캔 페이지 수 / 상세 수집 최대치
LIST_PAGES = 12
MAX_POSTS = 150

GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "Ultra", "울트라",
    "삼성", "Samsung",
    # 비교/경쟁사 (카메라 비교 토론 흡수)
    "iPhone", "아이폰", "Pixel", "픽셀",
]


class SLRClubCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "slrclub", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in SLR_BOARDS:
                try:
                    pages = await self._collect_board(client, board_code, board_name)
                    list_posts.extend(pages)
                except Exception as e:
                    logger.warning(f"  SLRClub {board_name} 실패: {e}")

            # 최신순 정렬, MAX_POSTS 캡
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"SLRClub 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  SLRClub 상세 수집 실패 ({post.source_url}): {e}")

        # MX 필터 적용 (Data Clean 2 / D1)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"SLRClub 수집 완료: {len(raw_vocs)}건 (MX 필터 적용 {before_n}→{len(raw_vocs)}, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    # ---------------------- 목록 ----------------------

    async def _collect_board(
        self, client: httpx.AsyncClient, board_code: str, board_name: str
    ) -> List[RawVOC]:
        """게시판 페이지를 LIST_PAGES만큼 스캔."""
        results: List[RawVOC] = []

        # 1) 첫 페이지 — page 파라미터 없이 호출 (최신).
        first_url = f"{BASE_URL}/bbs/zboard.php?id={board_code}"
        resp = await client.get(first_url)
        resp.raise_for_status()
        first_html = resp.text
        posts = self._parse_board_list(first_html, board_code)
        filtered = [p for p in posts if self._is_galaxy_related(p)]
        results.extend(filtered)
        logger.info(f"  SLRClub {board_name} p1: {len(filtered)}/{len(posts)}건")

        # 2) 다음 페이지 page 번호 동적 추출 → 감소
        next_page = self._extract_next_page_num(first_html, board_code)
        if next_page is None:
            return results

        for i in range(1, LIST_PAGES):
            page_no = next_page - (i - 1)
            if page_no <= 0:
                break
            try:
                url = f"{BASE_URL}/bbs/zboard.php?id={board_code}&page={page_no}"
                await self._random_delay()
                resp = await client.get(url)
                resp.raise_for_status()
                posts = self._parse_board_list(resp.text, board_code)
                filtered = [p for p in posts if self._is_galaxy_related(p)]
                results.extend(filtered)
                logger.info(
                    f"  SLRClub {board_name} page={page_no}: {len(filtered)}/{len(posts)}건"
                )
            except Exception as e:
                logger.warning(f"  SLRClub {board_name} page={page_no} 실패: {e}")
        return results

    def _extract_next_page_num(self, html: str, board_code: str) -> Optional[int]:
        """첫 페이지 HTML에서 다음 페이지에 해당하는 page 번호 추출.
        SLRClub은 page=80043x 같은 절대 카운터를 쓰므로 정적으로 1,2,3 못 씀.
        """
        # page=숫자 패턴 중 가장 큰 값 = 두 번째 페이지
        # (페이지 네비게이션 링크들에 page=NNN,NN-1,... 식으로 노출됨)
        nums = re.findall(rf"id={re.escape(board_code)}&page=(\d+)", html)
        nums = [int(n) for n in nums if n.isdigit()]
        if not nums:
            return None
        # 최댓값이 직전 페이지 (큰 숫자가 최신 직전)
        return max(nums)

    def _parse_board_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for row in soup.select("tr"):
            try:
                sbj = row.find("td", class_="sbj")
                if not sbj:
                    continue
                # 공지/이벤트 행 (vx2.php?id=help|notice|event|marketinfo) 제외
                a = sbj.find("a", href=True)
                if not a:
                    continue
                href = a["href"]
                if not href.startswith("/bbs/vx2.php"):
                    continue
                # 해당 board의 글만 포함
                m = re.search(r"id=([a-zA-Z_0-9]+)&no=(\d+)", href)
                if not m:
                    continue
                href_board, post_no = m.group(1), m.group(2)
                if href_board != board_code:
                    continue

                title = a.get_text(strip=True)
                if not title:
                    continue

                post_url = f"{BASE_URL}{href}"

                # 댓글 수: td.sbj 끝의 "[숫자]" 패턴
                sbj_text = sbj.get_text(" ", strip=True)
                cm = re.search(r"\[(\d+)\]\s*$", sbj_text)
                comment_count = int(cm.group(1)) if cm else 0

                # 작성자: td.name > a > span 또는 span.lop
                author = "익명"
                name_td = row.find("td", class_="list_name")
                if name_td:
                    span = name_td.find("span")
                    if span:
                        author = span.get_text(strip=True) or "익명"
                    else:
                        author = name_td.get_text(strip=True) or "익명"

                # 날짜: td.list_date — title 속성에 "YY/MM/DD HH:MM" 또는 "HH:MM" 표시
                date_td = row.find("td", class_="list_date")
                published_at = None
                if date_td:
                    raw_date = date_td.get("title") or date_td.get_text(strip=True)
                    published_at = self._parse_slr_date(raw_date)

                # 추천/조회
                vote_td = row.find("td", class_="list_vote")
                like_count = 0
                if vote_td:
                    try:
                        like_count = int(re.sub(r"[^\d]", "", vote_td.get_text(strip=True)) or 0)
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
                    meta={"board": board_code, "post_no": post_no},
                ))
            except Exception as e:
                logger.debug(f"SLRClub list row 파싱 실패: {e}")
        return results

    # ---------------------- 상세 ----------------------

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        post_url = post.source_url
        resp = await client.get(post_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = post.content

        # 본문 — #userct
        body_el = soup.select_one("#userct")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        # 작성일 — td.date span[title="2026년 05월 29일 19시 05분 45초"]
        published_at = post.published_at
        date_span = soup.select_one("td.date span[title]")
        if date_span:
            t = date_span.get_text(strip=True)
            d = self._parse_slr_detail_date(t)
            if d:
                published_at = d

        # 추천수
        likes = post.likes_count
        vote_td = soup.select_one("td.vote")
        if vote_td:
            try:
                likes = int(re.sub(r"[^\d]", "", vote_td.get_text(strip=True)) or 0)
            except ValueError:
                pass

        # 댓글 — AJAX 호출
        comment_vocs = await self._fetch_comments(client, soup, post_url)

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=published_at,
            likes_count=likes,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        logger.info(
            f"  SLRClub 상세 {post_url.split('=')[-1]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        soup: BeautifulSoup,
        post_url: str,
    ) -> List[RawVOC]:
        """comment_box data-* → POST /bbs/comment_db/load.php"""
        box = soup.select_one("#comment_box")
        if not box:
            return []

        bbsid = box.get("data-bbsid")
        tos = box.get("data-tos")
        cmrno = box.get("data-cmrno")
        cmx_raw = box.get("data-cmx") or "0"
        try:
            cmx = int(cmx_raw)
        except ValueError:
            cmx = 0
        if not (bbsid and tos and cmrno) or cmx == 0:
            return []

        try:
            resp = await client.post(
                f"{BASE_URL}/bbs/comment_db/load.php",
                data={
                    "id": bbsid,
                    "tos": tos,
                    "no": cmrno,
                    "sno": 1,
                    "spl": 300,  # 최대 출력 수
                    "mno": cmx,
                    "gp": "",
                    "ksearch": "",
                },
                headers={"Referer": post_url},
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.debug(f"SLRClub 댓글 ajax 실패 ({post_url}): {e}")
            return []

        results: List[RawVOC] = []
        for c in payload.get("c", []) or []:
            try:
                if int(c.get("del", 0) or 0) == 1:
                    continue
                memo = c.get("memo") or ""
                # HTML/특수문자 정리
                text = BeautifulSoup(memo, "html.parser").get_text("\n", strip=True)
                text = re.sub(r"\n{2,}", "\n", text).strip()
                if not text or len(text) < 5:
                    continue

                pk = c.get("pk") or ""
                if not pk:
                    continue

                cdate = self._parse_slr_detail_date(c.get("dt") or "")
                clikes = 0
                try:
                    clikes = int(c.get("vt") or 0)
                except (ValueError, TypeError):
                    clikes = 0

                results.append(RawVOC(
                    external_id=hashlib.md5(f"{post_url}#c{pk}".encode()).hexdigest()[:16],
                    content=text,
                    source_url=post_url,
                    author_name=c.get("name") or "익명",
                    published_at=cdate,
                    likes_count=clikes,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"SLRClub 댓글 항목 파싱 실패: {e}")
        return results

    # ---------------------- 유틸 ----------------------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_slr_date(self, text: str) -> Optional[datetime]:
        """목록 페이지 날짜: 'YY/MM/DD', 'HH:MM', 'HH:MM:SS' 등 표시."""
        if not text:
            return None
        text = text.strip()
        try:
            if re.match(r"\d{2}/\d{2}/\d{2}", text):
                return datetime.strptime(text[:8], "%y/%m/%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}:\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:8], "%H:%M:%S").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    def _parse_slr_detail_date(self, text: str) -> Optional[datetime]:
        """상세/댓글 날짜:
        - '2026/05/29 19:05:45'
        - '2026년 05월 29일 19시 05분 45초'
        """
        if not text:
            return None
        text = text.strip()
        # ISO-ish
        try:
            if re.match(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y/%m/%d %H:%M:%S").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}", text):
                return datetime.strptime(text[:16], "%Y/%m/%d %H:%M").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{4}/\d{2}/\d{2}", text):
                return datetime.strptime(text[:10], "%Y/%m/%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        # 한글 포맷
        m = re.match(
            r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2})시\s*(\d{1,2})분(?:\s*(\d{1,2})초)?",
            text,
        )
        if m:
            try:
                y, mo, d, h, mi, se = (int(g) if g else 0 for g in m.groups())
                return datetime(y, mo, d, h, mi, se, tzinfo=KST).astimezone(timezone.utc)
            except Exception:
                pass
        return None
