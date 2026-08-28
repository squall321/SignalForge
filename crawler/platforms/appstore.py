# 앱스토어 리뷰 수집기 — Google Play(내부 API) + Apple App Store(RSS)의 Samsung 앱 리뷰
"""
App Store Reviews 크롤러 — Samsung 앱의 사용자 리뷰(별점+본문) = 고밀도 VOC.

- Google Play: 내부 batchexecute API(RPC UsvDTd)로 리뷰 조회. 라이브러리 불필요.
  Samsung Members(com.samsung.android.voc = 문자 그대로 Voice-of-Customer 앱)·Galaxy
  Store·Good Lock·Samsung Notes·SmartThings·Health·Bixby·One UI Home 등.
- Apple App Store: 공식 customerreviews RSS(JSON). SmartThings·Health·Galaxy Wearable.
- 국가별(us/kr/in/gb/br/de) fan-out. 별점은 meta.rating, published_at 은 리뷰 작성시각.

플랫폼 코드: appstore  (alembic 0025 platforms row)
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

_PLAY_URL = "https://play.google.com/_/PlayStoreUi/data/batchexecute"
_APPLE_RSS = "https://itunes.apple.com/{c}/rss/customerreviews/id={id}/sortBy=mostRecent/json"

# (표시명, Play 패키지) — 삼성 안드로이드 앱
_PLAY_APPS = [
    ("Samsung Members", "com.samsung.android.voc"),
    ("Galaxy Store", "com.sec.android.app.samsungapps"),
    ("Good Lock", "com.samsung.android.goodlock"),
    ("Samsung Notes", "com.samsung.android.app.notes"),
    ("SmartThings", "com.samsung.android.oneconnect"),
    ("Samsung Health", "com.sec.android.app.shealth"),
    ("Bixby", "com.samsung.android.bixby.agent"),
    ("One UI Home", "com.sec.android.app.launcher"),
]
# (표시명, Apple ID) — 삼성 iOS 앱
_APPLE_APPS = [
    ("SmartThings", "1222822904"),
    ("Samsung Health", "1224541484"),
    ("Galaxy Wearable", "1117310635"),
]
_MARKETS = [("us", "en"), ("kr", "ko"), ("in", "en"), ("gb", "en"), ("br", "pt"), ("de", "de")]


class AppStoreCrawler(BaseCrawler):
    """Google Play + Apple App Store 의 Samsung 앱 리뷰 수집."""

    MIN_DELAY = 0.3
    MAX_DELAY = 0.8
    PLAY_COUNT = 80   # 앱·국가당 리뷰 수(최신)

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("appstore", product_code=product_code, job_id=job_id)

    async def crawl(self) -> List[RawVOC]:
        seen: set = set()
        out: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            for gl, hl in _MARKETS:
                for name, pkg in _PLAY_APPS:
                    out += await self._play(client, name, pkg, gl, hl, seen)
                    await self._random_delay()
            for c, _ in _MARKETS:
                for name, aid in _APPLE_APPS:
                    out += await self._apple(client, name, aid, c, seen)
                    await self._random_delay()
        logger.info("앱스토어 리뷰 수집 완료 — %d건", len(out))
        return out

    async def _play(self, client, name, pkg, gl, hl, seen) -> List[RawVOC]:
        inner = json.dumps([None, None, [2, 2, [self.PLAY_COUNT, None, None], None, []], [pkg, 7]])
        freq = json.dumps([[["UsvDTd", inner, None, "generic"]]])
        try:
            r = await client.post(
                f"{_PLAY_URL}?hl={hl}&gl={gl}", data={"f.req": freq},
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            if r.status_code != 200:
                return []
            line = next(l for l in r.text.split("\n") if l.startswith("[["))
            reviews = json.loads(json.loads(line)[0][2])[0]
        except (httpx.HTTPError, StopIteration, ValueError, IndexError, TypeError):
            return []
        res: List[RawVOC] = []
        for rv in reviews:
            try:
                rid = rv[0]
                author = rv[1][0] if rv[1] else None
                rating = rv[2]
                text = (rv[4] or "").strip()
                ts = rv[5][0] if (len(rv) > 5 and rv[5]) else None
                if not text or len(text) < 5:
                    continue
                uid = hashlib.md5(f"appstore#play#{rid}".encode()).hexdigest()[:16]
                if uid in seen:
                    continue
                seen.add(uid)
                res.append(RawVOC(
                    external_id=uid,
                    content=text,
                    source_url=f"https://play.google.com/store/apps/details?id={pkg}&reviewId={rid}",
                    author_name=author,
                    published_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                    country_code=gl.upper(),
                    meta={"store": "google_play", "app": name, "package": pkg, "rating": rating},
                ))
            except (IndexError, TypeError, ValueError):
                continue
        return res

    async def _apple(self, client, name, aid, country, seen) -> List[RawVOC]:
        try:
            r = await client.get(_APPLE_RSS.format(c=country, id=aid))
            if r.status_code != 200:
                return []
            entries = (r.json().get("feed", {}) or {}).get("entry", []) or []
        except (httpx.HTTPError, ValueError):
            return []
        res: List[RawVOC] = []
        for e in entries:
            if "im:rating" not in e:   # 첫 entry 는 앱 메타(리뷰 아님)
                continue
            rid = (e.get("id", {}) or {}).get("label", "")
            text = ((e.get("title", {}) or {}).get("label", "") + " — "
                    + (e.get("content", {}) or {}).get("label", "")).strip(" —")
            if not text:
                continue
            uid = hashlib.md5(f"appstore#apple#{rid}".encode()).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)
            rating = (e.get("im:rating", {}) or {}).get("label")
            author = ((e.get("author", {}) or {}).get("name", {}) or {}).get("label")
            res.append(RawVOC(
                external_id=uid, content=text,
                source_url=(e.get("link", {}) or {}).get("attributes", {}).get("href", "")
                           or f"https://apps.apple.com/app/id{aid}",
                author_name=author, published_at=None,
                country_code=country.upper(),
                meta={"store": "app_store", "app": name, "id": aid, "rating": rating},
            ))
        return res
