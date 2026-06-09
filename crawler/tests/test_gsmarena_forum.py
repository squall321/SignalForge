"""
GSMArena Forum 크롤러 단위 테스트 (Harvest 4 H4)

외부 네트워크 없이 (1) 목록 파싱 + EXCLUDED 처리, (2) 리뷰 페이지 파싱 (rev_id /
본문/작성자/likes/published_at), (3) external_id 안정성, (4) httpx 모킹된 전체
파이프라인을 검증한다.

실행: cd crawler && python -m pytest tests/test_gsmarena_forum.py -v
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.gsmarena_forum import (  # noqa: E402
    GSMArenaForumCrawler,
    SLUG_TO_PRODUCT_CODE,
    BASE_URL,
)


SAMPLE_LIST_HTML = """
<html><body>
<div class="makers"><ul>
  <li><a href="samsung_galaxy_s26_5g-14456.php">S26 5G</a></li>
  <li><a href="samsung_galaxy_s26_ultra_5g-14320.php">S26 Ultra</a></li>
  <li><a href="samsung_galaxy_z_trifold_5g-14292.php">Z Trifold</a></li>
  <li><a href="samsung_galaxy_a57_5g-14379.php">A57</a></li>
  <li><a href="samsung_galaxy_s25-13610.php">S25 (excluded)</a></li>
  <li><a href="samsung_galaxy_z_fold7-13826.php">Z Fold7 (excluded)</a></li>
  <li><a href="iphone-1234.php">non-samsung</a></li>
</ul></div>
</body></html>
"""

SAMPLE_REVIEWS_HTML = """
<html><body>
<div class="user-thread material-card" id="7059451">
  <ul class="user-thread-meta">
    <li class="uname2">Alice</li>
    <li class="upost"><time>5 days ago</time></li>
  </ul>
  <p class="uopin">
    Great phone but battery drains fast. Camera is amazing though.
  </p>
  <ul class="votes" data-votes="12"></ul>
</div>
<div class="user-thread material-card" id="7059433">
  <ul class="user-thread-meta">
    <li class="uname2">Bob</li>
    <li class="upost"><time>01 May 2026</time></li>
  </ul>
  <p class="uopin">
    <span class="uinreply">Quoting Alice:</span>
    <span class="uinreply-msg">Great phone but battery drains fast.</span>
    Agreed. Battery life is the weakest point of this device.
  </p>
  <ul class="votes" data-votes="3"></ul>
</div>
<div class="user-thread material-card" id="7059354">
  <ul class="user-thread-meta">
    <li class="upost"><time>yesterday</time></li>
  </ul>
  <p class="uopin">no</p>
  <ul class="votes" data-votes="0"></ul>
