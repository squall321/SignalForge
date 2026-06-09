"""
IT之家 (IT Home / ithome.com) 크롤러 — httpx + RSS + Comment JSON API

ithome.com 은 중국 최대 IT 뉴스 포털 중 하나. 본문은 정상이지만 검색/태그
페이지 (/zt/samsung/) 는 404 또는 차단. 다행히 메인 RSS `/rss/` 는 모든
최신 뉴스의 **전문 (content)** 을 description 에 그대로 담아 노출한다.

전략
  - 1차: 메인 RSS `/rss/` (≈ 60건) — description 에 full HTML body 포함.
    Samsung/三星 키워드로 필터링 후 본문 그대로 사용.
  - 2차: 필터된 기사만 article HTML 한 번 더 GET → 댓글 hash `data-id`
    및 작성자 추출 → 댓글 JSON API (`cmt.ithome.com/api/webcomment/
    getnewscomment?sn=<hash>&isInit=true&appver=900`) 호출.
  - 댓글 응답은 `topComments + hotComments + comments[].elements[type=0]`
    의 텍스트만 본문 끝에 [댓글] 으로 이어붙임 (clien 패턴과 동일).
  - external_id = md5(article_url + "#" + news_id) — RSS GUID 부재시도
    URL 의 `/0/AAA/BBB.htm` 에서 안정적으로 news_id 추출 가능.
  - 시간: RSS pubDate (RFC822 GMT — 이미 UTC 라 timezone 만 부여).
    CST (+8) 가 아니라 GMT 로 발행됨 — 직접 확인.
  - RSS 가 막히면 (403/404) Firefox UA 로 retry → 그래도 실패시 빈 결과.
"""
import asyncio
import hashlib
import html as html_lib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ithome.com"
RSS_URL  = f"{BASE_URL}/rss/"
CMT_URL  = "https://cmt.ithome.com/api/webcomment/getnewscomment"

# RSS 가 단일 페이지 (~60건) 만 노출하므로 LIST_PAGES 는 1 이 효과적이나
# 표준 인터페이스에 맞춰 변수로 유지. 추후 채널별 RSS 추가시 사용.
LIST_PAGES = 12
MAX_POSTS  = 150

FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)

GALAXY_KEYWORDS = [
    # 영문
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "one ui", "oneui", "exynos", "bixby",
    "buds", "watch", "tab",
    # 중국어 간체
    "三星", "盖乐世",
    "Galaxy S", "Galaxy Z", "Galaxy A",
]

# /0/958/447.htm → news_id 958447
NEWS_ID_RE = re.compile(r"/0/(\d+)/(\d+)\.htm")
DATA_ID_RE = re.compile(r'data-id="([a-f0-9]+)"')
AUTHOR_RE  = re.compile(r'author_baidu[^>]*>[^<]*<strong>([^<]+)</strong>')


class ITHomeCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "ithome", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_news_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) RSS 본문 일괄 수집
            rss_items = await self._fetch_rss(client)
            if not rss_items:
                logger.warning("ITHome RSS 비어있음 — Firefox UA 로 재시도")
                client.headers["User-Agent"] = FIREFOX_UA
                rss_items = await self._fetch_rss(client)

            filtered = [it for it in rss_items if self._is_galaxy_related(it)]
            logger.info(
                f"  ITHome RSS: 전체 {len(rss_items)} / Galaxy 매칭 {len(filtered)}"
            )

            # 2) 필터된 기사만 댓글/작성자 보강
            for voc in filtered:
                news_id = voc.meta.get("news_id")
                if not news_id or news_id in seen_news_ids:
                    continue
                seen_news_ids.add(news_id)

                try:
                    author, hash_id, comments_text, comments_count = (
                        await self._fetch_article_meta(client, voc.source_url)
                    )
                    if author:
                        voc.author_name = author
                    if comments_count is not None:
                        voc.comments_count = comments_count
                    if comments_text:
                        # 본문 + 댓글 결합 (4000자 제한 적용)
                        merged = f"{voc.content}\n\n[댓글]\n{comments_text}"
                        voc.content = merged[:4000]
                    if hash_id:
                        voc.meta["hash_id"] = hash_id
                except Exception as e:
                    logger.debug(
                        f"  ITHome 본문 보강 실패 ({voc.source_url}): {e}"
                    )

                items.append(voc)
                if len(items) >= MAX_POSTS:
                    break
                await self._random_delay()

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"ITHome 수집 완료: {len(result)}건")
        return result

    # --- RSS ---

    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[RawVOC]:
        try:
            resp = await client.get(
                RSS_URL,
                headers={
                    "Referer": BASE_URL + "/",
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                },
            )
        except httpx.RequestError as e:
            logger.warning(f"ITHome RSS 요청 실패: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"ITHome RSS HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"ITHome RSS 파싱 실패: {e}")
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        results: List[RawVOC] = []
        for item in channel.findall("item"):
            try:
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link")  or "").strip()
                if not title or not link:
                    continue

                news_id = self._extract_news_id(link)
                if not news_id:
                    continue

                # description 에 full HTML body
                desc_raw = item.findtext("description") or ""
                body = self._strip_html(desc_raw)
                if len(body) > 4000:
                    body = body[:4000]

                full_content = f"{title}\n{body}".strip() if body else title
                if len(full_content) < 20:
                    continue

                published_at = self._parse_rss_date(
                    item.findtext("pubDate") or ""
                )

                external_id = hashlib.md5(
                    f"{link}#{news_id}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=None,        # RSS 에 author 없음 — HTML 에서 보강
                    published_at=published_at,
                    comments_count=0,        # 추후 보강
                    country_code="CN",
                    meta={
                        "news_id": news_id,
                        "title": title,
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"ITHome item 파싱 실패: {e}")
        return results

    # --- Article HTML + Comments ---

    async def _fetch_article_meta(
        self, client: httpx.AsyncClient, url: str
    ) -> Tuple[Optional[str], Optional[str], str, Optional[int]]:
        """기사 HTML → (author, hash_id, comments_text, comments_count)."""
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return None, None, "", None
        html = resp.text

        author = None
        m = AUTHOR_RE.search(html)
        if m:
            author = html_lib.unescape(m.group(1)).strip() or None

        hash_id = None
        m2 = DATA_ID_RE.search(html)
        if m2:
            hash_id = m2.group(1)

        comments_text = ""
        comments_count = None
        if hash_id:
            comments_text, comments_count = await self._fetch_comments(
                client, hash_id, url
            )

        return author, hash_id, comments_text, comments_count

    async def _fetch_comments(
        self, client: httpx.AsyncClient, hash_id: str, referer: str
    ) -> Tuple[str, Optional[int]]:
        """댓글 JSON API → (joined_text, count)."""
        try:
            resp = await client.get(
                CMT_URL,
                params={"sn": hash_id, "isInit": "true", "appver": "900"},
                headers={"Referer": referer},
            )
        except httpx.RequestError:
            return "", None
        if resp.status_code != 200:
            return "", None

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return "", None
        if not data.get("success"):
            return "", None

        content = data.get("content") or {}
        all_cmts = []
        for key in ("topComments", "hotComments", "comments"):
            v = content.get(key) or []
            if isinstance(v, list):
                all_cmts.extend(v)

        if not all_cmts:
            return "", 0

        # 중복 제거 (id 기준)
        seen_ids: set = set()
        unique = []
        for c in all_cmts:
            cid = c.get("id")
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            unique.append(c)

        texts = []
        for c in unique:
            txt = self._extract_comment_text(c)
            if txt:
                texts.append(txt)
                # children (대댓글) 도 1단계 포함
                for ch in (c.get("children") or []):
                    ch_txt = self._extract_comment_text(ch)
                    if ch_txt:
                        texts.append(f"  └ {ch_txt}")

        joined = "\n".join(texts[:30])  # 너무 길지 않게 30 댓글 제한
        return joined, len(unique)

    @staticmethod
    def _extract_comment_text(cmt: dict) -> str:
        """댓글 elements[type=0].content 만 추출 (이미지/링크 제외)."""
        if not isinstance(cmt, dict):
            return ""
        elements = cmt.get("elements") or []
        parts = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            if el.get("type") == 0:  # Text
                c = (el.get("content") or "").strip()
                if c:
                    parts.append(c)
        return " ".join(parts)

    # --- helpers ---

    @staticmethod
    def _extract_news_id(url: str) -> Optional[str]:
        """https://www.ithome.com/0/958/447.htm → '958447'."""
        m = NEWS_ID_RE.search(url)
        if not m:
            return None
        return f"{m.group(1)}{m.group(2)}"

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "")
        if not text.strip():
            return False
        text_l = text.lower()
        for kw in GALAXY_KEYWORDS:
            if kw.lower() in text_l:
                return True
        return False

    @staticmethod
    def _parse_rss_date(text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 19:52:47 GMT' → UTC.
        ITHome RSS 는 GMT 로 발행하므로 그대로 UTC. naive 면 UTC 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def _main():
        c = ITHomeCrawler()
        vocs = await c.crawl()
        print(f"\n수집: {len(vocs)}건")
        for v in vocs[:5]:
            print(f"- [{v.external_id}] {v.author_name} @ {v.published_at} cmts={v.comments_count}")
            print(f"  {v.source_url}")
            print(f"  {v.content[:120]}…")
    asyncio.run(_main())
