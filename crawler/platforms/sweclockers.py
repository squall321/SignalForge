"""
SweClockers 크롤러 — httpx + 공식 RSS 피드 (SE)

sweclockers.com (스웨덴 메이저 IT 매체 + 자체 포럼). HTML 페이지 (/artikel/, /forum/,
/sok 등) 는 Cloudflare 챌린지(cf-mitigated: challenge, 403)로 모든 UA 차단되나,
공식 RSS 엔드포인트 /feeds/<channel> 은 200 OK 로 응답한다.

접근성 (2026-06-01 확인)
  - https://www.sweclockers.com               → 403 (CF challenge)
  - https://www.sweclockers.com/sok?q=...      → 403
  - https://www.sweclockers.com/forum          → 403
  - https://www.sweclockers.com/artikel/<id>/  → 403
  - https://www.sweclockers.com/forum/trad/<id>/ → 403
  - https://www.sweclockers.com/feeds/nyheter  → 200, 50건/호출
  - https://www.sweclockers.com/feeds/artiklar → 200, 50건/호출
  - https://www.sweclockers.com/feeds/forum    → 200, 50건/호출, **본문 HTML 포함**
  - https://www.sweclockers.com/feeds/galleri  → 200, 50건/호출

전략 (Tweakers/MyBroadband 패턴)
  - 본문 페이지 fetch 가 막혀 댓글 수집은 불가. RSS 자체가 information-dense:
    forum 피드는 스레드 본문 전체 HTML 을, 뉴스 피드는 제목+요약을 제공.
  - 3종 RSS 를 모두 수집 → URL/guid 단위 중복 제거 → Samsung/Galaxy 키워드 필터.
  - 시간: pubDate 가 RFC822 + '+0200' (CEST) → email.utils.parsedate_to_datetime
    이 자동 TZ-aware 파싱. astimezone(UTC) 만 적용.
  - RSS 응답이 brotli 면 httpx 가 디코드 못 하므로 Accept-Encoding 을 gzip/deflate
    로 제한 (MacRumors/ComputerBase 와 동일 처리).

키워드 필터
  - 'samsung', 'galaxy', 'one ui', 'exynos', 'bixby', 's25', 's24', 'fold', 'flip'
  - 스웨덴어 매체이나 제품명은 영문 그대로 사용.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sweclockers.com"

# (피드 경로, 표시명, kind)
# kind="forum" 은 본문 HTML 포함 (description 안에 <p> 전체) → 정보 밀도 높음
# kind="news" 는 제목 + 단문 description (1-2줄)
SWC_FEEDS = [
    ("/feeds/nyheter",  "Nyheter",  "news"),
    ("/feeds/artiklar", "Artiklar", "news"),
    ("/feeds/forum",    "Forum",    "forum"),
]

MAX_POSTS = 150
LIST_PAGES = 12  # RSS 자체는 페이지네이션 미지원 — 의례적 상한 (반복 호출 의미 없음)

# Samsung/Galaxy 관련 키워드 (스웨덴어 매체, 제품명은 영문)
GALAXY_KEYWORDS = [
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra", "buds", "watch",
    "one ui", "oneui", "exynos", "bixby",
    "tizen",  # 가전 영역도 Samsung
]


class SweClockersCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "sweclockers", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        all_items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # CF 가 일부 UA 에 관대. Firefox UA + 스웨덴어 우선.
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            )
            client.headers["Accept-Language"] = "sv-SE,sv;q=0.9,en;q=0.6"
            # brotli 디코더 미설치 환경 회피
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for feed_path, feed_name, kind in SWC_FEEDS:
                try:
                    items = await self._fetch_feed(client, feed_path, kind)
                    if kind == "news":
                        # 뉴스 피드는 Samsung 키워드 필터
                        filtered = [it for it in items if self._is_galaxy_related(it)]
                    else:
                        # 포럼 피드는 본문 텍스트가 풍부 → 키워드 필터
                        filtered = [it for it in items if self._is_galaxy_related(it)]
                    all_items.extend(filtered)
                    logger.info(
                        f"  SweClockers {feed_name}: {len(filtered)}/{len(items)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  SweClockers {feed_name} 피드 실패: {e}")

            # URL/external_id 단위 중복 제거 (피드 간 겹침은 거의 없으나 안전 장치)
            seen: set = set()
            unique: List[RawVOC] = []
            for it in all_items:
                key = it.external_id
                if key in seen:
                    continue
                seen.add(key)
                unique.append(it)

            # 최신순 정렬 + 상한
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            result = unique[:MAX_POSTS]

        logger.info(
            f"SweClockers 수집 완료: {len(result)}건 (전체 후보 {len(all_items)}, 고유 {len(unique)})"
        )
        return result

    # ----- RSS -----
    async def _fetch_feed(
        self, client: httpx.AsyncClient, feed_path: str, kind: str
    ) -> List[RawVOC]:
        url = BASE_URL + feed_path
        resp = await client.get(url, headers={
            "Referer": BASE_URL + "/",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        })
        resp.raise_for_status()
        # 명시적 UTF-8 디코딩 — RSS 헤더에 charset=UTF-8 명시됨
        text = resp.content.decode("utf-8", errors="replace")
        return self._parse_rss(text, kind)

    def _parse_rss(self, xml_text: str, kind: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"SweClockers RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw)
                # 포럼 피드는 <p>, <br/>, <a> 가 풍부 — 줄바꿈 보존 후 태그 제거
                desc = re.sub(r"<br\s*/?>", "\n", desc, flags=re.IGNORECASE)
                desc = re.sub(r"</p>", "\n\n", desc, flags=re.IGNORECASE)
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"[ \t]+", " ", desc)
                desc = re.sub(r"\n{3,}", "\n\n", desc).strip()

                # 길이 캡 — forum 본문이 매우 길 수 있음
                if len(desc) > 6000:
                    desc = desc[:6000]

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                # 안정 ID: guid 가 있으면 우선 사용 (예: https://...sweclockers.com/artikel/42748)
                guid_el = item.find("guid")
                guid_val = (guid_el.text or "").strip() if guid_el is not None else ""
                stable_key = guid_val or link

                # forum 피드는 comments 요소 없음. news 피드는 comments=forum thread URL
                comments_url = (item.findtext("comments") or "").strip()

                # 본문 결합
                combined = f"{title}\n{desc}".strip() if desc else title

                external_id = hashlib.md5(
                    f"{stable_key}#sweclockers".encode("utf-8")
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=None,  # RSS 가 author 미제공
                    published_at=published_at,
                    country_code="SE",
                    meta={
                        "kind": kind,
                        "guid": guid_val,
                        "comments_url": comments_url or None,
                        "source": "rss",
                    },
                ))
            except Exception as e:
                logger.debug(f"SweClockers item 파싱 실패: {e}")

        return results

    # ----- 필터/유틸 -----
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 19:00:00 +0200' (CEST) → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # 스웨덴 CET/CEST. parsedate_to_datetime 가 명시 TZ 를 잘 처리하지만
                # 누락 시 UTC 가정 (안전 폴백).
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
