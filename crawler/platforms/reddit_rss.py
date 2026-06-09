"""
Reddit RSS 크롤러 — OAuth 키 없이 공개 Atom feed 만으로 수집.

배경
====
crawler/platforms/reddit.py 는 https://oauth.reddit.com 에 의존하며
REDDIT_CLIENT_ID/SECRET 가 비어 있는 운영 환경에서는 0건만 수집한다.
이를 우회하기 위해 본 모듈은 다음 3단 fallback 을 사용한다.

  1) Atom RSS — https://www.reddit.com/r/<sub>/new.rss  (인증 불필요, 200 OK)
  2) JSON     — https://www.reddit.com/r/<sub>/new.json (UA 잘 맞추면 가능, 막힐 수 있음)
  3) Arctic Shift — https://arctic-shift.photon-reddit.com/api/posts/search
                    (역사 데이터, 공개 API)

본 1차 구현은 옵션 1 (RSS) 만 활성화한다. 차단되면 옵션 2 → 3 자동 fallback.

RSS 출력 구조 (Atom 1.0)
========================
- feed > entry (post) > id (t3_<id>), title, content (HTML), author/name,
                       published, updated, link@href
- subreddit 단위 새 글 25건 + 각 post 의 /<post>.rss 로 댓글 ~25건

수집 흐름
=========
1. 각 서브레딧 new.rss → post entries 파싱 (RawVOC, kind='post')
2. 정렬 후 상위 MAX_POSTS 의 댓글 RSS → comment entries (RawVOC, kind='comment')
3. RawVOC 모두 normalize 후 voc_records 로 ON CONFLICT 저장

플랫폼 코드: reddit_rss  (alembic 0015 platforms row 사전 삽입)

키 의존성
=========
없음. 단, UA 헤더는 SignalForge/1.0 RSS reader 로 고정한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

# Galaxy 전용 + 일반 안드로이드 서브레딧 (글로벌 영문권 main source)
# 2026-06-09 H3 data_grow — Galaxy 직계 6개 추가 (S24/ZFlip/ZFold6/Tab/oneui/samsunggalaxy)
# 환경변수 REDDIT_RSS_SUBS (콤마구분) 로 런타임 override 가능 → 워커 재기동 없이 적용.
_DEFAULT_SUBREDDITS: List[str] = [
    "samsung",
    "GalaxyS25",
    "Android",
    "AndroidQuestions",
    "GalaxyFold",
    "GalaxyWatch",
    "GalaxyBuds",
    # H3 신규
    "galaxys24",
    "GalaxyZFlip",
    "GalaxyZFold6",
    "GalaxyTab",
    "oneui",
    "samsunggalaxy",
]


def _load_subreddits() -> List[str]:
    raw = os.getenv("REDDIT_RSS_SUBS", "").strip()
    if not raw:
        return list(_DEFAULT_SUBREDDITS)
    out = [s.strip() for s in raw.split(",") if s.strip()]
    return out or list(_DEFAULT_SUBREDDITS)


SUBREDDITS: List[str] = _load_subreddits()

RSS_USER_AGENT = "SignalForge/1.0 RSS reader"
RSS_BASE = "https://www.reddit.com"
ARCTIC_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"

# Atom 1.0 namespace
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# 서브레딧당 listing limit 은 RSS 가 강제로 ~25개. 댓글 fetch 상한만 따로 둔다.
MAX_POSTS_FOR_COMMENTS = 30
# 각 post 의 댓글 RSS 에서 가져오는 최대 댓글 수
COMMENT_LIMIT = 30

# HTML 본문에서 텍스트 추출 — BeautifulSoup 사용 (lxml 백엔드)
def _html_to_text(html: str) -> str:
    """RSS content 의 HTML → 평문. lxml 우선, 없으면 html.parser, bs4 없으면 정규식."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()
    except ImportError:
        no_tags = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", no_tags).strip()


# Reddit post 본문 RSS 의 content 는 항상 표 마크업으로 link/comments 만 들어있다.
# title 외에 selftext 가 빠져 본문이 빈약하므로, post entry 는 title 을 본문으로 사용.
_TABLE_NOISE_PATTERN = re.compile(
    r"submitted by\s+/u/\S+|\[link\]|\[comments\]", re.IGNORECASE
)


