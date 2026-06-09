"""
DogDrip(개드립) 크롤러 — httpx + BeautifulSoup
www.dogdrip.net 통합검색(Galaxy 키워드) 결과에서 본문 + 댓글 수집

전용 IT/모바일 게시판이 따로 없어서 'dogdrip'(메인 게시판) + 'free'(자유)에
search API(_filter=search&search_target=title_content)를 키워드별로 호출.
"""
import hashlib
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

BASE_URL = "https://www.dogdrip.net"
KST = timezone(timedelta(hours=9))

# 검색 대상 게시판(mid) — 검색은 mid 단위로 동작. dogdrip(메인) 위주.
DOGDRIP_BOARDS = [
    ("dogdrip", "개드립"),
    ("free",    "자유게시판"),
]

# 갤럭시 관련 키워드 — 게시판 검색용 (한 번에 하나씩 검색)
SEARCH_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S26", "폴드", "플립", "삼성",
]

def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(min_value, v)


# 검색 페이지 수 (per board × per keyword)
# BACKFILL_PAGES 환경변수로 옛 글 백카탈로그 수집 시 10~30 까지 확장
LIST_PAGES = _env_int("DOGDRIP_BACKFILL_PAGES", 2)
# 상세 수집 게시물 상한
MAX_POSTS = _env_int("DOGDRIP_MAX_POSTS", 150)

SEARCH_URL = (
    "{base}/index.php?_filter=search&mid={mid}"
    "&search_keyword={kw}&search_target=title_content&page={page}"
)


class DogdripCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "dogdrip", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []
        seen_urls: set = set()

        async with self._make_httpx_client() as client:
            for mid, board_name in DOGDRIP_BOARDS:
                for kw in SEARCH_KEYWORDS:
                    for page in range(1, LIST_PAGES + 1):
                        try:
                            posts = await self._fetch_search_page(client, mid, kw, page)
                            # URL 중복 제거 (키워드 교차)
                            fresh = [p for p in posts if p.source_url not in seen_urls]
                            for p in fresh:
                                seen_urls.add(p.source_url)
                            list_posts.extend(fresh)
                            logger.info(
                                f"  Dogdrip {board_name} '{kw}' p{page}: "
                                f"{len(fresh)}/{len(posts)}건"
                            )
                            await self._random_delay()
                        except Exception as e:
                            logger.warning(
                                f"  Dogdrip {board_name} '{kw}' p{page} 실패: {e}"
                            )

            # 최신순 정렬, 상위 MAX_POSTS만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Dogdrip 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(
                        f"  Dogdrip 상세 실패 ({post.source_url}): {e}"
                    )

        # 2026-06-08 C3: MX 통합 키워드 필터 강제
        from nlp.mx_keywords import is_mx_relevant
        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Dogdrip 수집 완료: {len(raw_vocs)}/{before}건 (MX 필터 적용)"
        )
        return raw_vocs

    async def _fetch_search_page(
        self,
        client: httpx.AsyncClient,
        mid: str,
        kw: str,
        page: int,
    ) -> List[RawVOC]:
        from urllib.parse import quote
        url = SEARCH_URL.format(base=BASE_URL, mid=mid, kw=quote(kw), page=page)
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        return self._parse_list(resp.text, mid)

    def _parse_list(self, html: str, mid: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for item in soup.select("li.ed.flex.webzine"):
            try:
                link_el = item.select_one("a.title-link")
                if not link_el:
                    continue
                srl = link_el.get("data-document-srl") or ""
                title = link_el.get_text(strip=True)
                if not srl or not title:
                    continue

                # 검색 쿼리스트링 제거한 정규 URL 사용 (중복 제거 + 안정성)
                post_url = f"{BASE_URL}/{mid}/{srl}"

                # list-meta 내부에서 추천수/날짜/작성자 추출
                metas = item.select(".list-meta span")
                # 패턴: [추천, 추천(중복), 'N 일 전' 등 상대시간, 작성자]
                likes = 0
                published_at: Optional[datetime] = None
                author: Optional[str] = None
                for m in metas:
                    txt = m.get_text(strip=True)
                    if not txt:
                        continue
                    if txt.isdigit() and likes == 0:
                        try:
                            likes = int(txt)
                        except ValueError:
                            pass
                        continue
                    parsed_dt = self._parse_relative_date(txt)
                    if parsed_dt and published_at is None:
                        published_at = parsed_dt
                        continue
                    # 작성자는 마지막 자리 — 숫자/날짜 아닌 짧은 문자열
                    if not txt.isdigit() and not parsed_dt and len(txt) < 30:
                        author = txt

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author or "익명",
                    published_at=published_at,
                    likes_count=likes,
                    comments_count=0,
                    country_code="KR",
                    meta={"mid": mid, "srl": srl},
                ))
            except Exception as e:
                logger.debug(f"Dogdrip 리스트 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        resp = await client.get(post.source_url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_url = post.source_url
        title = post.content

        # 본문
        body_el = soup.select_one(".xe_content")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 댓글
        comment_vocs: List[RawVOC] = []
        for c in soup.select(".comment-item"):
            try:
                cid = c.get("id", "") or ""  # 'comment_<srl>'
                # 안정적 ID
                if cid.startswith("comment_"):
                    stable = cid.replace("comment_", "")
                else:
                    stable = hashlib.md5(c.get_text(strip=True).encode()).hexdigest()[:8]

                content_el = c.select_one(".rhymix_content.xe_content") \
                    or c.select_one(".xe_content")
                if not content_el:
                    continue
                ctext = content_el.get_text("\n", strip=True)
                if not ctext or len(ctext) < 3:
                    continue

                # 작성자: .comment-bar-author 내부 첫 a.link-reset (img alt 제외하고 텍스트만)
                author_el = c.select_one(".comment-bar-author a.link-reset")
                if author_el:
                    # img 제거 후 텍스트만
                    for img in author_el.select("img"):
                        img.decompose()
                    cauthor = author_el.get_text(strip=True) or "익명"
                else:
                    cauthor = "익명"

                # 댓글 시간 (상대시간)
                cdate_el = c.select_one(".comment-bar-author .text-muted")
                cdate = self._parse_relative_date(
                    cdate_el.get_text(strip=True)
                ) if cdate_el else None

                # 추천 수
                like_el = c.select_one(".action .count")
                try:
                    clikes = int(re.sub(r"[^\d]", "", like_el.get_text(strip=True)) or 0) \
                        if like_el else 0
                except ValueError:
                    clikes = 0

                comment_vocs.append(RawVOC(
                    external_id=hashlib.md5(
                        f"{post_url}#c{stable}".encode()
                    ).hexdigest()[:16],
                    content=ctext,
                    source_url=post_url,
                    author_name=cauthor,
                    published_at=cdate,
                    likes_count=clikes,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"Dogdrip 댓글 파싱 실패: {e}")

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
            f"  Dogdrip 상세 {post_url.rsplit('/', 1)[-1]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _parse_relative_date(self, text: str) -> Optional[datetime]:
        """'1 일 전', '3 시간 전', '5 분 전', '방금 전', '2026.05.20' 처리"""
        if not text:
            return None
        text = text.strip()
        now = datetime.now(KST)
        try:
            if "방금" in text or "초 전" in text:
                return now.astimezone(timezone.utc)
            m = re.match(r"(\d+)\s*분\s*전", text)
            if m:
                return (now - timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)
            m = re.match(r"(\d+)\s*시간\s*전", text)
            if m:
                return (now - timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)
            m = re.match(r"(\d+)\s*일\s*전", text)
            if m:
                return (now - timedelta(days=int(m.group(1)))).astimezone(timezone.utc)
            m = re.match(r"(\d+)\s*달\s*전", text) or re.match(r"(\d+)\s*개월\s*전", text)
            if m:
                return (now - timedelta(days=30 * int(m.group(1)))).astimezone(timezone.utc)
            # 절대일자 'YYYY.MM.DD' or 'YYYY-MM-DD'
            m = re.match(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", text)
            if m:
                y, mo, d = map(int, m.groups())
                return datetime(y, mo, d, tzinfo=KST).astimezone(timezone.utc)
        except Exception:
            pass
        return None
