"""
Hacker News 크롤러 — Algolia HN API (인증 불필요)

- 검색: https://hn.algolia.com/api/v1/search_by_date?query=<q>&tags=story&hitsPerPage=50
- 댓글 검색: tags=comment&numericFilters=created_at_i>...
- 스토리 + 댓글 트리: https://hn.algolia.com/api/v1/items/<id>

HN id는 정수형 영구 식별자라 external_id 안정성이 매우 높다.

다양화 전략 (라운드 3 — 검색어 50+ 확장):
1. 검색어 50+개 — 모델군 / 액세서리 / SW·생태계 / 키워드 4 그룹
2. tags=story 와 tags=comment 분리 호출 → 다른 풀
3. numericFilters 로 시간 윈도우 (story 7d / comment 3d) → 같은 인기 글 반복 방지
4. objectID set 으로 검색어 간 중복 제거
5. QUERY_SAMPLE_SIZE 옵션 — int 로 두면 매 crawl 마다 random.sample 로 N 개 무작위 선택
   (None 이면 전체 검색어 사용 — 기본값, 최대 커버리지)
"""
import hashlib
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="

# 검색어 풀 (R12 — 200+ 검색어, 9 그룹, 다국어 포함)
# R6 (2026-06-04): 옛 모델 + 위기·이슈 키워드 80+
# R12 (2026-06-04): S 시리즈 풀세트 + Note 1~20 + Z Fold/Flip 1~8 + Watch/Buds/Tab 풀세트
#                   + 위기 키워드 다양화 (Note 7 / GoS / Fold 결함 / Flip 힌지 / S20 가격 / S22 발열 등)
#                   + 다국어 키워드 (한·중) + 비교/생태계
#
# HN_TERMS_FILE 환경변수로 외부 파일 (한 줄당 키워드) 지정 가능 — 미지정 시 아래 풀 사용.
QUERY_TERMS = [
    # ====================================================================
    # 그룹 1: Galaxy S 시리즈 풀세트 (S1 ~ S26 + Plus/Ultra/FE) — 55개
    # ====================================================================
    "Galaxy S26", "Galaxy S26 Ultra", "Galaxy S26+", "Galaxy S26 Plus",
    "Galaxy S25", "Galaxy S25 Ultra", "Galaxy S25 Plus", "Galaxy S25+", "Galaxy S25 FE",
    "Galaxy S24", "Galaxy S24 Ultra", "Galaxy S24 Plus", "Galaxy S24+", "Galaxy S24 FE",
    "Galaxy S23", "Galaxy S23 Ultra", "Galaxy S23 Plus", "Galaxy S23 FE",
    "Galaxy S22", "Galaxy S22 Ultra", "Galaxy S22 Plus",
    "Galaxy S21", "Galaxy S21 Ultra", "Galaxy S21 Plus", "Galaxy S21 FE",
    "Galaxy S20", "Galaxy S20 Ultra", "Galaxy S20 Plus", "Galaxy S20 FE",
    "Galaxy S10", "Galaxy S10 5G", "Galaxy S10 Plus", "Galaxy S10e",
    "Galaxy S9", "Galaxy S9 Plus",
    "Galaxy S8", "Galaxy S8 Plus", "Galaxy S8 Active",
    "Galaxy S7", "Galaxy S7 Edge", "Galaxy S7 Active",
    "Galaxy S6", "Galaxy S6 Edge", "Galaxy S6 Edge Plus", "Galaxy S6 Active",
    "Galaxy S5", "Galaxy S5 Mini", "Galaxy S5 Active",
    "Galaxy S4", "Galaxy S4 Mini", "Galaxy S4 Active", "Galaxy S4 Zoom",
    "Galaxy S III", "Galaxy S3", "Galaxy S3 Mini",
    "Galaxy S II", "Galaxy S2",
    # ====================================================================
    # 그룹 2: Note 시리즈 풀세트 (Note 1 ~ Note 20) — 13개 + 위기 키워드
    # ====================================================================
    "Galaxy Note 20", "Galaxy Note 20 Ultra",
    "Galaxy Note 10", "Galaxy Note 10 Plus",
    "Galaxy Note 9", "Galaxy Note 8",
    "Galaxy Note 7",
    "Note 7 explosion", "Note 7 recall", "Note 7 fire", "Note 7 ban", "Note 7 battery",
    "Samsung Note 7", "Note 7 airline",
    "Galaxy Note 5", "Galaxy Note 4", "Galaxy Note 3", "Galaxy Note 2",
    "Samsung Galaxy Note",
    # ====================================================================
    # 그룹 3: Z Fold / Flip 풀세트 + 결함 키워드 — 28개
    # ====================================================================
    "Galaxy Z Fold 8", "Galaxy Z Fold 7", "Galaxy Z Fold 6", "Galaxy Z Fold 5",
    "Galaxy Z Fold 4", "Galaxy Z Fold 3", "Galaxy Z Fold 2",
    "Galaxy Fold", "Galaxy Fold 1", "Galaxy Fold 3",
    "Fold display broken", "Fold display crease", "Fold display peel",
    "Galaxy Z Flip 8", "Galaxy Z Flip 7", "Galaxy Z Flip 6", "Galaxy Z Flip 5",
    "Galaxy Z Flip 4", "Galaxy Z Flip 3", "Galaxy Z Flip",
    "Flip hinge gap", "Flip hinge broken", "Flip hinge loose",
    "Galaxy foldable",
    "Z Fold review", "Z Flip review",
    "Galaxy foldable durability",
    "Samsung foldable",
    "Samsung folding phone",
    # ====================================================================
    # 그룹 4: Watch / Buds / Ring / Tab — 28개
    # ====================================================================
    "Galaxy Watch",  # base — 회귀 호환
    "Galaxy Watch Ultra", "Galaxy Watch 7", "Galaxy Watch 6", "Galaxy Watch 5",
    "Galaxy Watch 4", "Galaxy Watch 3",
    "Galaxy Watch Active", "Galaxy Watch Active 2",
    "Galaxy Gear", "Gear S", "Gear S2", "Gear S3", "Gear Fit",
    "Galaxy Buds 3", "Galaxy Buds 2", "Galaxy Buds Pro", "Galaxy Buds Live", "Galaxy Buds FE",
    "Galaxy Ring",
    "Galaxy Tab S11", "Galaxy Tab S10", "Galaxy Tab S9", "Galaxy Tab S8",
    "Galaxy Tab S7", "Galaxy Tab S6", "Galaxy Tab Active",
    "Galaxy Tab A",
    # ====================================================================
    # 그룹 5: A 시리즈 / M / 기타 — 12개
    # ====================================================================
    "Galaxy A series", "Galaxy A55", "Galaxy A54", "Galaxy A53", "Galaxy A52",
    "Galaxy A35", "Galaxy A34", "Galaxy A15",
    "Galaxy M series", "Galaxy F series",
    "Samsung Galaxy budget",
    "Samsung Galaxy mid-range",
    # ====================================================================
    # 그룹 6: SW / 생태계 — 18개
    # ====================================================================
    "One UI 8", "One UI 7", "One UI 6", "One UI",
    "TouchWiz",
    "Tizen", "Bixby",
    "Galaxy AI", "Circle to Search", "Samsung DeX",
    "Samsung Pay", "Samsung Knox", "Samsung Internet",
    "Samsung Health", "SmartThings", "Samsung Cloud",
    "Galaxy Store", "Samsung TV Plus",
    # ====================================================================
    # 그룹 7: 위기·이슈·결함 키워드 — 25개
    # ====================================================================
    "Samsung recall", "Samsung defect", "Samsung lawsuit",
    "Galaxy fire", "Galaxy battery explosion", "Galaxy hinge",
    "Samsung GoS", "Galaxy GoS", "GoS throttling", "Game Optimizer Service",
    "Galaxy throttling",
    "Samsung S22 thermal", "Galaxy S22 overheating",
    "Galaxy S23 chip", "Snapdragon vs Exynos",
    "Samsung security flaw", "Galaxy firmware bug",
    "Samsung S20 price",
    "Samsung Knox warranty void",
    "Samsung bloatware",
    "Galaxy OTA bricked",
    "Galaxy update bug",
    "Samsung Camera shutter lag",
    "Samsung Android update delay",
    "Samsung greenwashing",
    # ====================================================================
    # 그룹 8: 비교 / 시장 / 경쟁 — 11개
    # ====================================================================
    "Galaxy vs iPhone", "Samsung vs Apple", "Galaxy vs Pixel",
    "Pixel vs Galaxy", "Android vs iOS",
    "Galaxy ecosystem", "Samsung Apple compare",
    "Samsung market share", "Samsung China market",
    "Samsung India launch", "Samsung Europe launch",
    # ====================================================================
    # 그룹 9: 일반 키워드 + 다국어 — 16개
    # ====================================================================
    "samsung", "samsung galaxy", "Samsung phone", "Samsung smartphone",
    "Galaxy battery", "Galaxy camera", "Galaxy display",
    "Snapdragon Galaxy", "Exynos Galaxy",
    # 한국어 (HN 검색에서 거의 hit 없을 가능성 — 시도)
    "갤럭시", "갤럭시노트", "갤노트", "갤폴드",
    # 중국어
    "三星", "盖乐世",
]


