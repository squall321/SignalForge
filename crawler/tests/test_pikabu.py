"""Pikabu collector — Data Grow R4 K2 신규 사이트.

목표
====
1. parse_search_html() 가 BeautifulSoup 으로 article.story 를 정확히
   분리하고 title/body/author/datetime/rating 을 추출한다.
2. story_id 결손, title-link 결손, 본문 < 30자 stories 는 스킵된다.
3. 외부 호출 없이 미리 캡처한 HTML 픽스쳐만 사용 (CI 안정).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from platforms.pikabu import parse_search_html, _parse_dt, _to_int  # noqa: E402


def _fixture_html() -> str:
    """최소 3 stories (1 정상 / 1 title 없음 / 1 본문 < 30자) 픽스쳐."""
    return """
    <html><body>
    <article class="story" data-story-id="14039065">
      <a class="story__title-link" href="https://pikabu.ru/story/galaxy_review_14039065?utm=x">
        Обзор Galaxy S25 после месяца использования
      </a>
      <div class="story-block story-block_type_text">
        Купил Samsung Galaxy S25 месяц назад. В целом смартфон отличный,
        камера улучшилась по сравнению с S24. Время автономной работы порядка
        7 часов экрана. Но цена кусается.
      </div>
      <div class="story-block story-block_type_text">
        Из минусов: иногда подвисает при многозадачности.
      </div>
      <a class="user__nick">tech_reviewer</a>
      <time datetime="2026-06-08T14:23:11+03:00">8 июня</time>
      <span class="story__rating-count">42</span>
      <span class="story__comments-link-count">13</span>
    </article>

    <article class="story" data-story-id="14040000">
      <!-- title-link 없음 → advert/promo 카드 → skip 되어야 함 -->
      <div class="story__advert-info">Реклама</div>
    </article>

    <article class="story" data-story-id="14041000">
      <a class="story__title-link" href="https://pikabu.ru/story/short_14041000">Galaxy</a>
      <div class="story-block story-block_type_text">короткий</div>
      <!-- title+body < 30자 → skip -->
    </article>

    <article class="story" data-story-id="14042000">
      <a class="story__title-link" href="https://pikabu.ru/story/foldable_news_14042000">
        Galaxy Z Fold 7 утечка
      </a>
      <div class="story-block story-block_type_text">
        Появились новые рендеры Galaxy Z Fold 7 с тонкой петлей.
        Презентация ожидается в июле 2026.
      </div>
      <a class="user__nick">leakster_ru</a>
      <time datetime="2026-06-09T10:00:00+03:00">сегодня</time>
      <span class="story__rating-count">1,2K</span>
      <span class="story__comments-link-count">87</span>
    </article>
    </body></html>
    """


def test_parse_search_html_extracts_valid_stories():
    vocs = parse_search_html(_fixture_html(), query="Galaxy")
    # 4 articles 중 2 valid (skip: advert no title, skip: < 30자)
    assert len(vocs) == 2, f"expected 2 valid stories, got {len(vocs)}"

    # 첫 story 필드 정확성
    v1 = next(v for v in vocs if v.meta["story_id"] == "14039065")
    assert "Galaxy S25" in v1.content
    assert "месяц назад" in v1.content  # text block 본문 포함
    assert "Из минусов" in v1.content  # 다중 text block 결합 확인
    assert v1.source_url == "https://pikabu.ru/story/galaxy_review_14039065"  # ?utm 제거
    assert v1.author_name == "tech_reviewer"
    assert v1.country_code == "RU"
    assert v1.likes_count == 42
    assert v1.comments_count == 13
    assert v1.published_at is not None
    assert v1.published_at.year == 2026 and v1.published_at.month == 6

    # 두번째 story — K 단위 rating
    v2 = next(v for v in vocs if v.meta["story_id"] == "14042000")
    assert v2.likes_count == 1200, f"1,2K → 1200, got {v2.likes_count}"
    assert v2.comments_count == 87
    assert v2.meta["query"] == "Galaxy"


def test_parse_dt_handles_offset_and_none():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    d = _parse_dt("2026-06-08T14:23:11+03:00")
    assert d is not None
    assert d.tzinfo is not None
    # +03:00 → UTC 11:23:11
    assert d.hour == 11 and d.minute == 23


def test_to_int_handles_kilo_and_garbage():
    assert _to_int("42") == 42
    assert _to_int("1,2K") == 1200  # 러시아 표기 ',' 십진점
    assert _to_int("3M") == 3_000_000
    assert _to_int("") == 0
    assert _to_int(None) == 0
    assert _to_int("garbage") == 0


def test_external_id_stable_and_short():
    """동일 story_id → 동일 external_id, 길이 16."""
    vocs = parse_search_html(_fixture_html(), query="Galaxy")
    for v in vocs:
        assert len(v.external_id) == 16
    vocs2 = parse_search_html(_fixture_html(), query="Samsung")
    # 같은 story_id 라면 query 가 달라도 external_id 동일.
    map1 = {v.meta["story_id"]: v.external_id for v in vocs}
    map2 = {v.meta["story_id"]: v.external_id for v in vocs2}
    for sid in map1:
        assert map1[sid] == map2[sid]