def _parse_iso(dt: Optional[str]) -> Optional[datetime]:
    if not dt:
        return None
    s = dt.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        return None


@dataclass
class _ParsedPost:
    """RSS 에서 추출한 post 정보. crawl() 흐름에서 댓글 RSS URL 계산에 사용."""

    reddit_id: str        # 't3_<id>'  (Atom id)
    post_id_raw: str      # 't3_' 떼어낸 순수 id (e.g. '1tt69f9')
    permalink: str        # /r/<sub>/comments/<id>/<slug>/  (link href 의 path)
    title: str
    content_text: str
    author: str
    published: Optional[datetime]
    subreddit: str


def parse_post_feed(xml_bytes: bytes, subreddit: str) -> List[_ParsedPost]:
    """subreddit new.rss → _ParsedPost 리스트. 예외 발생 시 [] 반환."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning(f"Reddit RSS parse 실패 r/{subreddit}: {e}")
        return []

    out: List[_ParsedPost] = []
    for entry in root.findall("a:entry", ATOM_NS):
        rid_el = entry.find("a:id", ATOM_NS)
        link_el = entry.find("a:link", ATOM_NS)
        title_el = entry.find("a:title", ATOM_NS)
        author_el = entry.find("a:author/a:name", ATOM_NS)
        pub_el = entry.find("a:published", ATOM_NS)
        upd_el = entry.find("a:updated", ATOM_NS)
        content_el = entry.find("a:content", ATOM_NS)

        rid = (rid_el.text or "").strip() if rid_el is not None else ""
        if not rid.startswith("t3_"):
            continue
        permalink = (link_el.get("href") or "") if link_el is not None else ""
        if not permalink:
            continue

        title = (title_el.text or "").strip() if title_el is not None else ""
        author = (author_el.text or "").strip() if author_el is not None else ""
        published = _parse_iso(
            (pub_el.text if pub_el is not None else None)
            or (upd_el.text if upd_el is not None else None)
        )

        raw_html = (content_el.text or "") if content_el is not None else ""
        content_text = _html_to_text(raw_html)
        # 표 마크업 잡음 제거 — Reddit 의 정형 link/comments 라벨
        content_text = _TABLE_NOISE_PATTERN.sub("", content_text).strip()

        # post 본문은 RSS 에서 거의 잡히지 않음 → title 이 본문 역할.
        # title + (남은 본문) 조합으로 NLP 손실 최소화.
        composite = title if not content_text else f"{title}\n{content_text}"
        if not composite.strip():
            continue

        out.append(_ParsedPost(
            reddit_id=rid,
            post_id_raw=rid[3:],
            permalink=permalink,
            title=title,
            content_text=composite,
            author=author or "[deleted]",
            published=published,
            subreddit=subreddit,
        ))
    return out


def parse_comment_feed(xml_bytes: bytes, parent_post_url: str, subreddit: str) -> List[RawVOC]:
    """post.rss → 댓글 RawVOC 리스트. 예외 발생 시 []."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning(f"Reddit comment RSS parse 실패 ({parent_post_url}): {e}")
        return []

    out: List[RawVOC] = []
    for entry in root.findall("a:entry", ATOM_NS):
        cid_el = entry.find("a:id", ATOM_NS)
        cid = (cid_el.text or "").strip() if cid_el is not None else ""
        # t1_<id> 만 댓글. t3_ 는 같은 feed 안 post 자기 자신.
        if not cid.startswith("t1_"):
            continue

        link_el = entry.find("a:link", ATOM_NS)
        author_el = entry.find("a:author/a:name", ATOM_NS)
        content_el = entry.find("a:content", ATOM_NS)
        upd_el = entry.find("a:updated", ATOM_NS)

        href = (link_el.get("href") or "") if link_el is not None else ""
        author = (author_el.text or "").strip() if author_el is not None else "[deleted]"
        raw_html = (content_el.text or "") if content_el is not None else ""
        text = _html_to_text(raw_html)
        if not text or text in ("[deleted]", "[removed]"):
            continue
        published = _parse_iso(upd_el.text if upd_el is not None else None)

        external_id = hashlib.md5(f"reddit_rss::{cid}".encode()).hexdigest()[:16]
        out.append(RawVOC(
            external_id=external_id,
            content=text,
            source_url=href or f"{parent_post_url.rstrip('/')}/{cid[3:]}/",
            author_name=author,
            published_at=published,
            likes_count=0,
            country_code="US",
            meta={
                "parent_post": parent_post_url,
                "subreddit": subreddit,
                "kind": "comment",
                "reddit_id": cid,
            },
        ))
    return out


