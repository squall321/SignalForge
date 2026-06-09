"""
Theqoo (더쿠) 크롤러 — httpx + BeautifulSoup
theqoo.net hot/it 게시판에서 Galaxy/스마트폰 관련 VOC 수집
XE/Rhymix 기반. 댓글은 AJAX(JSON)으로 따로 로드.
"""
import hashlib
import json
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

BASE_URL = "https://theqoo.net"

KST = timezone(timedelta(hours=9))

# 더쿠 크롤링 대상 게시판
# - hot: HOT 통합 (Galaxy/연예/이슈 다양 — 강한 키워드 필터 필요)
# - it : IT 게시판 (디지털/스마트폰 글이 가장 자주 등장)
THEQOO_BOARDS = [
    ("it",  "IT"),
    ("hot", "HOT"),
]

# 상세 페이지에서 본문/댓글을 수집할 최대 게시물 수
MAX_POSTS = 80
# 목록 스캔 페이지 수 (1-indexed)
LIST_PAGES = 5

# 제품 관련 검색 키워드
GALAXY_KEYWORDS = [
    # Galaxy
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22",
    "Fold", "Flip", "폴드", "플립", "버즈", "Buds",
    "워치", "Watch", "링", "Ring", "Ultra", "울트라",
    # 경쟁사 (비교용)
    "iPhone", "아이폰", "Pixel", "픽셀",
]

# 댓글 AJAX 엔드포인트
COMMENT_AJAX_URL = f"{BASE_URL}/index.php"
COMMENT_AJAX_ACT = "dispTheqooContentCommentListTheqoo"


class TheqooCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "theqoo", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in THEQOO_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_board_page(client, board_code, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(
                            f"  Theqoo {board_name} p{page}: {len(filtered)}/{len(posts)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Theqoo {board_name} p{page} 실패: {e}")

            # 최신순 정렬 후 상위 MAX_POSTS건만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Theqoo 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(
                        f"  Theqoo 상세 수집 실패 ({post.source_url}): {e}"
                    )

        # MX 필터 적용 (Data Clean 2 / D1)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Theqoo 수집 완료: {len(raw_vocs)}건 (MX 필터 적용 {before_n}→{len(raw_vocs)}, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    # ------------------------ 목록 ------------------------
    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_code: str, page: int
    ) -> List[RawVOC]:
        url = f"{BASE_URL}/{board_code}?page={page}"
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text, board_code)

    def _parse_board_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for row in soup.select("tbody tr"):
            classes = row.get("class") or []
            # 공지/이벤트 행 스킵
            if any(c in classes for c in ("notice", "notice_expand", "nofn")):
                continue

            title_cell = row.select_one("td.title")
            if not title_cell:
                continue
            link_el = title_cell.select_one("a[href]")
            if not link_el:
                continue
            href = link_el.get("href", "")
            # 게시판 내부 글 링크만 (예: /it/4219... 또는 /hot/4219...)
            if not href.startswith(f"/{board_code}/"):
                continue
            # /it/<post_id> 형태 — 후속 쿼리 제거
            m = re.match(rf"^/{board_code}/(\d+)", href)
            if not m:
                continue
            post_id = m.group(1)
            post_url = f"{BASE_URL}/{board_code}/{post_id}"

            # 제목 (a 안의 첫 텍스트 — replyNum 등 제외)
            # title cell 안에 a 태그가 2개 (본문링크 + replyNum) 인 경우가 있어 첫 a만 사용
            title = link_el.get_text(" ", strip=True)
            if not title:
                continue

            # 카테고리
            cate_el = row.select_one("td.cate")
            category = cate_el.get_text(strip=True) if cate_el else ""

            # 날짜 (MM.DD 또는 HH:MM)
            time_el = row.select_one("td.time")
            published_at = (
                self._parse_theqoo_list_date(time_el.get_text(strip=True))
                if time_el else None
            )

            # 댓글 수 (td.title 안의 a.replyNum)
            reply_el = title_cell.select_one("a.replyNum")
            comment_count = 0
            if reply_el:
                try:
                    comment_count = int(re.sub(r"[^\d]", "", reply_el.get_text(strip=True)) or 0)
                except ValueError:
                    pass

            # 조회수 (td.m_no) — likes 대용 신호 없음. engagement에 도움
            view_el = row.select_one("td.m_no")
            views = 0
            if view_el:
                try:
                    views = int(re.sub(r"[^\d]", "", view_el.get_text(strip=True)) or 0)
                except ValueError:
                    pass

            uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
            results.append(RawVOC(
                external_id=uid,
                content=(f"[{category}] {title}" if category else title),
                source_url=post_url,
                author_name="무명의 더쿠",  # 더쿠는 기본 익명
                published_at=published_at,
                likes_count=0,
                comments_count=comment_count,
                country_code="KR",
                meta={"board": board_code, "post_id": post_id, "views": views},
            ))

        return results

    # ------------------------ 상세 ------------------------
    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        post_url = post.source_url
        resp = await client.get(post_url)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 제목 — window.share_title 우선, 없으면 리스트 제목
        title_match = re.search(r'window\.share_title\s*=\s*"([^"]+)"', html)
        title = title_match.group(1) if title_match else post.content

        # 본문
        body_el = (
            soup.select_one(".rd_body .xe_content")
            or soup.select_one(".rd_body")
            or soup.select_one(".xe_content")
        )
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 정확한 작성일시 — .side 영역에 'YYYY.MM.DD HH:MM' 형태
        side_el = soup.select_one(".rd_hd .side") or soup.select_one(".side")
        post_dt = post.published_at
        if side_el:
            dt = self._parse_theqoo_detail_date(side_el.get_text(" ", strip=True))
            if dt:
                post_dt = dt

        # CSRF 토큰 추출 (댓글 AJAX용)
        csrf_match = re.search(
            r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html
        )
        csrf_token = csrf_match.group(1) if csrf_match else None

        post_id = post.meta.get("post_id") or post_url.rsplit("/", 1)[-1]

        # 댓글 수집 (AJAX)
        comment_vocs: List[RawVOC] = []
        if csrf_token:
            try:
                comments = await self._fetch_comments_ajax(
                    client, int(post_id), csrf_token, post_url
                )
                comment_vocs = self._build_comment_vocs(comments, post_url)
            except Exception as e:
                logger.debug(f"  Theqoo 댓글 AJAX 실패 ({post_url}): {e}")

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post_dt,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs) or post.comments_count,
            country_code="KR",
        )

        logger.info(
            f"  Theqoo 상세 {post_id}: 본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    async def _fetch_comments_ajax(
        self,
        client: httpx.AsyncClient,
        document_srl: int,
        csrf_token: str,
        referer: str,
    ) -> List[dict]:
        """더쿠 댓글 JSON 엔드포인트 호출. cpage=0 → 전체(또는 최신 1페이지)."""
        payload = {
            "act": COMMENT_AJAX_ACT,
            "document_srl": document_srl,
            "cpage": 0,
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf_token,
            "Content-Type": "application/json; charset=utf-8",
            "Referer": referer,
        }
        resp = await client.post(COMMENT_AJAX_URL, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return []
        comments = data.get("comment_list") or []
        return comments if isinstance(comments, list) else []

    def _build_comment_vocs(
        self, comments: List[dict], post_url: str
    ) -> List[RawVOC]:
        vocs: List[RawVOC] = []
        for idx, c in enumerate(comments, start=1):
            srl = c.get("srl")
            ct_html = c.get("ct") or ""
            # HTML → 텍스트
            ctext = BeautifulSoup(ct_html, "html.parser").get_text(" ", strip=True)
            if not ctext or len(ctext) < 2:
                continue
            cdate = self._parse_theqoo_comment_date(c.get("rd", ""))
            stable_key = srl if srl is not None else f"i{idx}"
            vocs.append(RawVOC(
                external_id=hashlib.md5(
                    f"{post_url}#c{stable_key}".encode()
                ).hexdigest()[:16],
                content=ctext,
                source_url=post_url,
                author_name="무명의 더쿠",
                published_at=cdate,
                likes_count=0,
                country_code="KR",
            ))
        return vocs

    # ------------------------ 헬퍼 ------------------------
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_theqoo_list_date(self, text: str):
        """리스트의 'MM.DD' 또는 'HH:MM' 파싱 → UTC datetime (입력은 KST 기준)"""
        text = text.strip()
        now_kst = datetime.now(KST)
        try:
            if re.match(r"^\d{2}:\d{2}$", text):
                hh, mm = text.split(":")
                return now_kst.replace(
                    hour=int(hh), minute=int(mm), second=0, microsecond=0
                ).astimezone(timezone.utc)
            if re.match(r"^\d{2}\.\d{2}$", text):
                mo, d = text.split(".")
                return now_kst.replace(
                    month=int(mo), day=int(d),
                    hour=0, minute=0, second=0, microsecond=0,
                ).astimezone(timezone.utc)
            if re.match(r"^\d{4}\.\d{2}\.\d{2}$", text):
                return datetime.strptime(text, "%Y.%m.%d").replace(tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    def _parse_theqoo_detail_date(self, text: str):
        """상세 페이지 'YYYY.MM.DD HH:MM' 파싱"""
        m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", text)
        if not m:
            return None
        try:
            y, mo, d, h, mi = (int(x) for x in m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_theqoo_comment_date(self, text: str):
        """댓글 JSON의 'YYYYMMDDHHMMSS' 파싱"""
        if not text or not re.match(r"^\d{14}$", text):
            return None
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            return None
