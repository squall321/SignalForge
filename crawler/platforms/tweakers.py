"""
Tweakers 크롤러 — httpx + RSS 기반, DPG Media WAF 우회

tweakers.net (네덜란드 최대 IT 커뮤니티/뉴스, NL, DPG Media) 의 Samsung/Galaxy
관련 기사 본문 수집.

전략
  - 기사 본문 페이지(/nieuws/<id>/<slug>.html)는 DPG MyPrivacy 동의 게이트로
    HTML 본체가 가려져 8KB 자바스크립트 stub 만 반환됨. WAF 도 활성.
  - Gathering 포럼(gathering.tweakers.net) 의 list_topics 는 403, 단일 message
    페이지는 404 — 포럼 크롤은 불가.
  - 그러나 공식 RSS 피드 /feeds/<channel>.xml 은 200 OK 로 응답하며
    title + description (1-3문장 요약) + author + category + pubDate 가 모두
    들어있어 VOC 정규화에 충분함. 페이지네이션은 동작하지 않으므로 채널 다양화
    (mixed/nieuws/reviews/plan) 로 후보 폭을 확보.
  - 시간: pubDate (RFC822, +0200 CEST / +0100 CET 명시) → UTC 변환.
    naive 일 경우 CET(UTC+1)으로 가정해 시간대를 부여.
  - 키워드 필터: Samsung/Galaxy 우선. Tweakers 가 종합 테크 사이트라 단일
    스냅샷에는 Samsung 글이 적을 수 있어, 폰/태블릿/안드로이드 등
    Galaxy 와 인접한 카테고리 키워드를 함께 사용 (Samsung 출시 직후 운영
    시 자연스럽게 직격 키워드가 다수 매칭됨).
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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://tweakers.net"

# 공식 RSS 채널 — 모두 200 OK 확인됨 (2026-06-01 기준)
RSS_FEEDS = [
    ("mixed",   "Mixed Feed"),    # 종합 (nieuws + reviews + plan + downloads + .adv)
    ("nieuws",  "Nieuws"),        # 뉴스 단독
    ("reviews", "Reviews"),       # 정식 리뷰
    ("plan",    "Plannen"),       # 곧 출시 예정 제품
]

# RSS 페이지네이션은 무시되므로 (?page=N → 동일 결과) 채널 다각화로만 후보 확보.
# 기존 contract 변수는 유지하되 실질 동작은 채널 루프 1회.
LIST_PAGES = 12
MAX_POSTS = 150

# 네덜란드 시간대 (CET/CEST). DST: 마지막 일요일 3월 → 마지막 일요일 10월.
# 단순화를 위해 naive datetime 폴백 시 CET(UTC+1)로 가정 (RSS 가 거의 항상
# offset 을 포함하므로 실제로는 폴백 진입 거의 없음).
CET = timezone(timedelta(hours=1))

# Samsung Galaxy 우선 + 인접 폰 카테고리 키워드 (네덜란드어 포함)
# 단어 경계 매칭을 위해 정규식 패턴으로 보관. \b 로 substring 오탐 방지
# (ex. "Ultratoren" 안의 "ultra", "uitbring" 안의 "ring" 같은 케이스 제외).
GALAXY_KEYWORDS = [
    r"\bsamsung\b", r"\bgalaxy\b",
    r"\bs2[3-9]\b", r"\bs3[0-9]\b",
    r"\bz\s*fold\b", r"\bz\s*flip\b",
    r"\bgalaxy\s+(fold|flip|ultra|tab|buds|watch|ring)\b",
    r"\bone\s*ui\b", r"\boneui\b", r"\bexynos\b", r"\bbixby\b",
    # 인접 폰 카테고리 키워드 (Tweakers 가 종합 사이트라 폭을 약간 넓힘).
    # telefoon/smartphone 은 단/복수 모두 매칭하도록 \b 접미사 없이 prefix 매칭.
    r"\bandroid", r"\bsmartphone", r"\btelefoon",
    r"\bgoogle\s+pixel\b",
]
GALAXY_PATTERN = re.compile(r"|".join(GALAXY_KEYWORDS), re.IGNORECASE)


class TweakersCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "tweakers", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_links: set = set()

        async with self._make_httpx_client() as client:
            # Firefox UA + nl Accept-Language 가 WAF 통과율 가장 높음
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            )
            client.headers["Accept-Language"] = "nl-NL,nl;q=0.9,en;q=0.7"
            client.headers["Accept-Encoding"] = "gzip, deflate, br"

            for channel, label in RSS_FEEDS:
                try:
                    posts = await self._fetch_feed(client, channel)
                    if not posts:
                        logger.info(f"  Tweakers {label}: 0건")
                        continue

                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    new_count = 0
                    for p in filtered:
                        if p.source_url in seen_links:
                            continue
                        seen_links.add(p.source_url)
                        items.append(p)
                        new_count += 1
                    logger.info(
                        f"  Tweakers {label}: {new_count} 신규 "
                        f"(전체 {len(posts)} / 필터 {len(filtered)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Tweakers {label} 실패: {e}")

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"Tweakers 수집 완료: {len(result)}건 (후보 {len(items)})")
        return result

    async def _fetch_feed(
        self, client: httpx.AsyncClient, channel: str
    ) -> List[RawVOC]:
        url = f"{BASE_URL}/feeds/{channel}.xml"
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if resp.status_code != 200:
            logger.debug(f"Tweakers {channel} HTTP {resp.status_code}")
            return []
        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Tweakers RSS 파싱 실패: {e}")
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        results: List[RawVOC] = []
        for item in channel.findall("item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                # GUID 에서 안정 ID 추출 (Tweakers GUID 는 'tweakers.net/<type>/<id>')
                guid = (item.findtext("guid") or "").strip()
                post_id = self._extract_post_id(guid) or hashlib.md5(
                    link.encode()
                ).hexdigest()[:12]

                desc = self._strip_html(item.findtext("description") or "")
                # 본문 길이 컷
                if len(desc) > 4000:
                    desc = desc[:4000]

                full_content = f"{title}\n{desc}".strip() if desc else title
                if len(full_content) < 20:
                    continue

                published_at = self._parse_rss_date(item.findtext("pubDate") or "")
                author = (item.findtext("author") or "").strip() or None
                category = (item.findtext("category") or "").strip()

                external_id = hashlib.md5(
                    f"{link}#{post_id}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=full_content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="NL",
                    meta={
                        "post_id": post_id,
                        "category": category,
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"Tweakers item 파싱 실패: {e}")

        return results

    # --- helpers ---

    @staticmethod
    def _extract_post_id(guid: str) -> Optional[str]:
        """GUID 'https://tweakers.net/nieuws/248508' 형태에서 마지막 숫자 추출."""
        if not guid:
            return None
        m = re.search(r"/(\d{3,})(?:[/?#]|$)", guid)
        if m:
            return m.group(1)
        return None

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
        text = voc.content or ""
        if not text.strip():
            return False
        if GALAXY_PATTERN.search(text):
            return True
        cat = voc.meta.get("category") or ""
        return bool(GALAXY_PATTERN.search(cat))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 20:55:15 +0200' → UTC.
        naive 일 경우 CET(UTC+1) 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CET)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
