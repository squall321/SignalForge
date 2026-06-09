"""
iFixit 크롤러 — News RSS + Answers search API (수리·분해 영문 커뮤니티)

www.ifixit.com 은 Cloudflare 안 걸려 있고 공식 RSS / REST API 모두 200 OK 응답.
News (블로그형 기사) + Answers (사용자 Q&A) 양쪽에서 Galaxy 관련 콘텐츠 수집.

전략
  1) News RSS:  https://www.ifixit.com/News/feed  (전체 카테고리)
     → Samsung/Galaxy 키워드 필터 후 description / content:encoded 본문 저장.
     주: WordPress RSS 라 발행 빈도 일 1-3건, Galaxy 적중률 낮음.  그래도
     공식 분해 보고서 (S25/Fold) 같은 핵심 VOC 시그널 포착.
  2) Answers API:  /api/2.0/search/<term>?filter=question&limit=20
     → 사용자 수리 문의·증상 보고. Galaxy 관련 키워드 다회 fan-out.
     본문은 별도 GET 필요 → Answers/View/<id> HTML 의 og:description 추출.

  - external_id: News = guid (link), Answers = question id (URL 마지막 숫자)
  - country_code="US" — iFixit 본사 미국
  - 본문 길이가 너무 짧으면 og:description 으로 보강.

회고
  - News 는 발행 빈도 낮지만 *공식 분해 평가* 가 VOC 인사이트 큼.
  - Answers 는 직접적 수리 VOC (배터리 드레인·화면 깨짐 등) 시그널 풍부.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Set
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

NEWS_RSS = "https://www.ifixit.com/News/feed"
ANSWERS_SEARCH_API = (
    "https://www.ifixit.com/api/2.0/search/{q}"
    "?filter=question&limit={limit}"
)
ANSWERS_PAGE = "https://www.ifixit.com/Answers/View/{qid}"

# Answers 검색 키워드 fan-out
ANSWERS_TERMS = [
    "Samsung Galaxy",
    "Galaxy S25",
    "Galaxy S24",
    "Galaxy Fold",
    "Galaxy Flip",
    "Galaxy Note",
    "Galaxy Buds",
]
ANSWERS_PER_TERM = 20
MAX_POSTS = 150

# Galaxy/Samsung 키워드 필터 (영문)
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# Answers URL → question id
QID_RE = re.compile(r"/Answers/View/(\d+)")

# OG meta
OG_DESC_RE = re.compile(
    r'<meta\s+property="og:description"\s+content="([^"]+)"', re.I
)
OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I
)
# 답변 페이지 본문 첫 datetime 마커 (질문 작성 시각)
DATETIME_RE = re.compile(r'datetime="([^"]+)"')


class IFixitCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "ifixit", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "en-US,en;q=0.9"

            # 1) News RSS
            try:
                news_items = await self._fetch_news_rss(client)
                galaxy_news = [v for v in news_items if self._is_galaxy_related(v)]
                items.extend(galaxy_news)
                logger.info(
                    f"  iFixit News RSS: {len(galaxy_news)}/{len(news_items)}건 "
                    f"(Galaxy)"
                )
            except Exception as e:
                logger.warning(f"  iFixit News RSS 실패: {e}")

            # 2) Answers search fan-out
            seen_qids: Set[str] = set()
            for term in ANSWERS_TERMS:
                try:
                    qids = await self._search_question_ids(client, term)
                    new = [qid for qid in qids if qid not in seen_qids]
                    seen_qids.update(new)
                    logger.info(
                        f"  iFixit Answers '{term}': {len(qids)} 검색 / "
                        f"{len(new)} 신규 qid"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  iFixit Answers '{term}' 실패: {e}")

            # 3) 각 qid → og:description 본문 + datetime
            for qid in list(seen_qids)[:MAX_POSTS]:
                try:
                    voc = await self._fetch_question(client, qid)
                    if voc is None:
                        continue
                    if not self._is_galaxy_related(voc):
                        continue
                    items.append(voc)
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  iFixit qid={qid} 실패: {e}")

        # dedupe by external_id
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.external_id in seen:
                continue
            seen.add(it.external_id)
            unique.append(it)

        # 최신순
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = unique[:MAX_POSTS]
        logger.info(f"iFixit 수집 완료: {len(result)}건")
        return result

    # ---------- News RSS ----------

    async def _fetch_news_rss(self, client: httpx.AsyncClient) -> List[RawVOC]:
        resp = await client.get(NEWS_RSS)
        resp.raise_for_status()
        return self._parse_news_rss(resp.text)

    def _parse_news_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"iFixit News RSS 파싱 실패: {e}")
            return []

        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "content": "http://purl.org/rss/1.0/modules/content/",
        }
        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw)
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                content_el = item.find("content:encoded", ns)
                body = ""
                if content_el is not None and content_el.text:
                    body = html_lib.unescape(content_el.text)
                    body = re.sub(r"<[^>]+>", " ", body)
                    body = re.sub(r"\s+", " ", body).strip()

                combined = f"{title}\n{desc}\n{body}".strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", ns)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                guid_raw = (item.findtext("guid") or link).strip()
                external_id = hashlib.md5(
                    f"ifixit_news#{guid_raw}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={
                        "guid": guid_raw,
                        "source": "ifixit_news_rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"iFixit news item 파싱 실패: {e}")
        return results

    # ---------- Answers search ----------

    async def _search_question_ids(
        self, client: httpx.AsyncClient, term: str
    ) -> List[str]:
        """검색 API → question dataType 만 추려 qid (URL 끝 숫자) 반환."""
        from urllib.parse import quote
        url = ANSWERS_SEARCH_API.format(q=quote(term), limit=ANSWERS_PER_TERM)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        results = data.get("results") or []
        qids: List[str] = []
        for r in results:
            if r.get("dataType") != "question":
                continue
            url_r = r.get("url") or ""
            m = QID_RE.search(url_r)
            if m:
                qids.append(m.group(1))
        return qids

    async def _fetch_question(
        self, client: httpx.AsyncClient, qid: str
    ) -> Optional[RawVOC]:
        url = ANSWERS_PAGE.format(qid=qid)
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return self._parse_question(qid, str(resp.url), resp.text)

    def _parse_question(
        self, qid: str, url: str, html: str
    ) -> Optional[RawVOC]:
        title_m = OG_TITLE_RE.search(html)
        desc_m = OG_DESC_RE.search(html)
        dt_m = DATETIME_RE.search(html)

        title = self._unescape(title_m.group(1)) if title_m else ""
        desc = self._unescape(desc_m.group(1)) if desc_m else ""

        # 제목 trailing 카테고리 노이즈 제거: " - Samsung Galaxy A"
        title = re.sub(r"\s*-\s*Samsung\s+Galaxy[^-]*$", "", title, flags=re.I).strip()

        content = f"{title}\n{desc}".strip()
        if len(content) < 20:
            return None

        published_at = self._parse_iso(dt_m.group(1)) if dt_m else None

        external_id = hashlib.md5(f"ifixit_q#{qid}".encode()).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=None,
            published_at=published_at,
            country_code="US",
            meta={
                "qid": qid,
                "source": "ifixit_answers",
            },
        )

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    @staticmethod
    def _unescape(s: str) -> str:
        return html_lib.unescape(s or "").strip()

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_iso(text: Optional[str]) -> Optional[datetime]:
        """ISO 8601 'YYYY-MM-DDTHH:MM:SS-07:00' → UTC."""
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
