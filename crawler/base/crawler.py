"""
BaseCrawler — 모든 플랫폼 크롤러의 추상 기반 클래스
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import asyncio
import logging
import os
import random

import httpx

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")


# @lat: RawVOC — [[voc-pipeline#RawVOC vs StandardVOC]] 참조.
@dataclass
class RawVOC:
    """크롤링한 원시 VOC 데이터"""
    external_id: str
    content: str
    source_url: str
    author_name: Optional[str] = None
    published_at: Optional[datetime] = None
    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0
    country_code: Optional[str] = None
    # 플랫폼 고유 메타 (자유 형식)
    meta: dict = field(default_factory=dict)


# @lat: StandardVOC — [[voc-pipeline#RawVOC vs StandardVOC]] 참조.
@dataclass
class StandardVOC:
    """정규화된 표준 VOC 포맷"""
    external_id: str
    content_original: str
    source_url: str
    platform_code: str
    product_code: Optional[str] = None

    author_name: Optional[str] = None
    published_at: Optional[datetime] = None
    country_code: Optional[str] = None

    # NLP 처리 후 채워짐
    content_translated: Optional[str] = None
    language_detected: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    categories: Optional[List[str]] = None
    engagement_score: Optional[float] = None

    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0


# @lat: USER_AGENTS — [[crawler#Bot Detection Bypass]] 참조.
# User-Agent 풀 (봇 감지 우회). Harvest 3 트랙 A: 5→10개 확장 (Chrome/Firefox/Edge/Safari × Win/Mac/Linux)
USER_AGENTS = [
    # Chrome (Windows/Mac/Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox (Windows/Mac/Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Edge (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

# Harvest 3 트랙 A: Accept-Language 회전 (한국어 우세 + 영어 폴백 / 다양화)
ACCEPT_LANGUAGES = [
    "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "ko-KR,ko;q=0.9,en;q=0.8",
    "ko,en-US;q=0.9,en;q=0.8",
    "ko-KR,ko;q=0.9",
]


# @lat: BaseCrawler — [[crawler#BaseCrawler]] 참조.
class BaseCrawler(ABC):
    """모든 크롤러의 추상 기반 클래스"""

    # 플랫폼별 기본 딜레이 (초)
    MIN_DELAY: float = 1.0
    MAX_DELAY: float = 3.0

    def __init__(
        self,
        platform_code: str,
        product_code: Optional[str] = None,
        job_id: Optional[int] = None,
    ):
        self.platform_code = platform_code
        self.product_code = product_code
        self.job_id = job_id
        self.logger = logging.getLogger(f"crawler.{platform_code}")

    @abstractmethod
    async def crawl(self) -> List[RawVOC]:
        """플랫폼별 크롤링 구현 — 원시 VOC 리스트 반환"""
        ...

    def parse(self, raw_data) -> List[RawVOC]:
        """HTML/JSON → RawVOC 변환 (플랫폼별 오버라이드 가능)"""
        return []

    def normalize(self, raw: RawVOC) -> StandardVOC:
        """RawVOC → StandardVOC 표준화

        product_code 결정 우선순위:
          1. raw.meta["product_code"]  — 크롤러가 명시 (예: Amazon ASIN→제품 매핑)
          2. self.product_code         — 특정 제품 대상 크롤 job
          3. infer_product_code(본문)  — 커뮤니티 글에서 키워드로 추론
        """
        from base.product_match import infer_product_code

        product_code = (
            raw.meta.get("product_code")
            or self.product_code
            or infer_product_code(raw.content)
        )
        return StandardVOC(
            external_id=raw.external_id,
            content_original=raw.content,
            source_url=raw.source_url,
            platform_code=self.platform_code,
            product_code=product_code,
            author_name=raw.author_name,
            published_at=raw.published_at,
            country_code=raw.country_code,
            likes_count=raw.likes_count,
            comments_count=raw.comments_count,
            shares_count=raw.shares_count,
        )

    # @lat: save — [[voc-pipeline#중복 방지]] 참조.
    # R14 트랙 A: external_id 중복 + content_hash 본문 중복 2단 차단.
    @staticmethod
    def _content_hash(content: Optional[str]) -> Optional[str]:
        """sha256(content) hex 첫 16자 — 30자 미만이면 None (해시 없음)."""
        if not content or len(content) < 30:
            return None
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    async def save(self, vocs: List[StandardVOC]) -> int:
        """StandardVOC 리스트를 DB에 저장 (중복 체크 포함)"""
        if not vocs:
            return 0

        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import select, insert
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        saved = 0
        async with AsyncSessionLocal() as db:
            # 플랫폼 ID 조회
            from sqlalchemy import text
            platform_result = await db.execute(
                text("SELECT id FROM platforms WHERE code = :code"),
                {"code": self.platform_code}
            )
            platform_row = platform_result.one_or_none()
            platform_id = platform_row[0] if platform_row else None

            # 제품 코드 → product_id 캐시 (VOC마다 다른 제품일 수 있음)
            product_id_cache: dict = {}

            async def _resolve_product_id(code):
                if not code:
                    return None
                key = code.upper()
                if key in product_id_cache:
                    return product_id_cache[key]
                row = (await db.execute(
                    text("SELECT id FROM products WHERE code = :code"),
                    {"code": key},
                )).one_or_none()
                pid = row[0] if row else None
                product_id_cache[key] = pid
                return pid

            for voc in vocs:
                try:
                    product_id = await _resolve_product_id(voc.product_code)
                    chash = self._content_hash(voc.content_original)

                    # R14 트랙 A: 본문 해시 사전 차단 — 같은 platform_id 에 이미
                    # 동일 content_hash 가 존재하면 INSERT 자체를 건너뜀.
                    if chash and platform_id is not None:
                        dup = (await db.execute(text(
                            """
                            SELECT 1 FROM voc_records
                            WHERE platform_id = :pid AND content_hash = :h
                            LIMIT 1
                            """
                        ), {"pid": platform_id, "h": chash})).first()
                        if dup:
                            continue

                    stmt = text("""
                        INSERT INTO voc_records (
                            product_id, platform_id, external_id, source_url, author_name,
                            content_original, content_hash,
                            content_translated, language_detected, country_code,
                            sentiment_score, sentiment_label, categories,
                            likes_count, comments_count, shares_count, engagement_score,
                            published_at, collected_at
                        ) VALUES (
                            :product_id, :platform_id, :external_id, :source_url, :author_name,
                            :content_original, :content_hash,
                            :content_translated, :language_detected, :country_code,
                            :sentiment_score, :sentiment_label, :categories,
                            :likes_count, :comments_count, :shares_count, :engagement_score,
                            :published_at, NOW()
                        )
                        ON CONFLICT (platform_id, external_id) DO NOTHING
                    """)
                    result = await db.execute(stmt, {
                        "product_id": product_id,
                        "platform_id": platform_id,
                        "external_id": voc.external_id,
                        "source_url": voc.source_url,
                        "author_name": voc.author_name,
                        "content_original": voc.content_original,
                        "content_hash": chash,
                        "content_translated": voc.content_translated,
                        "language_detected": voc.language_detected,
                        "country_code": voc.country_code,
                        "sentiment_score": voc.sentiment_score,
                        "sentiment_label": voc.sentiment_label,
                        "categories": voc.categories,
                        "likes_count": voc.likes_count,
                        "comments_count": voc.comments_count,
                        "shares_count": voc.shares_count,
                        "engagement_score": voc.engagement_score,
                        "published_at": voc.published_at,
                    })
                    if result.rowcount:
                        saved += 1
                except Exception as e:
                    self.logger.warning(f"VOC 저장 실패 ({voc.external_id}): {e}")

            await db.commit()
        await engine.dispose()
        return saved

    async def run(self) -> dict:
        """전체 크롤링 파이프라인 실행"""
        await self._update_job_status("running")
        try:
            raw_vocs = await self.crawl()
            self.logger.info(f"  수집: {len(raw_vocs)}건")

            # NLP 처리
            from nlp.pipeline import process_voc_list
            standard_vocs = [self.normalize(r) for r in raw_vocs]
            processed_vocs = await process_voc_list(standard_vocs)

            # DB 저장
            saved = await self.save(processed_vocs)
            self.logger.info(f"  신규 저장: {saved}건")

            await self._update_job_status("done", items_collected=saved)
            return {"status": "done", "items_collected": saved}
        except Exception as e:
            self.logger.exception(f"크롤링 실패: {e}")
            await self._update_job_status("failed", error_message=str(e))
            raise

    async def _update_job_status(
        self, status: str, items_collected: int = 0, error_message: Optional[str] = None
    ):
        if not self.job_id:
            return
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
            from sqlalchemy import text

            engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
            AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with AsyncSessionLocal() as db:
                params: dict = {"status": status, "job_id": self.job_id}
                extra = ""
                if status == "running":
                    extra = ", started_at = NOW()"
                elif status in ("done", "failed"):
                    extra = ", finished_at = NOW()"
                    params["items_collected"] = items_collected
                    params["error_message"] = error_message

                stmt = text(f"""
                    UPDATE crawl_jobs
                    SET status = :status
                        {extra}
                        {', items_collected = :items_collected' if 'items_collected' in params else ''}
                        {', error_message = :error_message'   if 'error_message' in params else ''}
                    WHERE id = :job_id
                """)
                await db.execute(stmt, params)
                await db.commit()
            await engine.dispose()
        except Exception as e:
            self.logger.warning(f"job 상태 업데이트 실패: {e}")

    async def _random_delay(self):
        delay = random.uniform(self.MIN_DELAY, self.MAX_DELAY)
        await asyncio.sleep(delay)

    @staticmethod
    def _random_ua() -> str:
        return random.choice(USER_AGENTS)

    @staticmethod
    def _random_accept_language() -> str:
        return random.choice(ACCEPT_LANGUAGES)

    def _make_httpx_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": self._random_ua()},
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            follow_redirects=True,
        )

    # Harvest 3 트랙 A: 매 요청마다 UA + Accept-Language 회전.
    # 같은 client 인스턴스 위에서 호출자가 사용 — 봇 패턴 노출 차단용.
    async def fetch_with_rotated_ua(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        method: str = "GET",
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> Optional[httpx.Response]:
        """fetch() 와 동일하지만 매 호출마다 client.headers 의 UA/Accept-Language
        를 새로 픽한다. extra_headers 로 Referer 등 추가 가능.

        주의: 같은 client 인스턴스를 공유하는 동시 요청과는 race condition 가능.
        crawler 내부 순차 호출(현 clien/fmkorea 패턴)에 안전.
        """
        client.headers["User-Agent"] = self._random_ua()
        client.headers["Accept-Language"] = self._random_accept_language()
        if extra_headers:
            for k, v in extra_headers.items():
                client.headers[k] = v
        return await self.fetch(client, url, method=method, params=params, json_body=json_body)

    # ── R12 Track E1: 견고화 — 재시도/백오프/연속 실패 추적 ────────────────
    # 정책 (instructions):
    #   - 403/429/503 → 지수 백오프 재시도 (최대 RETRY_MAX 회)
    #   - 5회 연속 실패 → 사이트 비활성 추천 (단순 로그)
    #   - timeout 명확화 (httpx.Timeout 분리)
    RETRY_MAX = 3
    RETRY_BACKOFF_BASE = 1.5  # 초. n회차 delay = base * 2^(n-1) + jitter
    CONSECUTIVE_FAIL_THRESHOLD = 5

    async def fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        method: str = "GET",
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Optional[httpx.Response]:
        """공통 재시도 fetch — 403/429/503 시 지수 백오프 재시도.

        성공: httpx.Response 반환 (status_code 무관, 200대 외도 호출자가 판단).
        실패: None 반환 (RETRY_MAX 초과). 연속 실패는 _record_failure 가 추적.
        """
        # 연속 실패 카운터 lazy init (BaseCrawler 인스턴스 단위)
        if not hasattr(self, "_consec_fail_count"):
            self._consec_fail_count = 0

        for attempt in range(1, self.RETRY_MAX + 1):
            try:
                if method.upper() == "GET":
                    resp = await client.get(url, params=params)
                else:
                    resp = await client.request(
                        method.upper(), url, params=params, json=json_body,
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                self.logger.warning(
                    "fetch %s 통신 실패 (%d/%d): %s",
                    url, attempt, self.RETRY_MAX, e.__class__.__name__,
                )
                if attempt >= self.RETRY_MAX:
                    self._record_failure(url, f"network: {e.__class__.__name__}")
                    return None
                await self._backoff_sleep(attempt)
                continue

            # 재시도 대상 status — 일시 차단/스로틀
            if resp.status_code in (403, 429, 503):
                self.logger.info(
                    "fetch %s status=%d (%d/%d) — 백오프 재시도",
                    url, resp.status_code, attempt, self.RETRY_MAX,
                )
                if attempt >= self.RETRY_MAX:
                    self._record_failure(url, f"status={resp.status_code}")
                    return resp  # 마지막 응답은 호출자에게 돌려줌
                await self._backoff_sleep(attempt)
                continue

            # 정상 (200대 / 영구 오류 4xx 등) — 성공 처리
            self._record_success()
            return resp

        # 도달 불가
        return None

    async def _backoff_sleep(self, attempt: int) -> None:
        """지수 백오프 — base * 2^(attempt-1) + jitter[0, 0.5)."""
        delay = self.RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(delay)

    def _record_failure(self, url: str, reason: str) -> None:
        """연속 실패 카운터 증가 + 임계 초과 시 비활성 추천 로그."""
        if not hasattr(self, "_consec_fail_count"):
            self._consec_fail_count = 0
        self._consec_fail_count += 1
        self.logger.warning(
            "fetch 실패 누적 %d/%d (%s : %s)",
            self._consec_fail_count, self.CONSECUTIVE_FAIL_THRESHOLD, url, reason,
        )
        if self._consec_fail_count >= self.CONSECUTIVE_FAIL_THRESHOLD:
            self.logger.error(
                "[RECOMMEND_DEACTIVATE] platform=%s 연속 실패 %d회 (마지막: %s) — "
                "사이트 비활성 검토 권고",
                self.platform_code, self._consec_fail_count, reason,
            )

    def _record_success(self) -> None:
        """fetch 성공 시 연속 실패 카운터 리셋."""
        if getattr(self, "_consec_fail_count", 0) > 0:
            self.logger.info(
                "fetch 정상 복구 (직전 연속 실패 %d회 리셋)",
                self._consec_fail_count,
            )
        self._consec_fail_count = 0
