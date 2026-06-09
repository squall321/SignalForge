"""
Gadgets 360 크롤러 — httpx + 카테고리 RSS + Samsung 태그 페이지(HTML)

gadgets360.com (인도) 의 Samsung/Galaxy 관련 기사를 수집한다.
Akamai 가 빈약한 헤더(curl/agent only)는 403 으로 차단하지만,
브라우저 유사 헤더 세트(User-Agent + Accept + Accept-Language + Accept-Encoding +
Referer) 를 모두 채우면 RSS/HTML 모두 200 OK 응답한다.

전략
  - 카테고리 RSS 5종(/rss/<cat>/feeds) 을 병합:
      mobiles, wearables, laptops, apps, tablets   (각 약 1000건)
    제목+요약에 Samsung/Galaxy 키워드 포함 항목만 필터.
  - 추가로 Samsung 태그 페이지(/tags/samsung?pgno=N) 3페이지에서 article URL
    수집(RSS 에 없는 인디아 전용 기사 보강).
  - 댓글은 외부 forum 위젯(comment-embed-min.js) 으로 로드되며 API 가 Akamai
    차단(403) → 본문 위주 수집. 본문은 JSON-LD <NewsArticle>.articleBody 가
    완전한 평문 본문을 담고 있어 1순위 사용. 없으면 og:description 폴백.
  - published_at 은 RSS pubDate(IST/+0530) 또는 JSON-LD datePublished →
    UTC 로 변환 저장.
"""
import hashlib
import html as html_lib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gadgets360.com"
FEED_URL = "{base}/rss/{cat}/feeds"
TAG_URL = "{base}/tags/samsung?pgno={page}"

# Samsung/Galaxy 관련 글이 섞여 들어올 카테고리 피드들 (각 약 1000건)
GADGETS360_FEEDS = [
    ("mobiles",   "Mobiles"),
    ("wearables", "Wearables"),
    ("laptops",   "Laptops"),
    ("apps",      "Apps"),
    ("tablets",   "Tablets"),
]

# Samsung 태그 페이지에서 추가 URL 수집 (RSS 에 없는 인디아 전용 기사 보강)
TAG_PAGES = 3

# HTML 본문 보강 대상 (본문 길이 짧은 항목만)
HTML_ENRICH_LIMIT = 40

# 최종 처리 캡
MAX_POSTS = 150

NS = {"dc": "http://purl.org/dc/elements/1.1/"}

# Samsung 단어 매칭은 \b 단어경계로 — "tab"/"watch"/"ring"/"fold" 등은 일반어라
# 단독으로는 false positive 가 너무 많아(예: Oura Ring) 제외. Samsung 의 구체적
# 제품 키워드(galaxy s/note/z/m/a/buds/watch+모델번호) 만 인정.
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{2}"             # Galaxy S22..S27
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{2}"  # M/A/F/Note 시리즈
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# 기사 URL 패턴: /<section>/<type>/<slug>-<id>
ARTICLE_URL_RE = re.compile(
    r"/(mobiles|wearables|audio|laptops|tablets|apps|telecom|tv|cameras|games|how-to)"
    r"/(news|reviews|features)/[a-z0-9-]+-(\d+)(?:#[^\"\\s]*)?",
    re.I,
)


