"""
Instiz 크롤러 — httpx + BeautifulSoup
instiz.net 일상(/name) / 이슈(/pt) / 자유잡담(/free) 게시판에서 일반 VOC 수집

사이트 특성:
  - 인스티즈는 K-POP/일상/연예 위주 커뮤니티이며, IT/스마트폰 전용 게시판이 없음
  - 제목 키워드 필터(갤럭시 등)를 적용하면 408건/12페이지 중 매치가 거의 0건 → 무의미
  - 따라서 제목 키워드 필터를 적용하지 않고 최신 글을 수집
  - product_code 매핑은 BaseCrawler.normalize → infer_product_code(본문)에 위임

구조 노트:
  - 목록: tr#detour (5 셀: 카테고리/제목+댓글수/시간/조회수/추천수). 작성자 표시 없음 → "익명".
  - 본문: div#memo_content_1 (article > td#content_td)
  - 댓글: div#ajax_comment > tr.cmt_view, id="tr<NUM>" (안정 ID), 작성자 #com<NUM>, 본문 #n<NUM>,
          시간 .minitext onmouseover='$(this).html("YYYY/M/D H:M:S")' (정확한 KST)
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

BASE_URL = "https://www.instiz.net"
KST = timezone(timedelta(hours=9))

# Instiz 크롤링 대상 게시판
# 인티&이슈/팁&강좌 등 IT 토론이 활발한 보드 우선
INSTIZ_BOARDS = [
    ("name", "일상"),    # 일상 — 팁/자료, 정보/소식 카테고리 포함 (IT/스마트폰 토론)
    ("pt",   "이슈"),    # 이슈 — 정보·기타 카테고리에 IT 이슈 포함
    ("free", "자유잡담"), # 자유잡담 — 일상 잡담 (제품 사용기 산발)
]

# 게시판 목록 URL — desktop view 강제 위해 mobile=0 쿠키 사용
BOARD_LIST_URL = "{base}/{board}?page={page}"

# 상세 페이지로 본문/댓글까지 긁을 게시물 최대 개수
# 인스티즈는 IT 토픽 비중이 낮아 다른 한국 사이트(150)보다 보수적으로 설정
MAX_POSTS = 60
# 목록 스캔 페이지 수 (1-indexed range 상한 = LIST_PAGES+1)
LIST_PAGES = 5

GALAXY_KEYWORDS = [
    # Galaxy 2025-26 + 구세대
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "Galaxy Ring", "갤링", "Ultra", "울트라",
    # 삼성 전자/모바일 컨텍스트만 (야구팀 "삼성"과 분리)
    "삼성전자", "삼성폰", "삼전", "Samsung Galaxy",
    # 경쟁사 비교용
    "iPhone", "아이폰", "Pixel", "픽셀",
]

# 회원 전용 글 차단 안내문 (본문 대신 표시됨) — 본문에서 제거
RESTRICTED_MARKERS = (
    "회원에게만 공개",
    "로그인 후 이용",
    "로그인된 인스티즈앱",
)


# @lat: InstizCrawler — [[crawler#Platform Strategy]] 참조.
class InstizCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "instiz", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    def _make_httpx_client(self) -> httpx.AsyncClient:
        # mobile=0 쿠키로 데스크탑 뷰 강제 (모바일 뷰는 구조 다름)
        return httpx.AsyncClient(
            headers={
                "User-Agent": self._random_ua(),
                "Referer": BASE_URL + "/",
            },
            cookies={"mobile": "0"},
            timeout=30.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in INSTIZ_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_board_page(client, board_code, page)
                        # 인스티즈는 IT 전용 보드가 없어 제목 키워드 필터링 시 매치율 0
                        # → 제목 단계 필터 생략, 본문+댓글 단계에서 키워드 필터 적용
                        list_posts.extend(posts)
                        logger.info(f"  Instiz {board_name} p{page}: {len(posts)}건")
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Instiz {board_name} p{page} 실패: {e}")

            # 중복 URL 제거 후 최신순 정렬 (목록에 동일 게시물이 중복 노출되는 경우 대비)
            seen = set()
            unique_posts = []
            for p in list_posts:
                if p.source_url in seen:
                    continue
                seen.add(p.source_url)
                unique_posts.append(p)
            unique_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = unique_posts[:MAX_POSTS]
            logger.info(
                f"Instiz 리스트 {len(unique_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  Instiz 상세 수집 실패 ({post.source_url}): {e}")

        # 2026-06-08 C3: MX 통합 키워드 필터 강제
        from nlp.mx_keywords import is_mx_relevant
        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Instiz 수집 완료: {len(raw_vocs)}/{before}건 (MX 필터 적용)"
        )
        return raw_vocs

    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_code: str, page: int
    ) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_code, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text, board_code)

    def _parse_board_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        # 실제 게시물 행: tr#detour (공지/광고 제외 자동)
        for row in soup.select("tr#detour"):
            try:
                cells = row.find_all("td", recursive=False)
                if len(cells) < 3:
                    continue

                # cell0: 카테고리 (예: "이슈·소식")
                category = cells[0].get_text(strip=True)

                # cell1: 제목 + 댓글수 (a > span)
                subj_cell = cells[1]
                a_el = subj_cell.find("a")
                if not a_el:
                    continue
                href = a_el.get("href", "")
                if not href:
                    continue
                raw_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                # URL 정규화: 쿼리스트링 제거 (page/category/green 등으로 중복 노출됨)
                post_url = raw_url.split("?")[0]

                # 제목 텍스트 — span.texthead_notice 내부 텍스트, 댓글수(cmt3) 제외
                title_span = a_el.select_one("span.texthead_notice") or a_el.select_one("span") or a_el
                # 댓글수 span 제거하고 텍스트 추출
                cmt_el = title_span.select_one(".cmt3, .cmt, .cmt2")
                comment_count = 0
                if cmt_el:
                    try:
                        comment_count = int(re.sub(r"[^\d]", "", cmt_el.get_text(strip=True)) or 0)
                    except ValueError:
                        comment_count = 0
                    cmt_el.extract()  # 제목에서 댓글수 제거
                title = title_span.get_text(" ", strip=True)
                if not title:
                    continue

                # cell2: 시간 (HH:MM 또는 YY/MM/DD)
                date_text = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                published_at = self._parse_instiz_date(date_text)

                # cell3: 조회수 (참고)
                # cell4: 추천수
                like_count = 0
                if len(cells) > 4:
                    try:
                        like_count = int(re.sub(r"[^\d]", "", cells[4].get_text(strip=True)) or 0)
                    except ValueError:
                        like_count = 0

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name="익명",  # instiz 목록에 작성자 없음
                    published_at=published_at,
                    likes_count=like_count,
                    comments_count=comment_count,
                    country_code="KR",
                    meta={"board": board_code, "category": category},
                ))
            except Exception as e:
                logger.debug(f"Instiz 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        post_url = post.source_url
        resp = await client.get(post_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = post.content.strip()

        # --- 본문 ---
        body_el = soup.select_one("#memo_content_1") or soup.select_one(".memo_content")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
        # 회원 전용 글이면 본문 마커 제거 (제목만 남김)
        if any(m in body_text for m in RESTRICTED_MARKERS):
            body_text = ""

        # --- 댓글 ---
        comment_vocs: List[RawVOC] = []
        idx = 0
        for tr in soup.select("tr.cmt_view"):
            tr_id = tr.get("id", "")  # 예: "tr153310297"
            # 안정 댓글 ID 추출 (재크롤 중복 방지)
            stable_id = tr_id.replace("tr", "") if tr_id.startswith("tr") else ""

            # 본문: comment_line 내부 span#n<NUM>
            text_el = tr.select_one(".comment_line span[id^=n]")
            if not text_el:
                # fallback: comment_line 전체에서 minitext(시간) 제외한 텍스트
                cl = tr.select_one(".comment_line")
                if not cl:
                    continue
                ctext = cl.get_text("\n", strip=True)
            else:
                ctext = text_el.get_text("\n", strip=True)
            ctext = re.sub(r"\n{2,}", "\n", ctext).strip()

            if not ctext or len(ctext) < 3:
                # 이미지/스티커 전용 댓글 등
                continue
            if "삭제된" in ctext[:20]:
                continue
            # 회원 전용 잠금 안내문 ("1시간 내 작성된 댓글은…") — 본문이 아닌 안내 문구는 drop
            if any(m in ctext for m in RESTRICTED_MARKERS) or "회원만 볼 수" in ctext:
                continue
            # 빈 본문 placeholder
            if ctext.strip() == "(내용 없음)":
                continue

            # 작성자: span#com<NUM> 내부 .href u (글쓴이) 또는 nickname
            cauthor = "익명"
            auth_el = tr.select_one("span[id^=com] .href u") or tr.select_one("span[id^=com] .href")
            if auth_el:
                a_txt = auth_el.get_text(strip=True)
                if a_txt:
                    cauthor = a_txt
            else:
                # 일반 익명 닉: span[id^=com] 안의 텍스트 first part
                ce = tr.select_one(f"#com{stable_id}") if stable_id else None
                if ce:
                    a_txt = ce.get_text(strip=True)
                    if a_txt and len(a_txt) < 30:
                        cauthor = a_txt

            # 시간: .minitext onmouseover='$(this).html("2026/5/29 13:08:56")'
            cdate = None
            time_el = tr.select_one(".comment_line .minitext")
            if time_el:
                mouseover = time_el.get("onmouseover", "")
                m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})", mouseover)
                if m:
                    try:
                        cdate = datetime(
                            int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)), int(m.group(6)),
                            tzinfo=KST,
                        ).astimezone(timezone.utc)
                    except Exception:
                        cdate = None

            idx += 1
            ckey = stable_id or f"i{idx}"
            cuid = hashlib.md5(f"{post_url}#c{ckey}".encode()).hexdigest()[:16]
            comment_vocs.append(RawVOC(
                external_id=cuid,
                content=ctext,
                source_url=post_url,
                author_name=cauthor,
                published_at=cdate,
                country_code="KR",
            ))

        body_uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
        body_voc = RawVOC(
            external_id=body_uid,
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post.published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        logger.info(
            f"  Instiz 상세 {post_url.split('/')[-1].split('?')[0]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_instiz_date(self, text: str):
        """'13:05' (오늘) 또는 '26/05/28' (과거) 또는 'YY.MM.DD' 파싱 → UTC."""
        text = text.strip()
        try:
            if re.match(r"\d{2}:\d{2}$", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}/\d{2}/\d{2}", text):
                return datetime.strptime(text[:8], "%y/%m/%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}\.\d{2}\.\d{2}", text):
                return datetime.strptime(text[:8], "%y.%m.%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None
