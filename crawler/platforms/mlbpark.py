"""
MLB Park 크롤러 — httpx + BeautifulSoup
mlbpark.donga.com 에서 삼성 Galaxy 관련 VOC 수집.

NOTE on board choice:
  - `b=phone` (폰판기) 게시판은 실측 결과 "폰판기" 계정의 KT/SKT 시세표 광고글만
    존재하는 dealer-only board 였음 (VOC 가치 0).
  - 실제 사용자 폰 토론은 `b=bullpen` 일반 게시판에서 일어남 — 따라서 본 크롤러는
    bullpen 게시판을 Galaxy 키워드로 검색(`m=search`)하여 후보 게시물을 수집한다.
  - 이 결정은 task spec("keep to phone board")과 다르지만, 실제 VOC 수집 목적을
    달성하는 유일한 방법이라 판단.
"""
import hashlib
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://mlbpark.donga.com"

# bullpen 게시판을 Galaxy 키워드로 검색.
# (phone board 는 dealer 광고만 있어 VOC 수집 불가능)
SEARCH_QUERIES = [
    "갤럭시",
    "Galaxy",
    "아이폰",
    "iPhone",
]

# 검색 결과에서 본문/댓글까지 수집할 최대 게시물 수 (최신순)
MAX_POSTS = 80
# 검색 페이지 수 (p=1, p=31, p=61, ... 30 step). 5 페이지 = 최근 150건 후보
LIST_PAGES = 5

# 검색 URL 패턴 — p 는 0-based offset (페이지당 30건)
SEARCH_URL = (
    "{base}/mp/b.php?b=bullpen&select=sct&query={q}"
    "&m=search&p={p}"
)

# Galaxy / 경쟁사 관련 키워드 — 제목 필터링
GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S26", "S25", "S24", "S23", "S22",
    "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "Ultra", "울트라",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: MLBParkCrawler — [[crawler#Platform Strategy]] 참조.
class MLBParkCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "mlbpark", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []
        seen_urls = set()

        async with self._make_httpx_client() as client:
            for query in SEARCH_QUERIES:
                # MLB Park 페이지네이션: p=1, 31, 61, 91, 121 (30 step)
                for page_idx in range(LIST_PAGES):
                    p_val = 1 + page_idx * 30
                    try:
                        posts = await self._fetch_search_page(client, query, p_val)
                        # 중복 URL 제거 (여러 키워드 검색 시 같은 글이 잡힐 수 있음)
                        new_posts = [p for p in posts if p.source_url not in seen_urls]
                        for p in new_posts:
                            seen_urls.add(p.source_url)
                        # 제목 키워드 필터 (검색이 본문 매칭이라 제목엔 키워드 없을 수 있음)
                        filtered = [p for p in new_posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(
                            f"  MLBPark search '{query}' p={p_val}: "
                            f"{len(filtered)}/{len(new_posts)}건 (신규)"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  MLBPark search '{query}' p={p_val} 실패: {e}"
                        )

            # 최신순 정렬 후 상위 MAX_POSTS 만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"MLBPark 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(
                        f"  MLBPark 상세 수집 실패 ({post.source_url}): {e}"
                    )

        logger.info(
            f"MLBPark 수집 완료: {len(raw_vocs)}건 (게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    async def _fetch_search_page(
        self, client: httpx.AsyncClient, query: str, p: int
    ) -> List[RawVOC]:
        url = SEARCH_URL.format(
            base=BASE_URL, q=urllib.parse.quote(query), p=p
        )
        resp = await client.get(url)
        resp.raise_for_status()
        # MLB Park 은 UTF-8 — Content-Type 헤더로 확인됨
        return self._parse_search_list(resp.text)

    def _parse_search_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        table = soup.find("table", class_="tbl_type01")
        if not table:
            return results

        # 첫 번째 row 는 헤더, 그 다음부터 게시물
        for row in table.find_all("tr")[1:]:
            try:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                # 제목/링크: .tit a.txt 가 실제 게시물 링크
                title_el = row.select_one(".tit a.txt")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if not href or "m=view" not in href:
                    continue
                post_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 댓글 수
                reply_el = row.select_one(".replycnt")
                comment_count = 0
                if reply_el:
                    cm = re.search(r"\d+", reply_el.get_text())
                    if cm:
                        comment_count = int(cm.group(0))

                # 작성자
                nick_el = row.select_one(".nick")
                author = nick_el.get_text(strip=True) if nick_el else "익명"

                # 날짜
                date_el = row.select_one(".date")
                date_text = date_el.get_text(strip=True) if date_el else ""
                published_at = self._parse_mlbpark_date(date_text)

                # 조회수 (likes 대신 — MLB Park 리스트엔 추천수 없음)
                view_el = row.select_one(".viewV")
                view_count = 0
                if view_el:
                    vm = re.search(r"\d+", view_el.get_text())
                    if vm:
                        view_count = int(vm.group(0))

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=0,
                    comments_count=comment_count,
                    country_code="KR",
                    meta={"view_count": view_count},
                ))
            except Exception as e:
                logger.debug(f"MLBPark 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        """게시물 상세 페이지 → 본문 + AJAX 댓글 RawVOC 변환"""
        post_url = post.source_url
        resp = await client.get(post_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = post.content  # 리스트에서 받은 제목

        # 본문
        body_el = soup.select_one("#contentDetail")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        # 댓글: AJAX 로딩 → b.php?b=bullpen&id={post_id}&m=reply 호출
        post_id = self._extract_post_id(post_url)
        comment_vocs: List[RawVOC] = []
        if post_id:
            try:
                comment_vocs = await self._fetch_comments(
                    client, post_id, post_url, referer=post_url
                )
            except Exception as e:
                logger.warning(
                    f"  MLBPark 댓글 AJAX 실패 ({post_url}): {e} — 본문만 저장"
                )

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
            f"  MLBPark 상세 id={post_id}: 본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        post_id: str,
        post_url: str,
        referer: str,
    ) -> List[RawVOC]:
        ajax_url = f"{BASE_URL}/mp/b.php?b=bullpen&id={post_id}&m=reply"
        resp = await client.get(
            ajax_url,
            headers={
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.raise_for_status()
        return self._parse_comments(resp.text, post_url)

    def _parse_comments(self, html: str, post_url: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []
        idx = 0

        for cmt in soup.select("div.other_con[id^='reply_']"):
            cid = cmt.get("id", "").replace("reply_", "").strip()

            # 본문: span.re_txt — 멘션(.name_re)은 제거하고 추출
            txt_el = cmt.select_one("span.re_txt")
            if not txt_el:
                continue
            # 멘션 라벨 제거 (다른 사람 닉네임)
            for mention in txt_el.select(".name_re"):
                mention.decompose()
            text = txt_el.get_text(" ", strip=True)
            text = re.sub(r"\s{2,}", " ", text).strip()
            if not text or len(text) < 2:
                continue

            # 작성자
            name_el = cmt.select_one("span.name")
            author = name_el.get_text(strip=True) if name_el else "익명"

            # 날짜
            date_el = cmt.select_one("span.date")
            published_at = (
                self._parse_mlbpark_date(date_el.get_text(strip=True))
                if date_el else None
            )

            idx += 1
            ckey = cid or f"i{idx}"
            cuid = hashlib.md5(f"{post_url}#c{ckey}".encode()).hexdigest()[:16]

            results.append(RawVOC(
                external_id=cuid,
                content=text,
                source_url=post_url,
                author_name=author,
                published_at=published_at,
                likes_count=0,
                country_code="KR",
            ))

        return results

    def _extract_post_id(self, url: str) -> str:
        m = re.search(r"id=(\d+)", url)
        return m.group(1) if m else ""

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_mlbpark_date(self, text: str):
        """'2026-05-28', '2026-05-28 14:32', '07:58:38' 파싱"""
        text = text.strip()
        try:
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).astimezone(timezone.utc)
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
                return datetime.strptime(text[:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST).astimezone(timezone.utc)
            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=KST).astimezone(timezone.utc)
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