class Gadgets360Crawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "gadgets360", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # Akamai 통과용 브라우저 풀세트
            # 주의: gadgets360 Akamai 는 httpx 의 Chrome UA 를 TLS 핑거프린트
            # 불일치로 403 차단한다. Firefox UA 로 강제(Akamai 통과 확인됨).
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            )
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate, br"

            # 1) 카테고리 RSS 수집 → Samsung 키워드 필터
            for cat, cat_name in GADGETS360_FEEDS:
                try:
                    posts = await self._fetch_feed(client, cat)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  Gadgets360 RSS [{cat_name}]: {len(filtered)}/{len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Gadgets360 RSS [{cat_name}] 실패: {e}")

            # 2) Samsung 태그 페이지에서 URL 보강 (제목 미상태 → 후속 HTML 보강 단계에서 채움)
            tag_urls: List[str] = []
            for page in range(1, TAG_PAGES + 1):
                try:
                    urls = await self._fetch_tag_urls(client, page)
                    tag_urls.extend(urls)
                    logger.info(f"  Gadgets360 Tag p{page}: URL {len(urls)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Gadgets360 Tag p{page} 실패: {e}")

            # 기존 RSS 수집 URL 집합
            seen: set = set()
            unique: List[RawVOC] = []
            for it in items:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 태그 URL 중 신규만 placeholder VOC 로 추가 (본문은 HTML 보강 단계에서 채움)
            for url in tag_urls:
                if url in seen:
                    continue
                seen.add(url)
                aid_m = re.search(r"-(\d+)(?:[#?]|$)", url)
                article_id = aid_m.group(1) if aid_m else \
                    hashlib.md5(url.encode()).hexdigest()[:12]
                external_id = hashlib.md5(
                    f"{url}#{article_id}".encode()
                ).hexdigest()[:16]
                unique.append(RawVOC(
                    external_id=external_id,
                    content="",  # 후속 단계에서 채움
                    source_url=url,
                    author_name=None,
                    published_at=None,
                    country_code="IN",
                    meta={"article_id": article_id, "source": "tag"},
                ))

            # 3) HTML 본문 보강 — 본문이 짧거나 비어있는 항목
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            enrich_targets = [v for v in unique if len(v.content) < 300][:HTML_ENRICH_LIMIT]
            logger.info(
                f"  Gadgets360 HTML 보강: {len(enrich_targets)}건 후보"
            )
            for voc in enrich_targets:
                try:
                    enriched = await self._fetch_article_body(client, voc.source_url)
                    if enriched is None:
                        continue
                    title, body, pub_at, author = enriched
                    if body and len(body) > len(voc.content):
                        voc.content = f"{title}\n{body}".strip() if title else body
                    elif title and not voc.content:
                        voc.content = title
                    if pub_at and not voc.published_at:
                        voc.published_at = pub_at
                    if author and not voc.author_name:
                        voc.author_name = author
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"    article {voc.source_url} 보강 실패: {e}")

            # 비어있는 항목 제거, Galaxy 무관 항목 컷
            unique = [v for v in unique if v.content and self._is_galaxy_related(v)]

        # 최신순 정렬 → 상위 MAX_POSTS
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = unique[:MAX_POSTS]
        logger.info(
            f"Gadgets360 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    # ---------- fetchers ----------

    async def _fetch_feed(self, client: httpx.AsyncClient, cat: str) -> List[RawVOC]:
        url = FEED_URL.format(base=BASE_URL, cat=cat)
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        # 명시적 인코딩(서버 utf-8)
        return self._parse_feed(resp.text)

    async def _fetch_tag_urls(self, client: httpx.AsyncClient, page: int) -> List[str]:
        url = TAG_URL.format(base=BASE_URL, page=page)
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        resp.raise_for_status()
        found: List[str] = []
        seen: set = set()
        for m in ARTICLE_URL_RE.finditer(resp.text):
            path = m.group(0).split("#")[0]
            full = BASE_URL + path if path.startswith("/") else path
            if full in seen:
                continue
            seen.add(full)
            found.append(full)
        return found

    async def _fetch_article_body(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[tuple]:
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return None
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        body = ""
        pub_at: Optional[datetime] = None
        author: Optional[str] = None

        # 1) JSON-LD NewsArticle 우선 — articleBody 가 완전한 본문 제공
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for d in candidates:
                if not isinstance(d, dict):
                    continue
                if d.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle"):
                    title = title or (d.get("headline") or "").strip()
                    body_raw = d.get("articleBody") or ""
                    if body_raw and len(body_raw) > len(body):
                        body = body_raw.strip()
                    dp = d.get("datePublished") or d.get("dateCreated")
                    if dp and pub_at is None:
                        pub_at = self._parse_iso_date(dp)
                    auth = d.get("author")
                    if auth and author is None:
                        if isinstance(auth, list) and auth:
                            auth = auth[0]
                        if isinstance(auth, dict):
                            author = (auth.get("name") or "").strip() or None
                        elif isinstance(auth, str):
                            author = auth.strip() or None

        # 2) 폴백 — og:title / og:description
        if not title:
            og_t = soup.find("meta", attrs={"property": "og:title"})
            title = og_t.get("content", "").strip() if og_t else ""
        if not body:
            og_d = soup.find("meta", attrs={"property": "og:description"})
            body = og_d.get("content", "").strip() if og_d else ""

        # 본문 길이 제한 (longform 컷)
        if len(body) > 4000:
            body = body[:4000]

        if not title and not body:
            return None
        return (title, body, pub_at, author)

    # ---------- parsers ----------

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Gadgets360 RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                # RSS link 에 #rss-gadgets-xxx fragment 가 붙음 → 제거해 canonical URL 사용
                clean_link = link.split("#")[0]

                guid_raw = (item.findtext("guid") or clean_link).strip()
                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw).strip()
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                # URL 마지막 숫자가 안정적 article id
                aid_m = re.search(r"-(\d+)(?:[#?]|$)", clean_link)
                article_id = aid_m.group(1) if aid_m else \
                    hashlib.md5(clean_link.encode()).hexdigest()[:12]
                external_id = hashlib.md5(
                    f"{clean_link}#{article_id}".encode()
                ).hexdigest()[:16]

                content = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=clean_link,
                    author_name=author,
                    published_at=published_at,
                    country_code="IN",
                    meta={"article_id": article_id, "guid": guid_raw, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"Gadgets360 item 파싱 실패: {e}")
        return results

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 형식 'Fri, 29 May 2026 17:50:03 +0530' (IST) → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # naive 이면 IST 가정 (gadgets360 본사)
                from datetime import timedelta
                ist = timezone(timedelta(hours=5, minutes=30))
                dt = dt.replace(tzinfo=ist)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_iso_date(self, text: str) -> Optional[datetime]:
        """JSON-LD ISO8601 '2026-05-28T07:00:01+05:30' → UTC datetime"""
        if not text:
            return None
        try:
            # Z 처리
            s = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                from datetime import timedelta
                ist = timezone(timedelta(hours=5, minutes=30))
                dt = dt.replace(tzinfo=ist)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
