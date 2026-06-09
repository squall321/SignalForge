"""
AnandTech Forums 크롤러 — httpx + Tag pages 진입

forums.anandtech.com (XenForo 2.3) 의 Mobile Devices 서브포럼
(`/forums/mobile-devices.55/`) 은 로그인 게이트로 막혀 있어 (HTTP 403,
`template-login`) 게스트 목록 진입이 불가능하다. 또한 서브포럼 RSS
(`/forums/mobile-devices.55/index.rss`) 도 403. 단,

  - 사이트 전체 RSS `/forums/-/index.rss`            → 200 (Mobile 비중 낮음)
  - 태그 페이지 `/tags/<tag>/[page-N]`               → 200 (게스트 OK)
  - 스레드 페이지 `/threads/<slug>.<id>/[page-N]`     → 200 (게스트 OK)

전략 (MacRumors RSS + XenForo 본문/댓글 패턴 결합)
  - `/tags/samsung/` `/tags/galaxy/` `/tags/android/` 의 페이지를 순회해
    Galaxy/Samsung/Android 관련 스레드 후보를 모은다 (XenForo 태그는
    사이트 전반에 부착돼 있어 Mobile Devices 외 SSD/메모리 글도 섞이지만
    Galaxy 키워드 필터로 후행 정제)
  - 최신 활동순 스레드 본문(OP)+댓글을 page-N 까지 수집해 RawVOC 리스트로 변환
  - 댓글 external_id = md5(thread_url + "#c" + post-XXXXX) — XenForo 안정 ID
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://forums.anandtech.com"

# Galaxy/Samsung/Android 신호가 강한 태그 (게스트 200). 순서대로 순회.
ANANDTECH_TAGS = [
    ("samsung", "Samsung"),
    ("galaxy",  "Galaxy"),
    ("android", "Android"),
]

# 태그 페이지당 ~20스레드. samsung 은 다수 페이지, galaxy/android 는 1-2페이지.
TAG_PAGES = 4
# 스레드 내부 페이지(댓글) 보강 한도 — 너무 깊이 들어가면 오래된 댓글까지 끌어옴
THREAD_REPLY_PAGES = 2
# 본문 수집 대상 상한
MAX_POSTS = 150

# 영문 사이트 — Galaxy 관련 필터 (XenForo 태그 외 추가 안전망)
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra", "buds", "watch", "one ui", "oneui",
    "exynos", "tab s", "note ",
]


class AnandTechCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "anandtech", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[dict] = []  # {url, title}

        async with self._make_httpx_client() as client:
            # XenForo 가 reverse-proxy 헤더 검사를 하므로 기본 헤더 보강
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )

            # 1) 태그 페이지에서 스레드 URL 후보 수집
            for tag_slug, tag_name in ANANDTECH_TAGS:
                for page in range(1, TAG_PAGES + 1):
                    try:
                        threads = await self._fetch_tag_page(client, tag_slug, page)
                        if not threads:
                            break  # 더 이상 페이지 없음
                        candidates.extend(threads)
                        logger.info(
                            f"  AnandTech tag={tag_name} p{page}: {len(threads)}개 스레드"
                        )
                        await self._random_delay()
                    except httpx.HTTPStatusError as e:
                        # 페이지 범위 초과 시 404 → 다음 태그로
                        logger.debug(
                            f"  AnandTech tag={tag_name} p{page} 종료: {e.response.status_code}"
                        )
                        break
                    except Exception as e:
                        logger.warning(f"  AnandTech tag={tag_name} p{page} 실패: {e}")
                        break

            # 2) URL 단위 중복 제거 + Galaxy 키워드 1차 필터 (제목 기반)
            seen = set()
            filtered: List[dict] = []
            for c in candidates:
                if c["url"] in seen:
                    continue
                seen.add(c["url"])
                if self._title_is_galaxy(c["title"]):
                    filtered.append(c)

            target = filtered[:MAX_POSTS]
            logger.info(
                f"AnandTech 후보 {len(candidates)} → 고유+필터 {len(filtered)} → 상세 {len(target)}건"
            )

            # 3) 스레드 본문 + 댓글 수집
            raw_vocs: List[RawVOC] = []
            for c in target:
                await self._random_delay()
                try:
                    detail = await self._fetch_thread_detail(client, c["url"], c["title"])
                    raw_vocs.extend(detail)
                except Exception as e:
                    logger.warning(f"  AnandTech 상세 실패 ({c['url']}): {e}")

        logger.info(f"AnandTech 수집 완료: {len(raw_vocs)}건 (스레드 {len(target)}건)")
        return raw_vocs

    # ----- 태그 페이지 -----
    async def _fetch_tag_page(
        self, client: httpx.AsyncClient, tag_slug: str, page: int
    ) -> List[dict]:
        if page == 1:
            url = f"{BASE_URL}/tags/{tag_slug}/"
        else:
            url = f"{BASE_URL}/tags/{tag_slug}/page-{page}"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        return self._parse_tag_page(resp.text)

    def _parse_tag_page(self, html: str) -> List[dict]:
        """tag 페이지는 XenForo 검색결과 형태 — /threads/<slug>.<id> 링크 추출"""
        soup = BeautifulSoup(html, "lxml")
        results: List[dict] = []
        seen_local = set()
        for a in soup.select('a[href*="/threads/"]'):
            href = a.get("href", "")
            m = re.search(r"(/threads/[a-z0-9\-]+\.\d+)", href)
            if not m:
                continue
            canon = m.group(1)
            if canon in seen_local:
                continue
            seen_local.add(canon)
            title = a.get_text(strip=True)
            # 노이즈(빈 텍스트, 'Last Post' 같은 메타링크) 제거
            if not title or len(title) < 5:
                continue
            results.append({
                "url": f"{BASE_URL}{canon}/",
                "title": title,
            })
        return results

    # ----- 스레드 상세 -----
    async def _fetch_thread_detail(
        self, client: httpx.AsyncClient, thread_url: str, hint_title: str
    ) -> List[RawVOC]:
        """스레드의 본문 + 댓글을 RawVOC 리스트로 반환"""
        all_vocs: List[RawVOC] = []
        body_voc: Optional[RawVOC] = None
        comment_count_total = 0

        for page in range(1, THREAD_REPLY_PAGES + 1):
            page_url = thread_url if page == 1 else f"{thread_url}page-{page}"
            try:
                resp = await client.get(page_url, headers={"Referer": BASE_URL + "/"})
                if resp.status_code == 404:
                    break  # 더 이상 페이지 없음
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                break
            except Exception as e:
                logger.debug(f"  AnandTech 스레드 page {page} 실패: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            articles = soup.select("article.message")
            if not articles:
                break

            for art in articles:
                pid = (art.get("data-content") or "").strip()  # 예: 'post-40411265'
                if not pid:
                    continue
                author = (art.get("data-author") or "").strip() or None

                body_el = art.select_one(".message-body .bbWrapper")
                if not body_el:
                    continue
                # 인용/스크립트 정리
                for trash in body_el.select("script, style, .bbCodeBlock--quote"):
                    trash.decompose()
                body_text = body_el.get_text("\n", strip=True)
                body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
                if not body_text or len(body_text) < 5:
                    continue

                # ISO8601 datetime 속성 (예: 2021-01-14T15:26:44-0500)
                time_el = art.select_one("time.u-dt[datetime]")
                ts = self._parse_iso_dt(time_el.get("datetime")) if time_el else None

                # 첫 메시지(OP) 가 본문, 나머지는 댓글로 분리
                if body_voc is None:
                    body_voc = RawVOC(
                        external_id=hashlib.md5(thread_url.encode()).hexdigest()[:16],
                        content=f"{hint_title}\n{body_text}".strip(),
                        source_url=thread_url,
                        author_name=author,
                        published_at=ts,
                        country_code="US",
                        meta={"first_post_id": pid},
                    )
                else:
                    comment_count_total += 1
                    all_vocs.append(RawVOC(
                        external_id=hashlib.md5(
                            f"{thread_url}#c{pid}".encode()
                        ).hexdigest()[:16],
                        content=body_text,
                        source_url=thread_url,
                        author_name=author,
                        published_at=ts,
                        country_code="US",
                    ))

            await self._random_delay()

        if body_voc:
            body_voc.comments_count = comment_count_total
            slug = thread_url.rstrip("/").split("/")[-1]
            logger.info(
                f"  AnandTech 스레드 {slug}: 본문 + 댓글 {comment_count_total}건"
            )
            return [body_voc] + all_vocs
        return []

    # ----- 필터/유틸 -----
    def _title_is_galaxy(self, title: str) -> bool:
        t = (title or "").lower()
        return any(kw in t for kw in GALAXY_KEYWORDS)

    def _parse_iso_dt(self, text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        try:
            # XenForo: '2021-01-14T15:26:44-0500' (콜론 없는 오프셋)
            # python 3.12 fromisoformat 은 콜론 없는 오프셋 지원, 안전을 위해 보강
            s = text.strip()
            if re.search(r"[+\-]\d{4}$", s):
                s = s[:-5] + s[-5:-2] + ":" + s[-2:]
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
