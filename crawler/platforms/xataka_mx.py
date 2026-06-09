"""
Xataka México 크롤러 — httpx + HTML(태그 페이지) + 인라인 JSON 댓글

xataka.com.mx (멕시코 테크 매체, Xataka 그룹) 의 Samsung/Galaxy 관련
기사 본문 + 댓글 수집.

전략
  - /tag/<topic> 태그 페이지(서버사이드 HTML) 에서 기사 링크 수집:
      samsung, galaxy, samsung-galaxy, one-ui
  - /feedburner.xml RSS 보강 — 키워드 매칭한 신규 글만 추가.
  - 기사 상세 페이지에서 본문(div.article-content) 추출.
    댓글은 인라인 스크립트의 `AML.Comments.config.data = {...}` JSON 에서 파싱
    (xataka.com 과 완전 동일한 패턴).
  - 시간: meta[article:published_time] (ISO8601 Z = UTC),
    RSS pubDate (RFC822). naive datetime 은 멕시코 CST(UTC-6) 기준 보정.
    (멕시코 본토 대부분은 -6, 2022년 이후 DST 폐지)
  - Galaxy/Samsung 키워드 필터 — 본문/제목에 매칭 필요.

xataka.com 과 다른 점:
  - 도메인이 xataka.com.mx (article URL 정규식, RSS 도메인 변경)
  - 댓글이 적은 편 (멕시코 사이트는 트래픽이 본가보다 작음) → 본문 위주
  - country_code="MX", 시간대 CST(-6)
  - article id 추출 시 `AML.Comments.config.postId = N` 도 지원
"""
import hashlib
import html as html_lib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.xataka.com.mx"
RSS_URL = f"{BASE_URL}/feedburner.xml"

# Samsung/Galaxy 관련 태그 페이지
XATAKA_MX_TAGS = [
    ("samsung",         "Samsung"),
    ("galaxy",          "Galaxy"),
    ("samsung-galaxy",  "Samsung Galaxy"),
    ("one-ui",          "One UI"),
]

# 본문 보강(상세 fetch) 캡
DETAIL_LIMIT = 60
# 최종 처리 캡
MAX_POSTS = 150

# 멕시코 표준시 (CST = UTC-6). 멕시코 본토 대부분은 2022 년 이후 DST 폐지.
# 일부 북부 주가 PST(UTC-7) 이지만 사이트 콘텐츠 기준은 CST 단일.
CST_MX = timezone(timedelta(hours=-6))

# Galaxy / Samsung 표기는 스페인어권에서도 영문 동일
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]

# 기사 URL 패턴: https://www.xataka.com.mx/<categoria>/<slug>
ARTICLE_PATTERN = re.compile(
    r'href="(https://www\.xataka\.com\.mx/[a-z0-9-]+/[a-z0-9-]+)(?:#[^"]*)?"'
)


class XatakaMXCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "xataka_mx", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidate_urls: dict = {}  # url -> tag_name

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "es-MX,es;q=0.9,en;q=0.7"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 태그 페이지에서 기사 링크 수집
            for tag, tag_name in XATAKA_MX_TAGS:
                try:
                    urls = await self._fetch_tag_listing(client, tag)
                    new_count = 0
                    for u in urls:
                        if u not in candidate_urls:
                            candidate_urls[u] = tag_name
                            new_count += 1
                    logger.info(
                        f"  XatakaMX tag [{tag_name}]: {new_count} 신규 (전체 {len(urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  XatakaMX tag [{tag_name}] 실패: {e}")

            # 2) RSS 보강 — 키워드 매칭만 추가
            try:
                rss_items = await self._fetch_rss(client)
                rss_added = 0
                for it in rss_items:
                    if self._is_galaxy_related_text(it["title"] + " " + it.get("desc", "")):
                        if it["url"] not in candidate_urls:
                            candidate_urls[it["url"]] = "RSS"
                            rss_added += 1
                logger.info(f"  XatakaMX RSS 보강: {rss_added} 신규 (전체 {len(rss_items)})")
            except Exception as e:
                logger.warning(f"  XatakaMX RSS 실패: {e}")

            # 3) 상위 N건 상세 fetch
            urls_sorted = list(candidate_urls.keys())[:DETAIL_LIMIT]
            logger.info(
                f"XatakaMX 후보 {len(candidate_urls)}건 중 상세 수집 {len(urls_sorted)}건"
            )

            raw_vocs: List[RawVOC] = []
            for art_url in urls_sorted:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_article_detail(client, art_url)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  XatakaMX 상세 실패 ({art_url}): {e}")

        # external_id 단위 중복 제거
        seen: set = set()
        unique: List[RawVOC] = []
        for v in raw_vocs:
            if v.external_id in seen:
                continue
            seen.add(v.external_id)
            unique.append(v)

        result = unique[:MAX_POSTS]
        logger.info(
            f"XatakaMX 수집 완료: {len(result)}건 "
            f"(후보 {len(candidate_urls)} → 본문/댓글 {len(unique)})"
        )
        return result

    # --- listing ---

    async def _fetch_tag_listing(
        self, client: httpx.AsyncClient, tag: str
    ) -> List[str]:
        url = f"{BASE_URL}/tag/{tag}"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        seen: set = set()
        out: List[str] = []
        for m in ARTICLE_PATTERN.finditer(resp.text):
            u = m.group(1)
            # 카테고리/태그/저자/특수 페이지 제외
            if re.search(r"/(?:tag|autor|categoria|archivos|edicion|seleccion)/", u):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[dict]:
        resp = await client.get(
            RSS_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []
        items: List[dict] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = item.findtext("description") or ""
            desc = re.sub(r"<[^>]+>", " ", html_lib.unescape(desc_raw))
            desc = re.sub(r"\s+", " ", desc).strip()
            if not title or not link:
                continue
            items.append({"title": title, "url": link, "desc": desc})
        return items

    # --- detail ---

    async def _fetch_article_detail(
        self, client: httpx.AsyncClient, url: str
    ) -> List[RawVOC]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return []
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 제목
        title_el = soup.find("meta", attrs={"property": "og:title"})
        title = title_el.get("content", "").strip() if title_el else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # 본문 — div.article-content 내부 p / li 추출
        body_parts: List[str] = []
        body_root = soup.select_one("div.article-content")
        if body_root is not None:
            for el in body_root.find_all(["p", "li"]):
                txt = el.get_text(" ", strip=True)
                if txt:
                    body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback: og:description
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = desc_el.get("content", "").strip() if desc_el else ""

        if not title and not body:
            return []

        # Galaxy/Samsung 관련 글만
        if not self._is_galaxy_related_text(title + " " + body):
            return []

        # 발행일 — ISO8601 Z = UTC
        pub_el = soup.find("meta", attrs={"property": "article:published_time"})
        published_at = self._parse_iso_date(
            pub_el.get("content", "") if pub_el else ""
        )

        # 저자
        author = None
        author_a = soup.find("a", href=re.compile(r"^/autor/[a-z0-9-]+/?$"))
        if author_a:
            atext = author_a.get_text(" ", strip=True)
            m = re.search(r"de\s+(.+)$", atext)
            if m:
                author = m.group(1).strip()
            elif atext:
                author = atext

        # 본문 길이 컷 (4000자)
        if len(body) > 4000:
            body = body[:4000]

        full_content = f"{title}\n{body}".strip() if body else title

        # article id — JSON 의 "post_id":N 또는 `AML.Comments.config.postId = N`,
        # 둘 다 없으면 URL md5
        post_id_match = re.search(r'"post_id"\s*:\s*(\d+)', html) \
            or re.search(r'AML\.Comments\.config\.postId\s*=\s*(\d+)', html)
        article_id = post_id_match.group(1) if post_id_match else \
            hashlib.md5(url.encode()).hexdigest()[:12]

        body_voc = RawVOC(
            external_id=hashlib.md5(f"{url}#{article_id}".encode()).hexdigest()[:16],
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="MX",
            meta={"article_id": article_id, "source": "html"},
        )

        # 댓글 파싱
        comment_vocs = self._parse_comments(html, url)
        body_voc.comments_count = len(comment_vocs)

        logger.info(
            f"  XatakaMX 상세 {url.split('/')[-1][:40]}: "
            f"본문 {len(body)}자 + 댓글 {len(comment_vocs)}건"
        )

        return [body_voc] + comment_vocs

    def _parse_comments(self, html: str, post_url: str) -> List[RawVOC]:
        """AML.Comments.config.data = {...}; 인라인 JSON 에서 댓글 추출
        (xataka.com 과 동일 패턴, 댓글 없는 글이 많아 빈 결과가 정상)
        """
        idx = html.find("AML.Comments.config.data")
        if idx < 0:
            return []
        eq = html.find("=", idx)
        if eq < 0:
            return []
        start = html.find("{", eq)
        if start < 0:
            return []
        # 괄호 균형으로 JSON 종료 위치 찾기
        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, min(len(html), start + 5_000_000)):
            ch = html[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end < 0:
            return []
        try:
            data = json.loads(html[start:end])
        except json.JSONDecodeError:
            return []

        comments = data.get("comments") or []
        out: List[RawVOC] = []
        for c in comments:
            try:
                if str(c.get("comment_approved", "1")) != "1":
                    continue
                if c.get("comment_deleted_date"):
                    continue
                content_raw = c.get("content") or c.get("content_filtered") or ""
                text = re.sub(r"<[^>]+>", " ", html_lib.unescape(content_raw))
                text = re.sub(r"\s+", " ", text).strip()
                if not text or len(text) < 3:
                    continue

                cid = c.get("id")
                if cid is None:
                    continue

                ts = c.get("date")
                cdate: Optional[datetime] = None
                if isinstance(ts, (int, float)) and ts > 0:
                    cdate = datetime.fromtimestamp(int(ts), tz=timezone.utc)

                author = (
                    c.get("user_name")
                    or c.get("author")
                    or c.get("comment_author")
                    or "anonimo"
                )

                karma = c.get("karma") or 0
                try:
                    likes = int(c.get("vote_count") or 0)
                except (TypeError, ValueError):
                    likes = 0

                out.append(RawVOC(
                    external_id=hashlib.md5(
                        f"{post_url}#c{cid}".encode()
                    ).hexdigest()[:16],
                    content=text,
                    source_url=post_url,
                    author_name=str(author),
                    published_at=cdate,
                    likes_count=likes,
                    country_code="MX",
                    meta={"comment_id": cid, "karma": karma, "source": "comment"},
                ))
            except Exception as e:
                logger.debug(f"XatakaMX 댓글 파싱 실패: {e}")
        return out

    # --- helpers ---

    def _is_galaxy_related_text(self, text: str) -> bool:
        t = (text or "").lower()
        if not t.strip():
            return False
        return any(kw in t for kw in GALAXY_KEYWORDS)

    def _parse_iso_date(self, text: str) -> Optional[datetime]:
        """ISO8601 'YYYY-MM-DDTHH:MM:SSZ' → UTC.
        naive datetime 이면 멕시코 CST(-6) 로 간주."""
        if not text:
            return None
        try:
            t = text.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST_MX)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 → UTC. naive 면 CST(-6)."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST_MX)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
