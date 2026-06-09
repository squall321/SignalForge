"""
The Verge 크롤러 — httpx + Atom XML (RSS) + HTML 보강

theverge.com 의 본문 기사(저널리즘 콘텐츠)에서 Samsung/Galaxy 관련 VOC 수집.

전략
  - 다수 Atom 피드(/rss/{tag}/index.xml) 를 병합:
      samsung, tech, mobile, phones
    각 피드 10건씩 → 약 40건 후보 (중복은 link 기준 제거).
  - 추가로 /samsung 태그 HTML 페이지의 article link 들을 긁어 미커버 기사 보강.
  - 본문은 Atom <content>(전문 요약/리드) 사용 — RSS description 만으로도 의미 충분.
  - 댓글은 Coral 플랫폼(JS bundle 로 비동기 로드) → httpx 직접 수집 불가.
    → 본문 기반 VOC 만 수집하고 LIST_PAGES/소스 다양화로 정보 밀도 확보.
  - Galaxy/Samsung 키워드 필터로 Apple/Google 위주 글 컷.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.theverge.com"
RSS_URL = "{base}/rss/{tag}/index.xml"

# Atom 네임스페이스 (The Verge 는 RSS 가 아닌 Atom 포맷)
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# 다중 피드 — Samsung 직접 태그 + 일반 기술 카테고리(키워드 필터로 좁힘)
VERGE_FEEDS = [
    ("samsung", "Samsung Tag"),
    ("tech",    "Tech"),
    ("mobile",  "Mobile"),
    ("phones",  "Phones"),
]

# HTML 보강용 태그 페이지 (RSS 가 10건으로 제한되므로 listing 페이지에서 더 긁기)
HTML_LIST_URLS = [
    f"{BASE_URL}/samsung",
]

# 최종 처리 캡
MAX_POSTS = 150

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class TheVergeCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "theverge", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            # 'br'(brotli) 는 httpx 기본 미지원 → gzip/deflate 로만 advertise
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) Atom RSS 피드 수집
            for tag, tag_name in VERGE_FEEDS:
                try:
                    posts = await self._fetch_feed(client, tag)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  TheVerge RSS [{tag_name}]: {len(filtered)}/{len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  TheVerge RSS [{tag_name}] 실패: {e}")

            # 2) HTML 보강 — Samsung 태그 페이지 article 링크에서 상세 fetch
            for list_url in HTML_LIST_URLS:
                try:
                    article_urls = await self._fetch_listing_links(client, list_url)
                    # RSS 에서 이미 가져온 URL 제외
                    known = {it.source_url for it in items}
                    new_urls = [u for u in article_urls if u not in known][:30]
                    logger.info(
                        f"  TheVerge HTML listing: {len(new_urls)} 신규 후보 (전체 {len(article_urls)})"
                    )
                    for art_url in new_urls:
                        try:
                            voc = await self._fetch_article(client, art_url)
                            if voc and self._is_galaxy_related(voc):
                                items.append(voc)
                            await self._random_delay()
                        except Exception as e:
                            logger.debug(f"    article {art_url} 실패: {e}")
                except Exception as e:
                    logger.warning(f"  TheVerge listing [{list_url}] 실패: {e}")

        # link 단위 중복 제거
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.source_url in seen:
                continue
            seen.add(it.source_url)
            unique.append(it)

        # 최신순 정렬 → 상위 MAX_POSTS
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = unique[:MAX_POSTS]
        logger.info(
            f"TheVerge 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    async def _fetch_feed(self, client: httpx.AsyncClient, tag: str) -> List[RawVOC]:
        url = RSS_URL.format(base=BASE_URL, tag=tag)
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        return self._parse_atom(resp.text)

    def _parse_atom(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"TheVerge Atom 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        # Atom 의 entry 는 default namespace 사용
        for entry in root.findall("atom:entry", ATOM_NS):
            try:
                title = self._text(entry.find("atom:title", ATOM_NS)).strip()
                # <link rel="alternate" type="text/html" href="..."/>
                link_el = entry.find("atom:link[@rel='alternate']", ATOM_NS)
                if link_el is None:
                    link_el = entry.find("atom:link", ATOM_NS)
                link = (link_el.get("href") if link_el is not None else "").strip()
                if not title or not link:
                    continue

                entry_id = self._text(entry.find("atom:id", ATOM_NS)).strip() or link
                summary = self._text(entry.find("atom:summary", ATOM_NS))
                content = self._text(entry.find("atom:content", ATOM_NS))

                body_html = content or summary
                body = self._strip_html(body_html)

                published_at = self._parse_atom_date(
                    self._text(entry.find("atom:published", ATOM_NS))
                    or self._text(entry.find("atom:updated", ATOM_NS))
                )

                # author/name
                author_el = entry.find("atom:author/atom:name", ATOM_NS)
                author = author_el.text.strip() if author_el is not None and author_el.text else None

                # article id 추출 (URL 패턴 /category/123456/slug)
                m = re.search(r"/(\d{4,})/", link)
                article_id = m.group(1) if m else hashlib.md5(link.encode()).hexdigest()[:8]

                external_id = hashlib.md5(f"{link}#{article_id}".encode()).hexdigest()[:16]

                full_content = f"{title}\n{body}".strip() if body else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={"article_id": article_id, "atom_id": entry_id, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"TheVerge entry 파싱 실패: {e}")

        return results

    async def _fetch_listing_links(self, client: httpx.AsyncClient, url: str) -> List[str]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        # The Verge 기사 URL 패턴: /category/123456/slug
        pattern = re.compile(r'href="(/[a-z-]+/\d{4,}/[a-z0-9-]+)"')
        seen = set()
        out: List[str] = []
        for m in pattern.finditer(resp.text):
            path = m.group(1)
            full = BASE_URL + path
            if full not in seen:
                seen.add(full)
                out.append(full)
        return out

    async def _fetch_article(self, client: httpx.AsyncClient, url: str) -> Optional[RawVOC]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # 제목
        title_el = soup.find("meta", attrs={"property": "og:title"})
        title = title_el.get("content", "").strip() if title_el else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # 본문 — duet--article--article-body-component 내부 p 태그 추출
        body_parts: List[str] = []
        for el in soup.select("div.duet--article--article-body-component p, div.duet--article--article-body-component li"):
            txt = el.get_text(" ", strip=True)
            if txt:
                body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback: og:description
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = desc_el.get("content", "").strip() if desc_el else ""

        if not title and not body:
            return None

        # 발행일 — meta[property=article:published_time]
        pub_el = soup.find("meta", attrs={"property": "article:published_time"})
        published_at = self._parse_atom_date(pub_el.get("content") if pub_el else "") if pub_el else None

        # 저자 — meta[name=author]
        author_el = soup.find("meta", attrs={"name": "author"})
        author = author_el.get("content", "").strip() if author_el else None

        m = re.search(r"/(\d{4,})/", url)
        article_id = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:8]
        external_id = hashlib.md5(f"{url}#{article_id}".encode()).hexdigest()[:16]

        # 본문 길이 제한 (지나치게 긴 longform 컷 — 약 4000자)
        if len(body) > 4000:
            body = body[:4000]

        content = f"{title}\n{body}".strip() if body else title

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="US",
            meta={"article_id": article_id, "source": "html"},
        )

    # --- helpers ---

    @staticmethod
    def _text(el) -> str:
        if el is None or el.text is None:
            return ""
        return html_lib.unescape(el.text)

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        # 태그 제거 + 공백 정리
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        # "Read the full story at The Verge." 트레일러 제거
        no_tags = re.sub(r"Read the full story at The Verge\.\s*$", "", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_atom_date(self, text: str) -> Optional[datetime]:
        """ISO8601 'YYYY-MM-DDTHH:MM:SS±HH:MM' → UTC datetime"""
        if not text:
            return None
        try:
            # Python 3.11+ 는 fromisoformat 이 +00:00 형식 지원
            t = text.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
