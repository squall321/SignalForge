"""
Tinhte.vn 크롤러 — httpx + RSS + 본문/댓글 보강 (XenForo + Next.js 프런트)

베트남 최대 IT 커뮤니티(XenForo 기반). `/forums/...`/`/tags/...` 등 게시판 직접 접근은
403(WAF) 으로 모두 차단되지만, 다음 두 경로는 안정적으로 200 OK 다.

  1) https://tinhte.vn/lists/trang-chu.1/index.rss        — 홈 RSS (최신 20)
     · feeds.feedburner.com/tinhte 와 동일 콘텐츠 (체크섬 일치)
     · 다른 리스트(dien-thoai.5, cong-nghe.6) 도 동일 패턴, 카테고리는 Samsung 비중↓
  2) https://tinhte.vn/thread/<slug>.<id>/                — 개별 스레드 페이지

스레드 페이지는 Next.js 기반이지만 SSR 결과에 작성자/본문/모든 댓글의 정적
HTML 이 포함돼 BS4 만으로 파싱 가능. 댓글은 `<div id="post-XXXX" class="...
thread-comment__box">` 단위로 안정 ID 가 부여돼 멱등 재크롤이 가능하다.
댓글 날짜는 JS-rendered (span title 이 비어 있음) 이라 게시물 published_at
로 fallback.

전략: 여러 리스트 RSS 를 fetch → Samsung/Galaxy 키워드 필터 → 본문은 RSS
content:encoded 사용, 스레드 페이지에서 comments_count 와 댓글 RawVOC 만 보강.
"""
import hashlib
import html as html_lib
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://tinhte.vn"

# RSS 출처들. trang-chu 외 카테고리 리스트도 같은 ?index.rss 패턴.
# /forums/<f>.61/index.rss 는 403 — list ID 만 우회 가능.
TINHTE_FEEDS = [
    ("/lists/trang-chu.1/index.rss",  "Trang chủ"),
    ("/lists/dien-thoai.5/index.rss", "Điện thoại"),
    ("/lists/cong-nghe.6/index.rss",  "Công nghệ"),
]

# RSS 페이지네이션은 지원되지 않아 1회당 ~60 candidate. 키워드 필터 후 본문 보강.
MAX_POSTS = 150

# 베트남어 컨텐츠지만 "Samsung"/"Galaxy"/"S2x" 는 외래어 그대로 사용 — 영문 키워드로 충분
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s25", "s26", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "one ui", "oneui", "exynos",
]


class TinhteCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "tinhte", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "vi-VN,vi;q=0.9,en;q=0.6"
            client.headers["Referer"] = BASE_URL + "/"

            # 1) 리스트 RSS 들을 모두 수집 → Samsung 필터
            for feed_path, feed_name in TINHTE_FEEDS:
                try:
                    items = await self._fetch_feed(client, feed_path)
                    filtered = [it for it in items if self._is_galaxy_related(it)]
                    candidates.extend(filtered)
                    logger.info(
                        f"  Tinhte {feed_name}: {len(filtered)}/{len(items)}건 (Samsung)"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Tinhte {feed_name} 실패: {e}")

            # 2) URL 단위 중복 제거 (홈/카테고리 중복 노출)
            seen: set = set()
            unique: List[RawVOC] = []
            for it in candidates:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 3) 최신순 정렬 → 상위 MAX_POSTS 만 본문+댓글 보강
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            targets = unique[:MAX_POSTS]
            logger.info(
                f"Tinhte 후보 {len(candidates)} → 고유 {len(unique)} → 상세 {len(targets)}건"
            )

            results: List[RawVOC] = []
            for post in targets:
                await self._random_delay()
                try:
                    detail = await self._fetch_thread_detail(client, post)
                    results.extend(detail)
                except Exception as e:
                    logger.warning(f"  Tinhte 상세 실패 ({post.source_url}): {e}")
                    # 보강 실패 시에도 RSS 본문은 유지
                    results.append(post)

        # MX 필터 적용 (Data Clean 2 / D1)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(results)
        results = [v for v in results if is_mx_relevant(v.content)]
        logger.info(f"Tinhte 수집 완료: {len(results)}건 (MX 필터 적용 {before_n}→{len(results)})")
        return results

    # ----- RSS -----
    async def _fetch_feed(self, client: httpx.AsyncClient, feed_path: str) -> List[RawVOC]:
        url = BASE_URL + feed_path
        resp = await client.get(url, headers={
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        })
        resp.raise_for_status()
        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Tinhte RSS 파싱 실패: {e}")
            return []

        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "content": "http://purl.org/rss/1.0/modules/content/",
            "slash": "http://purl.org/rss/1.0/modules/slash/",
        }
        out: List[RawVOC] = []

        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                # 풍부한 본문은 content:encoded 에 들어 있음 (description 은 발췌)
                enc = item.find("content:encoded", ns)
                body_html = (enc.text or "") if (enc is not None and enc.text) else (item.findtext("description") or "")
                body_text = self._html_to_text(body_html)

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator = item.find("dc:creator", ns)
                author = creator.text.strip() if (creator is not None and creator.text) else None

                # 댓글 수: <slash:comments> 또는 <thr:total>
                slash_c = item.find("slash:comments", ns)
                try:
                    ccount = int((slash_c.text or "0").strip()) if slash_c is not None else 0
                except ValueError:
                    ccount = 0

                # 안정 ID: link 의 thread id (마지막 ".숫자/")
                m = re.search(r"\.(\d+)/?$", link)
                thread_id = m.group(1) if m else hashlib.md5(link.encode()).hexdigest()[:12]
                external_id = hashlib.md5(link.encode()).hexdigest()[:16]

                combined = f"{title}\n{body_text}".strip() if body_text else title

                out.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    comments_count=ccount,
                    country_code="VN",
                    meta={"thread_id": thread_id},
                ))
            except Exception as e:
                logger.debug(f"Tinhte item 파싱 실패: {e}")
        return out

    # ----- 스레드 상세 (댓글) -----
    async def _fetch_thread_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        resp = await client.get(post.source_url)
        if resp.status_code >= 400:
            logger.debug(f"Tinhte 스레드 {resp.status_code}: {post.source_url}")
            return [post]
        soup = BeautifulSoup(resp.text, "lxml")

        comment_vocs: List[RawVOC] = []
        # 각 댓글: <div id="post-XXXX" class="... thread-comment__box ...">
        boxes = soup.select('div[id^="post-"].thread-comment__box, div.thread-comment__box[id^="post-"]')
        # BS4 multi-class selector 의 한계로 fallback selector 도 사용
        if not boxes:
            boxes = [b for b in soup.find_all("div", id=re.compile(r"^post-\d+$"))
                     if "thread-comment__box" in " ".join(b.get("class") or [])]

        first_post_seen = False
        for box in boxes:
            post_id = box.get("id", "").replace("post-", "").strip()
            if not post_id:
                continue

            author_a = box.select_one(".thread-comment__author .author-name") \
                       or box.select_one(".author-name")
            cauthor = author_a.get_text(strip=True) if author_a else None

            body_el = box.select_one(".xfBody")
            if not body_el:
                continue
            # 인용 블록(bbCodeQuote) 은 본문 시그널 흐림 → 제거
            for q in body_el.select(".bbCodeQuote, script, style, .TinhteMods_Tag_Info"):
                q.decompose()
            ctext = body_el.get_text("\n", strip=True)
            ctext = re.sub(r"\n{3,}", "\n\n", ctext).strip()
            if not ctext or len(ctext) < 5:
                continue

            # 첫 thread-comment__box 가 본문 작성자 자신의 'OP 게시물' 인 경우가 있다.
            # 본문 보강 용도이지 별도 RawVOC 로 추가하면 본문 글과 중복되므로 스킵.
            if not first_post_seen and cauthor and post.author_name \
                    and cauthor.strip().lower() == post.author_name.strip().lower():
                first_post_seen = True
                continue
            first_post_seen = True

            # 댓글 안정 external_id (md5(url + "#c" + post_id))
            cext = hashlib.md5(f"{post.source_url}#c{post_id}".encode()).hexdigest()[:16]
            comment_vocs.append(RawVOC(
                external_id=cext,
                content=ctext,
                source_url=post.source_url,
                author_name=cauthor,
                # 댓글 정확 시각은 JS-rendered → 본문 시각으로 fallback (ICT→UTC 변환은 RSS 단계에서 완료)
                published_at=post.published_at,
                country_code="VN",
                meta={"comment_id": post_id},
            ))

        body_voc = RawVOC(
            external_id=post.external_id,
            content=post.content,
            source_url=post.source_url,
            author_name=post.author_name,
            published_at=post.published_at,
            comments_count=len(comment_vocs) or post.comments_count,
            country_code="VN",
            meta=post.meta,
        )
        logger.info(
            f"  Tinhte 스레드 {post.meta.get('thread_id', '?')}: "
            f"본문 {len(post.content)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    # ----- 유틸 -----
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _html_to_text(self, html: str) -> str:
        if not html:
            return ""
        # CDATA 안의 HTML 태그 제거 + 엔터티 디코드
        txt = html_lib.unescape(html)
        txt = re.sub(r"<br\s*/?>", "\n", txt)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        return txt.strip()

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Mon, 01 Jun 2026 16:36:34 +0000' → UTC datetime.
        ICT(UTC+7) 표기가 들어와도 parsedate_to_datetime 이 offset 을 처리해 UTC 정렬됨."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # naive → ICT (UTC+7) 로 간주 후 UTC 변환
                from datetime import timedelta
                ict = timezone(timedelta(hours=7))
                dt = dt.replace(tzinfo=ict)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
