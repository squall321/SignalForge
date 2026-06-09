"""
Tudo Celular 크롤러 — httpx + 메인 RSS + 카테고리 페이지 + per-article /feed/ 우회

tudocelular.com (브라질 모바일 전문 매체) 의 Samsung/Galaxy 관련 기사 본문 수집.

접근성
  - 일반 페이지 (`/`, `/Samsung/`) 는 Cloudflare Turnstile 챌린지로 차단됨 (한국 ASN ASN 차단).
  - 그러나 다음 두 종류 엔드포인트는 Firefox UA 로 우회 가능 (HTTP 200):
      a) 메인 RSS: /feed/  → 표준 RSS XML, 최근 ~20건 + content:encoded 풍부
      b) 카테고리 검색-페이지: /<keyword>/feed/  → HTML 검색 결과 (~4건 per keyword)
      c) 개별 기사 /feed/: /<categoria>/noticias/n<id>/<slug>.html/feed/  → 본문 HTML
         (기사 직접 URL 은 429 차단되지만 .html/feed/ 는 통과)

전략
  - 메인 RSS 에서 신규 기사 + content:encoded 의 본문 일부 수집
  - /samsung/feed/, /galaxy/feed/ 검색 페이지에서 Samsung 관련 추가 URL 확보
  - 각 후보 URL 에 대해 .html/feed/ 로 풀 본문 HTML 추출 (textblock div 파싱)
  - Galaxy/Samsung 키워드 매칭만 보존, 브라질=BRT(UTC-3) 시각 변환

시각
  - RSS pubDate 는 RFC822 (예: "Sat, 30 May 2026 16:20:00 +0200") → email.utils 파싱
  - 본문에 ISO date 가 노출되는 경우 적음 → 주로 RSS pubDate 사용
  - naive datetime 은 BRT(UTC-3) 기준 으로 UTC 변환

댓글
  - tudocelular 댓글은 JS 동적 로드(템플릿 + Ajax) — 정적 HTML 에서 추출 불가.
  - 본문 자체가 정보 밀도가 충분하므로 본문 1건/기사 로 단순화.
"""
import hashlib
import html as html_lib
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

BASE_URL = "https://www.tudocelular.com"
RSS_URL = f"{BASE_URL}/feed/"

# Samsung/Galaxy 관련 카테고리 검색-페이지 (각 ~4-10건)
TC_KEYWORDS = [
    ("samsung",  "Samsung"),
    ("galaxy",   "Galaxy"),
]

# 본문 fetch 캡 — 차단 회피 위해 보수적으로
DETAIL_LIMIT = 60
# 최종 처리 캡
MAX_POSTS = 150

# 브라질리아 표준시 (BRT = UTC-3). 브라질은 2019년 이후 서머타임 폐지 → 연중 -3 고정.
BRT = timezone(timedelta(hours=-3))

# 브라질 포르투갈어 + 영문 키워드 (모바일 매체이므로 광범위)
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]

# 기사 URL 패턴 — /<categoria>/noticias/n<id>/<slug>.html
ARTICLE_URL_RE = re.compile(
    r'https://www\.tudocelular\.com/[A-Za-z0-9_-]+/noticias/n(\d+)/[a-z0-9-]+\.html'
)


# Firefox UA — Cloudflare Turnstile 우회 (Xataka/Gadgets360 패턴과 동일)
FIREFOX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"


class TudoCelularCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "tudocelular", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    def _make_httpx_client(self) -> httpx.AsyncClient:
        # Cloudflare Turnstile 우회 — Firefox UA + pt-BR Accept-Language
        return httpx.AsyncClient(
            headers={
                "User-Agent": FIREFOX_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        candidates: dict = {}  # url -> (article_id, source_kind, rss_meta)

        async with self._make_httpx_client() as client:
            # 1) 메인 RSS — content:encoded 본문 일부 + pubDate
            try:
                rss_items = await self._fetch_main_rss(client)
                rss_added = 0
                for it in rss_items:
                    m = ARTICLE_URL_RE.match(it["url"])
                    if not m:
                        continue
                    # Samsung/Galaxy 키워드 매칭만
                    blob = (it["title"] + " " + it["body_seed"] + " " + it.get("category", "")).lower()
                    if not self._is_galaxy_related_text(blob):
                        continue
                    if it["url"] not in candidates:
                        candidates[it["url"]] = {
                            "article_id": m.group(1),
                            "source": "rss",
                            "rss_meta": it,
                        }
                        rss_added += 1
                logger.info(
                    f"  TudoCelular RSS: {rss_added} Samsung 매치 (전체 {len(rss_items)})"
                )
            except Exception as e:
                logger.warning(f"  TudoCelular RSS 실패: {e}")

            await self._random_delay()

            # 2) /<keyword>/feed/ 카테고리 검색-페이지 — URL 보강
            for kw, label in TC_KEYWORDS:
                try:
                    urls = await self._fetch_keyword_listing(client, kw)
                    new_count = 0
                    for u in urls:
                        m = ARTICLE_URL_RE.match(u)
                        if not m:
                            continue
                        if u not in candidates:
                            candidates[u] = {
                                "article_id": m.group(1),
                                "source": f"kw:{label}",
                                "rss_meta": None,
                            }
                            new_count += 1
                    logger.info(
                        f"  TudoCelular kw [{label}]: {new_count} 신규 (전체 {len(urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  TudoCelular kw [{label}] 실패: {e}")

            # 3) 상위 N건 상세 fetch — .html/feed/ 로 풀 본문 HTML
            urls_sorted = list(candidates.keys())[:DETAIL_LIMIT]
            logger.info(
                f"TudoCelular 후보 {len(candidates)}건 중 상세 수집 {len(urls_sorted)}건"
            )

            raw_vocs: List[RawVOC] = []
            for art_url in urls_sorted:
                await self._random_delay()
                meta = candidates[art_url]
                try:
                    voc = await self._fetch_article_via_feed(client, art_url, meta)
                    if voc is not None:
                        raw_vocs.append(voc)
                except Exception as e:
                    logger.warning(f"  TudoCelular 상세 실패 ({art_url}): {e}")

        # external_id 중복 제거
        seen: set = set()
        unique: List[RawVOC] = []
        for v in raw_vocs:
            if v.external_id in seen:
                continue
            seen.add(v.external_id)
            unique.append(v)

        result = unique[:MAX_POSTS]
        logger.info(
            f"TudoCelular 수집 완료: {len(result)}건 (후보 {len(candidates)} → 본문 {len(unique)})"
        )
        return result

    # --- listing ---

    async def _fetch_main_rss(self, client: httpx.AsyncClient) -> List[dict]:
        resp = await client.get(
            RSS_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        # RSS 는 UTF-8 (헤더에서 명시)
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []
        items: List[dict] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc_raw = item.findtext("description") or ""
            content_raw = item.findtext(
                "{http://purl.org/rss/1.0/modules/content/}encoded"
            ) or ""
            # 카테고리 (다중 가능, 첫 번째만)
            cat_el = item.find("category")
            category = (cat_el.text if cat_el is not None and cat_el.text else "")

            # body_seed — content:encoded 또는 description 의 텍스트 (Samsung 매칭용)
            body_seed = re.sub(r"<[^>]+>", " ", html_lib.unescape(content_raw or desc_raw))
            body_seed = re.sub(r"\s+", " ", body_seed).strip()

            if not title or not link:
                continue
            items.append({
                "title": title,
                "url": link,
                "pub": pub,
                "category": category,
                "body_seed": body_seed,
            })
        return items

    async def _fetch_keyword_listing(
        self, client: httpx.AsyncClient, keyword: str
    ) -> List[str]:
        """/<keyword>/feed/ — 카테고리 검색 결과 페이지 (HTML)

        예: /samsung/feed/  →  ~4건 의 기사 URL.
        실제 URL 은 외부 도메인 의 a href 로 노출됨.
        """
        url = f"{BASE_URL}/{keyword}/feed/"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return []
        # 인코딩: ISO-8859-1 (헤더에서 명시) → httpx 가 자동 변환하지 못할 수 있음
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            html = resp.content.decode("iso-8859-1", errors="replace")
        else:
            html = resp.text

        seen: set = set()
        out: List[str] = []
        for m in ARTICLE_URL_RE.finditer(html):
            u = m.group(0)
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    # --- detail ---

    async def _fetch_article_via_feed(
        self, client: httpx.AsyncClient, url: str, cand_meta: dict
    ) -> Optional[RawVOC]:
        """기사 본문을 <url>/feed/ 형태로 가져옴 (직접 URL 은 429 차단)."""
        feed_url = url + "/feed/"
        resp = await client.get(feed_url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            logger.debug(f"  TudoCelular feed {resp.status_code}: {url}")
            return None

        # ISO-8859-1 인코딩
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            html = resp.content.decode("iso-8859-1", errors="replace")
        else:
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        # 제목 — <title>...</title>
        title = ""
        if soup.title and soup.title.string:
            t = soup.title.string.strip()
            # " - TudoCelular.com" 꼬리 제거
            title = re.sub(r"\s*-\s*TudoCelular\.com\s*$", "", t).strip()
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # 본문 — div.textblock 내부 p / li
        body_parts: List[str] = []
        # notice_content 컨테이너 안의 textblock 만 (사이드바 textblock 회피)
        notice = soup.find(id="notice_content") or soup
        for tb in notice.select("div.textblock"):
            for el in tb.find_all(["p", "li"]):
                txt = el.get_text(" ", strip=True)
                if txt and len(txt) > 5:
                    body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback — meta og:description / RSS body_seed
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = (desc_el.get("content", "").strip() if desc_el else "")
        if not body and cand_meta.get("rss_meta"):
            body = (cand_meta["rss_meta"] or {}).get("body_seed", "") or ""

        if not title and not body:
            return None

        # Galaxy/Samsung 관련 글만
        full_blob = (title + " " + body).lower()
        if not self._is_galaxy_related_text(full_blob):
            return None

        # 발행일 — RSS pubDate 우선, 없으면 og:updated_time / meta
        published_at: Optional[datetime] = None
        rss_meta = cand_meta.get("rss_meta")
        if rss_meta:
            published_at = self._parse_rss_date(rss_meta.get("pub", ""))
        if published_at is None:
            pub_el = (
                soup.find("meta", attrs={"property": "article:published_time"})
                or soup.find("meta", attrs={"property": "og:updated_time"})
            )
            if pub_el:
                published_at = self._parse_iso_date(pub_el.get("content", ""))

        # 저자 — meta[name=author] 또는 article:author
        author = None
        au = (
            soup.find("meta", attrs={"name": "author"})
            or soup.find("meta", attrs={"property": "article:author"})
        )
        if au:
            a = au.get("content", "").strip()
            if a and a.lower() != "tudocelular.com":
                author = a

        # 본문 길이 컷
        if len(body) > 4000:
            body = body[:4000]

        full_content = f"{title}\n{body}".strip() if body else title

        article_id = cand_meta.get("article_id") or hashlib.md5(url.encode()).hexdigest()[:12]

        logger.info(
            f"  TudoCelular 상세 n{article_id}: 본문 {len(body)}자 ({cand_meta.get('source')})"
        )

        return RawVOC(
            external_id=hashlib.md5(f"{url}#{article_id}".encode()).hexdigest()[:16],
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="BR",
            meta={"article_id": article_id, "source": cand_meta.get("source", "tc")},
        )

    # --- helpers ---

    def _is_galaxy_related_text(self, text: str) -> bool:
        t = (text or "").lower()
        if not t.strip():
            return False
        return any(kw in t for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 16:20:00 +0200' → UTC"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_iso_date(self, text: str) -> Optional[datetime]:
        """ISO8601 → UTC. naive 면 브라질 BRT(-3) 가정."""
        if not text:
            return None
        try:
            t = text.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