def _load_query_terms() -> List[str]:
    """HN_TERMS_FILE 환경변수 지정 시 파일에서 검색어 로드, 미지정 시 QUERY_TERMS 사용."""
    path = os.getenv("HN_TERMS_FILE", "").strip()
    if not path:
        return list(QUERY_TERMS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            terms = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        if terms:
            logger.info("HN_TERMS_FILE=%s 로 %d개 검색어 로드", path, len(terms))
            return terms
    except Exception as e:
        logger.warning("HN_TERMS_FILE 로드 실패 (%s) — QUERY_TERMS 사용: %s", path, e)
    return list(QUERY_TERMS)

# 회당 사용할 검색어 수 — None 이면 전체 사용, int 면 random.sample(QUERY_TERMS, N)
# Algolia 무료 1 req/s 제한 + 1회 crawl 시간 단축 옵션
QUERY_SAMPLE_SIZE: Optional[int] = None

# 시간 윈도우 (초) — 라운드 3 에서 확장 (검색어 50+ 와 결합해 풍부한 글 확보)
# HN 의 Samsung 키워드 빈도가 낮아 7d/3d 로는 50개 검색어가 무용 → 90d/90d
STORY_WINDOW_SECONDS = 90 * 24 * 3600    # 90일
COMMENT_WINDOW_SECONDS = 90 * 24 * 3600  # 90일

# 응답량
STORY_HITS_PER_PAGE = 50
COMMENT_HITS_PER_PAGE = 100

# 후처리 상한 (DB 부하 방지) — 검색어 50+ 확장에 맞춰 상향
MAX_STORIES = 600          # 검색어 50+ × 50 ≈ 최대 2500, dedup 후 절단
MAX_COMMENTS = 1500        # 검색어 50+ × 100 ≈ 최대 5000, dedup 후 절단
MAX_COMMENT_TREE_PER_STORY = 20   # item 트리 보강 (인기 스토리 한정)
TOP_STORIES_FOR_TREE = 10  # 상위 스토리만 트리 보강


class HackerNewsCrawler(BaseCrawler):
    MIN_DELAY = 0.4
    MAX_DELAY = 0.8

    def __init__(self, platform_code: str = "hackernews", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    def _select_terms(self) -> List[str]:
        """이번 crawl 에서 사용할 검색어 목록.
        QUERY_SAMPLE_SIZE 가 None 이면 전체, 그렇지 않으면 random.sample 로 N 개.
        HN_TERMS_FILE 환경변수 지정 시 파일에서 풀 로드 (R12)."""
        pool = _load_query_terms()
        if QUERY_SAMPLE_SIZE is None or QUERY_SAMPLE_SIZE >= len(pool):
            return list(pool)
        n = max(1, QUERY_SAMPLE_SIZE)
        return random.sample(pool, n)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_story_ids: set = set()
        seen_comment_ids: set = set()
        now_i = int(time.time())
        story_cutoff = now_i - STORY_WINDOW_SECONDS
        comment_cutoff = now_i - COMMENT_WINDOW_SECONDS

        # 검색어 선택 (전체 또는 random sample)
        terms = self._select_terms()
        logger.info(
            "HN 검색어 %d개 사용 (전체 풀 %d개, sample_size=%s)",
            len(terms),
            len(QUERY_TERMS),
            QUERY_SAMPLE_SIZE,
        )

        async with self._make_httpx_client() as client:
            # ===========================================
            # 1) 검색어별 스토리 수집 (tags=story, 7일)
            # ===========================================
            stories: List[dict] = []
            for q in terms:
                try:
                    hits = await self._search(
                        client,
                        query=q,
                        tags="story",
                        hits_per_page=STORY_HITS_PER_PAGE,
                        numeric_filters=f"created_at_i>{story_cutoff}",
                    )
                    new_count = 0
                    for h in hits:
                        sid = h.get("objectID")
                        if not sid or sid in seen_story_ids:
                            continue
                        seen_story_ids.add(sid)
                        stories.append(h)
                        new_count += 1
                    logger.info(
                        f"  HN story '{q}': fetched={len(hits)} new={new_count}"
                    )
                except Exception as e:
                    logger.warning(f"  HN story '{q}' 실패: {e}")
                await self._random_delay()

            # 최신순 정렬 후 상한
            stories.sort(key=lambda h: h.get("created_at_i") or 0, reverse=True)
            target_stories = stories[:MAX_STORIES]

            # ===========================================
            # 2) 검색어별 댓글 수집 (tags=comment, 3일)
            # ===========================================
            comments_from_search: List[dict] = []
            for q in terms:
                try:
                    hits = await self._search(
                        client,
                        query=q,
                        tags="comment",
                        hits_per_page=COMMENT_HITS_PER_PAGE,
                        numeric_filters=f"created_at_i>{comment_cutoff}",
                    )
                    new_count = 0
                    for h in hits:
                        cid = h.get("objectID")
                        if not cid or cid in seen_comment_ids:
                            continue
                        seen_comment_ids.add(cid)
                        comments_from_search.append(h)
                        new_count += 1
                    logger.info(
                        f"  HN comment '{q}': fetched={len(hits)} new={new_count}"
                    )
                except Exception as e:
                    logger.warning(f"  HN comment '{q}' 실패: {e}")
                await self._random_delay()

            comments_from_search.sort(
                key=lambda h: h.get("created_at_i") or 0, reverse=True
            )
            target_comments = comments_from_search[:MAX_COMMENTS]

            # ===========================================
            # 3) RawVOC 변환
            # ===========================================
            for hit in target_stories:
                v = self._story_hit_to_voc(hit)
                if v:
                    raw_vocs.append(v)

            for hit in target_comments:
                v = self._comment_hit_to_voc(hit)
                if v:
                    raw_vocs.append(v)

            # ===========================================
            # 4) 상위 스토리 댓글 트리 보강 (인기 토픽 깊이)
            # ===========================================
            tree_total = 0
            for hit in target_stories[:TOP_STORIES_FOR_TREE]:
                sid = hit.get("objectID")
                if not sid:
                    continue
                await self._random_delay()
                try:
                    tree = await self._fetch_comment_tree(client, sid)
                    # 검색에서 이미 가져온 댓글은 제외
                    fresh = [
                        c for c in tree
                        if c.meta.get("hn_id")
                        and str(c.meta["hn_id"]) not in seen_comment_ids
                    ]
                    for c in fresh:
                        seen_comment_ids.add(str(c.meta["hn_id"]))
                    raw_vocs.extend(fresh)
                    tree_total += len(fresh)
                except Exception as e:
                    logger.warning(f"  HN tree (story={sid}) 실패: {e}")

        logger.info(
            "HN 수집 완료: stories=%d, comments(search)=%d, comments(tree)=%d, total=%d",
            len(target_stories),
            len(target_comments),
            tree_total,
            len(raw_vocs),
        )
        return raw_vocs

    # ---------------------------------------------------
    # Algolia 검색 (story / comment 공용)
    # ---------------------------------------------------
    async def _search(
        self,
        client: httpx.AsyncClient,
        query: str,
        tags: str,
        hits_per_page: int,
        numeric_filters: Optional[str] = None,
    ) -> List[dict]:
        params = {
            "query": query,
            "tags": tags,
            "hitsPerPage": hits_per_page,
        }
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        resp = await client.get(ALGOLIA_SEARCH, params=params)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("hits") or []

    # ---------------------------------------------------
    # 스토리 hit → RawVOC
    # ---------------------------------------------------
    def _story_hit_to_voc(self, hit: dict) -> Optional[RawVOC]:
        sid = hit.get("objectID")
        if not sid:
            return None
        title = (hit.get("title") or "").strip()
        story_text = (hit.get("story_text") or "").strip()
        if "<" in story_text and ">" in story_text:
            try:
                from bs4 import BeautifulSoup
                story_text = BeautifulSoup(story_text, "html.parser").get_text(" ", strip=True)
            except Exception:
                pass

        content = f"{title}\n{story_text}".strip() if story_text else title
        if not content:
            return None

        created_at_i = hit.get("created_at_i")
        published_at = (
            datetime.fromtimestamp(created_at_i, tz=timezone.utc)
            if created_at_i
            else None
        )

        article_url = hit.get("url") or f"{HN_ITEM_URL}{sid}"

        return RawVOC(
            external_id=hashlib.md5(f"hn_{sid}".encode()).hexdigest()[:16],
            content=content,
            source_url=article_url,
            author_name=hit.get("author") or None,
            published_at=published_at,
            likes_count=int(hit.get("points") or 0),
            comments_count=int(hit.get("num_comments") or 0),
            country_code="US",
            meta={"hn_id": sid, "hn_item_url": f"{HN_ITEM_URL}{sid}", "kind": "story"},
        )

    # ---------------------------------------------------
    # 댓글 검색 hit → RawVOC (tags=comment 결과)
    # ---------------------------------------------------
    def _comment_hit_to_voc(self, hit: dict) -> Optional[RawVOC]:
        cid = hit.get("objectID")
        if not cid:
            return None
        text_html = hit.get("comment_text") or ""
        if not text_html:
            return None

        if "<" in text_html and ">" in text_html:
            try:
                from bs4 import BeautifulSoup
                body = BeautifulSoup(text_html, "html.parser").get_text(" ", strip=True)
            except Exception:
                body = text_html
        else:
            body = text_html

        body = body.strip()
        if not body:
            return None

        created_at_i = hit.get("created_at_i")
        cdate = (
            datetime.fromtimestamp(created_at_i, tz=timezone.utc)
            if created_at_i
            else None
        )

        # Algolia comment hit 에는 story_id (부모 스토리) 가 들어있다
        story_id = hit.get("story_id") or hit.get("parent_id")

        return RawVOC(
            external_id=hashlib.md5(f"hn_{cid}".encode()).hexdigest()[:16],
            content=body,
            source_url=f"{HN_ITEM_URL}{cid}",
            author_name=hit.get("author") or None,
            published_at=cdate,
            likes_count=0,
            country_code="US",
            meta={
                "hn_id": cid,
                "parent_story": str(story_id) if story_id else None,
                "kind": "comment",
            },
        )

    # ---------------------------------------------------
    # 상위 스토리 댓글 트리 보강
    # ---------------------------------------------------
    async def _fetch_comment_tree(
        self, client: httpx.AsyncClient, story_id: str
    ) -> List[RawVOC]:
        url = f"{ALGOLIA_ITEM}/{story_id}"
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()

        out: List[RawVOC] = []
        self._flatten_comments(payload.get("children") or [], story_id, out)
        return out[:MAX_COMMENT_TREE_PER_STORY]

    def _flatten_comments(
        self, nodes: List[dict], story_id: str, out: List[RawVOC]
    ) -> None:
        for node in nodes:
            if len(out) >= MAX_COMMENT_TREE_PER_STORY:
                return
            if not isinstance(node, dict):
                continue
            if node.get("type") != "comment":
                self._flatten_comments(node.get("children") or [], story_id, out)
                continue

            cid = node.get("id")
            text_html = node.get("text") or ""
            if not cid or not text_html:
                self._flatten_comments(node.get("children") or [], story_id, out)
                continue

            try:
                from bs4 import BeautifulSoup
                body = BeautifulSoup(text_html, "html.parser").get_text(" ", strip=True)
            except Exception:
                body = text_html

            if not body.strip():
                self._flatten_comments(node.get("children") or [], story_id, out)
                continue

            created_at_i = node.get("created_at_i")
            cdate = (
                datetime.fromtimestamp(created_at_i, tz=timezone.utc)
                if created_at_i
                else None
            )

            out.append(RawVOC(
                external_id=hashlib.md5(f"hn_{cid}".encode()).hexdigest()[:16],
                content=body,
                source_url=f"{HN_ITEM_URL}{cid}",
                author_name=node.get("author") or None,
                published_at=cdate,
                likes_count=0,
                country_code="US",
                meta={
                    "hn_id": str(cid),
                    "parent_story": str(story_id),
                    "kind": "comment",
                },
            ))

            self._flatten_comments(node.get("children") or [], story_id, out)