</div>
</body></html>
"""


# ------------------------------------------------------------
# Test 1: 목록 파싱 — EXCLUDED 5건 제외, 순서 유지, 중복 제거
# ------------------------------------------------------------
def test_parse_device_list_filters_excluded():
    c = GSMArenaForumCrawler()
    devs = c._parse_device_list(SAMPLE_LIST_HTML)
    slug_ids = [s for _, _, s in devs]
    # Samsung 갤럭시 4개만 — S25/Z Fold7 EXCLUDED, iphone 정규식 매칭 안 됨
    assert len(devs) == 4, f"기대 4건 (EXCLUDED 2건 + iPhone 1건 제외), got {slug_ids}"
    assert slug_ids[0] == "samsung_galaxy_s26_5g-14456", \
        "목록 페이지 순서가 유지되어야 함"
    assert "samsung_galaxy_s25-13610" not in slug_ids
    assert "samsung_galaxy_z_fold7-13826" not in slug_ids
    # 각 튜플은 (slug, id, "slug-id")
    slug, dev_id, slug_id = devs[0]
    assert slug == "samsung_galaxy_s26_5g"
    assert dev_id == "14456"
    assert slug_id == f"{slug}-{dev_id}"


def test_parse_device_list_dedupes():
    c = GSMArenaForumCrawler()
    # 같은 디바이스가 여러 위치(스펙 영역/사이드바)에 등장해도 1번만
    html_dup = (
        '<a href="samsung_galaxy_a17-14157.php">x</a>'
        '<a href="samsung_galaxy_a17-14157.php">y</a>'
    )
    devs = c._parse_device_list(html_dup)
    assert len(devs) == 1


# ------------------------------------------------------------
# Test 2: 리뷰 파싱 — rev_id, 본문 정제, 인용 제거, likes, 날짜
# ------------------------------------------------------------
def test_parse_reviews_page_basics():
    c = GSMArenaForumCrawler()
    page_url = f"{BASE_URL}/samsung_galaxy_s26_5g-reviews-14456.php"
    items = c._parse_reviews_page(
        html=SAMPLE_REVIEWS_HTML, page_url=page_url, slug="samsung_galaxy_s26_5g"
    )
    # 3번째 블록 ("no") 은 < 5자 컷
    assert len(items) == 2, f"기대 2건, got {len(items)}"

    a = items[0]
    assert a.external_id == "gsmaf_7059451"
    assert a.source_url == f"{page_url}#7059451"
    assert "battery drains fast" in a.content
    assert a.author_name == "Alice"
    assert a.likes_count == 12
    assert a.country_code == "US"
    assert a.meta["slug"] == "samsung_galaxy_s26_5g"
    # SLUG_TO_PRODUCT_CODE 에 매핑 존재 → meta 에 product_code 전파
    assert a.meta.get("product_code") == SLUG_TO_PRODUCT_CODE[
        "samsung_galaxy_s26_5g"
    ]

    b = items[1]
    # 인용 블록(span.uinreply, span.uinreply-msg) 제거 후 작성자 본문만 남아야
    assert "Quoting Alice" not in b.content
    assert "Battery life is the weakest point" in b.content
    # "01 May 2026" 절대 날짜
    assert b.published_at == datetime(
        2026, 5, 1, tzinfo=timezone.utc
    )


# ------------------------------------------------------------
# Test 3: external_id 안정성 — rev_id 동일 → external_id 동일, prefix 구분
# ------------------------------------------------------------
def test_external_id_prefix_distinguishes_from_legacy_gsmarena():
    c = GSMArenaForumCrawler()
    page_url = f"{BASE_URL}/samsung_galaxy_s26_5g-reviews-14456.php"
    items = c._parse_reviews_page(
        html=SAMPLE_REVIEWS_HTML, page_url=page_url, slug="samsung_galaxy_s26_5g"
    )
    # 기존 gsmarena.py 는 gsma_<id> — forum 은 gsmaf_<id>
    assert all(v.external_id.startswith("gsmaf_") for v in items)
    assert not any(v.external_id.startswith("gsma_") and
                   not v.external_id.startswith("gsmaf_") for v in items)


# ------------------------------------------------------------
# Test 4: 날짜 파서 — 절대/상대/yesterday
# ------------------------------------------------------------
def test_parse_gsmarena_date_variants():
    parse = GSMArenaForumCrawler._parse_gsmarena_date
    assert parse("01 May 2026") == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert parse("") is None
    assert parse("garbage text") is None
    # 상대 시간 — 정확한 값보다는 timezone aware UTC 여부 확인
    rel = parse("3 days ago")
    assert rel is not None and rel.tzinfo == timezone.utc
    yest = parse("yesterday")
    assert yest is not None and yest.tzinfo == timezone.utc


# ------------------------------------------------------------
# Test 5: 통합 crawl() — httpx 모킹으로 목록→reviews 페이지 흐름 검증
# ------------------------------------------------------------
def test_crawl_end_to_end_with_mocked_httpx(monkeypatch):
    """MockTransport 로 (a) 목록 URL → SAMPLE_LIST_HTML, (b) 임의 reviews URL →
    SAMPLE_REVIEWS_HTML 응답을 주입하고, 전체 파이프라인이 RawVOC 를 생성하는지 확인.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("samsung-phones-9.php"):
            return httpx.Response(200, text=SAMPLE_LIST_HTML)
        if "-reviews-" in request.url.path:
            return httpx.Response(200, text=SAMPLE_REVIEWS_HTML)
        return httpx.Response(404, text="")

    transport = httpx.MockTransport(handler)

    def fake_client(self):  # type: ignore[no-untyped-def]
        return httpx.AsyncClient(transport=transport, follow_redirects=True)

    monkeypatch.setattr(
        GSMArenaForumCrawler, "_make_httpx_client", fake_client
    )

    # 딜레이 제거 — 테스트 빨리 끝나도록
    async def no_delay(self):  # type: ignore[no-untyped-def]
        return None
    monkeypatch.setattr(GSMArenaForumCrawler, "_random_delay", no_delay)

    c = GSMArenaForumCrawler()
    c.MAX_DEVICES = 2  # 빠른 회전
    raw = asyncio.run(c.crawl())

    # 목록 4 디바이스 (EXCLUDED 제외) × 첫 페이지 2건 × MAX_DEVICES=2
    assert len(raw) >= 4, f"통합 파이프라인 결과 부족: {len(raw)}"
    # 모든 RawVOC 가 gsmarena_forum slug meta 보유
    assert all(r.meta.get("slug", "").startswith("samsung_galaxy_") for r in raw)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