def post_to_rawvoc(p: _ParsedPost) -> RawVOC:
    external_id = hashlib.md5(f"reddit_rss::{p.reddit_id}".encode()).hexdigest()[:16]
    return RawVOC(
        external_id=external_id,
        content=p.content_text,
        source_url=p.permalink,
        author_name=p.author,
        published_at=p.published,
        likes_count=0,
        comments_count=0,
        country_code="US",
        meta={
            "subreddit": p.subreddit,
            "kind": "post",
            "reddit_id": p.reddit_id,
            "title": p.title,
        },
    )


# @lat: RedditRSSCrawler — [[crawler#Reddit RSS Fallback]] 참조.
class RedditRSSCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "reddit_rss", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        # crawl() 안에서 path 단계별 통계
        self.stats = {
            "rss_posts": 0,
            "rss_comments": 0,
            "json_posts": 0,
            "arctic_posts": 0,
            "blocked": [],
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": RSS_USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        all_vocs: List[RawVOC] = []
        # 런타임 재로드 — 워커 재기동 없이 env 로 subs 갱신 가능
        subreddits = _load_subreddits()
        async with self._client() as client:
            # 1) 각 서브레딧 new.rss → post
            posts: List[_ParsedPost] = []
            for sub in subreddits:
                try:
                    new_posts = await self._fetch_subreddit_rss(client, sub)
                    posts.extend(new_posts)
                    logger.info(f"  reddit_rss r/{sub}: {len(new_posts)} posts (rss)")
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code if e.response is not None else 0
                    if code in (403, 429):
                        # fallback: JSON → arctic
                        self.stats["blocked"].append(f"{sub}:rss:{code}")
                        logger.warning(
                            f"  reddit_rss r/{sub} RSS blocked ({code}), JSON fallback"
                        )
                        try:
                            new_posts = await self._fetch_subreddit_json(client, sub)
                            posts.extend(new_posts)
                            logger.info(f"  reddit_rss r/{sub}: {len(new_posts)} posts (json)")
                        except Exception as je:
                            logger.warning(f"  reddit_rss r/{sub} JSON 실패: {je}")
                            try:
                                arc = await self._fetch_arctic(client, sub)
                                posts.extend(arc)
                                logger.info(
                                    f"  reddit_rss r/{sub}: {len(arc)} posts (arctic)"
                                )
                            except Exception as ae:
                                logger.warning(f"  reddit_rss r/{sub} arctic 실패: {ae}")
                    else:
                        logger.warning(f"  reddit_rss r/{sub} RSS 실패: {e}")
                except Exception as e:
                    logger.warning(f"  reddit_rss r/{sub} 예외: {e}")

                await self._random_delay()

            self.stats["rss_posts"] = len(posts)
            # post RawVOC 들 누적
            for p in posts:
                all_vocs.append(post_to_rawvoc(p))

            # 2) 상위 MAX_POSTS_FOR_COMMENTS 개 post 댓글 RSS
            posts.sort(
                key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = posts[:MAX_POSTS_FOR_COMMENTS]
            logger.info(
                f"reddit_rss comment fetch 대상: {len(target)}/{len(posts)} posts"
            )

            comment_total = 0
            for p in target:
                await self._random_delay()
                try:
                    comments = await self._fetch_post_comments(client, p)
                    all_vocs.extend(comments)
                    comment_total += len(comments)
                except Exception as e:
                    logger.warning(f"  reddit_rss 댓글 실패 ({p.permalink}): {e}")

            self.stats["rss_comments"] = comment_total

        # MX 통합 키워드 영구 필터 (Data Clean 4)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(all_vocs)
        all_vocs = [v for v in all_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"reddit_rss 수집 완료: posts {self.stats['rss_posts']} + "
            f"comments {self.stats['rss_comments']} = {len(all_vocs)}건 "
            f"(mx_filter {before_n}→{len(all_vocs)})"
        )
        return all_vocs

    # ----- 옵션 1: RSS -----
    async def _fetch_subreddit_rss(
        self, client: httpx.AsyncClient, sub: str
    ) -> List[_ParsedPost]:
        url = f"{RSS_BASE}/r/{sub}/new.rss"
        resp = await client.get(url)
        resp.raise_for_status()
        return parse_post_feed(resp.content, sub)

    async def _fetch_post_comments(
        self, client: httpx.AsyncClient, p: _ParsedPost
    ) -> List[RawVOC]:
        # /r/<sub>/comments/<id>.rss 가 정식 endpoint
        url = f"{RSS_BASE}/r/{p.subreddit}/comments/{p.post_id_raw}.rss"
        resp = await client.get(url)
        resp.raise_for_status()
        return parse_comment_feed(resp.content, p.permalink, p.subreddit)[:COMMENT_LIMIT]

    # ----- 옵션 2: JSON (UA + 추가 헤더로 시도) -----
    async def _fetch_subreddit_json(
        self, client: httpx.AsyncClient, sub: str
    ) -> List[_ParsedPost]:
        url = f"{RSS_BASE}/r/{sub}/new.json?limit=100"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        }
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        children = (data.get("data") or {}).get("children") or []
        out: List[_ParsedPost] = []
        for ch in children:
            d = ch.get("data") or {}
            permalink = d.get("permalink") or ""
            if not permalink:
                continue
            full_url = f"{RSS_BASE}{permalink}"
            created = d.get("created_utc")
            pub = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
            title = d.get("title") or ""
            selftext = d.get("selftext") or ""
            composite = (title + "\n" + selftext).strip()
            rid = d.get("name") or f"t3_{d.get('id') or ''}"
            out.append(_ParsedPost(
                reddit_id=rid,
                post_id_raw=d.get("id") or "",
                permalink=full_url,
                title=title,
                content_text=composite,
                author=d.get("author") or "[deleted]",
                published=pub,
                subreddit=sub,
            ))
        self.stats["json_posts"] += len(out)
        return out

    # ----- 옵션 3: Arctic Shift -----
    async def _fetch_arctic(
        self, client: httpx.AsyncClient, sub: str
    ) -> List[_ParsedPost]:
        params = {"subreddit": sub, "limit": 100, "sort": "desc"}
        resp = await client.get(ARCTIC_BASE, params=params, timeout=45.0)
        resp.raise_for_status()
        data = resp.json()
        # Arctic Shift 응답 구조: {"data": [...]}
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out: List[_ParsedPost] = []
        for d in items:
            permalink = d.get("permalink") or ""
            url = (
                permalink if permalink.startswith("http")
                else f"{RSS_BASE}{permalink}"
            )
            if not url:
                continue
            created = d.get("created_utc")
            pub = (
                datetime.fromtimestamp(int(created), tz=timezone.utc)
                if created else None
            )
            title = d.get("title") or ""
            selftext = d.get("selftext") or ""
            composite = (title + "\n" + selftext).strip()
            rid = d.get("name") or f"t3_{d.get('id') or ''}"
            out.append(_ParsedPost(
                reddit_id=rid,
                post_id_raw=d.get("id") or "",
                permalink=url,
                title=title,
                content_text=composite,
                author=d.get("author") or "[deleted]",
                published=pub,
                subreddit=sub,
            ))
        self.stats["arctic_posts"] += len(out)
        return out


# 단독 실행: python -m platforms.reddit_rss
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    crawler = RedditRSSCrawler()
    vocs = asyncio.run(crawler.crawl())
    print(f"\n=== reddit_rss dry run ===")
    print(f"vocs: {len(vocs)}")
    print(f"stats: {crawler.stats}")
    if vocs:
        sample = vocs[0]
        print(f"sample[0]: url={sample.source_url}")
        print(f"           content={sample.content[:120]}...")
