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
import time

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


# 예산이 끝나면 **네트워크를 타지 않는** transport.
#
# 가드를 크롤러마다 손으로 넣는 방식은 실패했다 — 96개 중 88개는 가드가 아예
# 없었고, 있는 8개도 바깥 루프에 두는 실수를 반복했다(appstore·telepolis·
# mobile_review). 사람이 루프 중첩을 눈으로 보고 최내곽을 고르는 일이라
# 반복해서 틀린다.
#
# 그래서 길목에서 막는다. 예산이 끝나면 요청을 실제로 보내지 않고 508 응답을
# 즉시 돌려준다. 호출자는 이미 "200 아니면 건너뛴다"로 되어 있으므로 남은
# 반복은 네트워크 없이 순식간에 소진되고, crawl() 은 **그때까지 모은 것을
# 정상 반환**한다. 예외를 던지면 수집분이 통째로 날아가므로 던지지 않는다.
#
# **200 + 빈 JSON 본문**을 쓴다. 에러 상태코드를 쓰면 안 된다 —
# 크롤러 60개가 resp.raise_for_status() 를 부르고, 거기서 예외가 터져
# crawl() 밖으로 새면 그때까지 모은 것을 통째로 잃는다. 막으려던 바로 그
# 실패다(실측: 508 을 쓰던 초판이 ppomppu 에서 HTTPStatusError 를 냈다).
# 본문 b"{}" 는 두 소비 경로를 모두 안전하게 만든다 —
#   · resp.text  → HTML 파서가 0건을 찾고 루프가 그냥 넘어간다
#   · resp.json() → 빈 dict 라 .get(...) 이 자연히 빈 리스트를 준다
# x-sf-budget 헤더로 이 응답임을 구분할 수 있다.
# 봇 차단벽 서명 — HTTP 200 으로 오지만 내용은 챌린지 페이지인 것들.
#
# 이게 없으면 차단된 소스가 "정상인데 조용함"으로 보인다. 실측 —
# androidcentral 은 35일간 실패 기록 0건·수집 0건이었고, 원인은 stile 챌린지
# 벽이었다. 0건은 "할 말이 없었다"와 "막혔다"를 구분하지 못한다.
_WALL_SIGNS = (
    "/.stile/challenge",            # stile
    "cf-browser-verification",      # Cloudflare
    "/cdn-cgi/challenge-platform",  # Cloudflare turnstile
    "just a moment",                # Cloudflare 대기 페이지 제목
    "enable javascript and cookies to continue",
    "px-captcha",                   # PerimeterX
    "/_incapsula_resource",         # Imperva
    "are you a robot",
)


def _looks_walled(resp: httpx.Response, url: str = "", body: bytes = b"") -> bool:
    """봇 차단벽 페이지인지 — URL·상태·본문 서명으로 판정.

    url/body 는 호출자가 넘긴다. transport 안의 응답은 아직 읽기 전이라
    resp.content 접근이 ResponseNotRead 로 터지기 때문이다 — 그걸 try 로
    삼키면 본문 판정이 조용히 죽는다(실측: 벽 0건으로 나왔다).
    """
    # **본문이 실하면 거절이 아니다.** 상태코드가 무엇이든 페이지를 받았다면
    # 파싱할 수 있다 — computerbase 는 429 와 함께 32KB 짜리 정상 포럼 HTML 을
    # 돌려주는데, 상태코드만 보고 차단으로 적었다(실측 오탐).
    if len(body) > 20_000:
        return False
    # **429 는 벽이 아니다.** 레이트리밋은 일시적이고 기존 백오프·재시도가
    # 다루는 정상 운영 상황이다. 이걸 blocked 로 올리면 건강한 소스에 오경보가
    # 난다 — computerbase 는 누적 3,445건·30일 574건을 수집하는 정상 소스인데
    # 짧은 시간에 반복 호출하자 429 를 내놨고, 그걸 차단으로 적었다.
    # blocked 는 "구조적으로 막혔다"는 뜻이어야 한다(35일 은폐된 androidcentral).
    # 429 는 _throttle_hits 로만 세어 로그에 남긴다.
    if resp.status_code == 429:
        return False
    if resp.status_code in (401, 403):
        return True
    if resp.status_code != 200:
        return False
    if any(sig in (url or str(resp.request.url)).lower() for sig in _WALL_SIGNS):
        return True
    # 챌린지 페이지는 대개 아주 작다.
    if not body:
        return False
    text = body.decode("utf-8", "ignore").lower()
    return any(sig in text for sig in _WALL_SIGNS)


class _BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, crawler: "BaseCrawler", inner: httpx.AsyncBaseTransport):
        self._crawler = crawler
        self._inner = inner
        self._blocked = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._crawler.budget_exceeded():
            self._blocked += 1
            if self._blocked == 1:
                self._crawler.logger.warning(
                    "수집 예산 소진 — 이후 요청은 네트워크 없이 종료한다"
                    " (여기까지 모은 것은 유지)")
            return httpx.Response(
                200, request=request, content=b"{}",
                headers={"x-sf-budget": "exceeded",
                         "content-type": "application/json"})

        # **시도를 먼저 센다.** 이 줄이 inner 호출 뒤에 있을 때는, 연결이
        # 통째로 거부되는 사이트(slrclub: 80/443 refused)가 예외로 빠져나가
        # 카운터를 0 으로 남겼다. 그러면 "접속이 안 되는 사이트"와 "요청을
        # 시도조차 안 한 크롤러"가 똑같이 '요청 0' 으로 보인다 — 구분이 안 되니
        # 아무도 손대지 않았다.
        self._crawler._wall_total += 1
        try:
            resp = await self._inner.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._crawler._conn_fails += 1
            self._crawler._conn_fail_reason = f"{type(e).__name__}: {e}"[:120]
            raise

        # 본문을 여기서 다 읽어 확정 응답으로 바꿔 돌려준다. 크롤러 중
        # 스트리밍(.stream/aiter_bytes)을 쓰는 곳은 없어서 의미 차이가 없고,
        # 이렇게 해야 챌린지 페이지 본문을 볼 수 있다.
        body = b""
        try:
            body = await resp.aread()
        except Exception:
            return resp
        finally:
            try:
                await resp.aclose()
            except Exception:
                pass

        try:
            if resp.status_code == 429:
                self._crawler._throttle_hits += 1
            elif _looks_walled(resp, url=str(request.url), body=body):
                self._crawler._wall_hits += 1
        except Exception:
            pass

        # aread() 가 이미 압축을 풀었다. 원래 헤더를 그대로 붙이면 httpx 가
        # 한 번 더 풀려다 DecodingError 를 낸다 — 그 두 헤더는 떼고 돌려준다.
        headers = [(k, v) for k, v in resp.headers.raw
                   if k.lower() not in (b"content-encoding", b"content-length")]
        return httpx.Response(resp.status_code, headers=headers,
                              content=body, request=request,
                              extensions=resp.extensions)

    async def aclose(self) -> None:
        await self._inner.aclose()


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
        self._started = time.monotonic()
        # 봇 차단벽 관측 — 0건이 "할 말 없음"인지 "막힘"인지 가른다
        self._wall_hits = 0
        self._wall_total = 0
        self._throttle_hits = 0     # 429 — 레이트리밋. 차단과 대응이 다르다
        self._conn_fails = 0        # 연결 거부/타임아웃 — 벽이 아니라 '문이 없음'
        self._conn_fail_reason = ""

    # ── 수집 시간 예산 ────────────────────────────────────────────────
    # run() 은 crawl() 전량 → NLP 전량 → save() 를 **마지막에 한 번만** 한다.
    # 그래서 celery soft time limit(600초)이 crawl() 도중에 터지면 긁은 것이
    # 통째로 버려진다. 실측(2026-09-09) — clien·dogdrip 이 하루 44회씩 전부
    # SoftTimeLimitExceeded 로 끝나 3일간 저장 0건이었고, 본문·댓글은 정상적으로
    # 긁고 있었는데 커밋 직전에 죽는 것이었다(워커 시간 16.7h/일 소모).
    #
    # 긴 루프를 도는 크롤러는 반복마다 budget_exceeded() 를 확인하고 break 해
    # **부분 결과라도 반환**해야 한다. 죽는 것보다 절반이 낫다.
    # 실측(2026-09-10, dogdrip) — crawl 452.9s/897건 · NLP 271.4s · save 1.0s
    # = 합계 725.3s 로 soft limit 600s 를 넘겼다. 시간만 보는 예산으로는 부족하다.
    # **NLP 비용이 수집량에 비례**하기 때문이다(건당 0.303s).
    # 그래서 시간과 건수를 함께 본다 —
    #   500건 상한이면 NLP 약 152s, 그 시점 crawl 이 약 253s → 합계 약 406s 로 여유가 있다.
    #   느린 소스는 건수가 안 차므로 330s 시간 상한이 먼저 걸린다(330 + NLP + save < 600).
    CRAWL_BUDGET_SEC: float = float(os.getenv("CRAWL_TIME_BUDGET_SEC", "330"))
    CRAWL_MAX_ITEMS: int = int(os.getenv("CRAWL_MAX_ITEMS", "500"))
    # NLP→저장 청크. 이 단위로 커밋하므로 타임아웃 시 잃는 것은 마지막 청크뿐이다.
    NLP_CHUNK: int = int(os.getenv("NLP_CHUNK", "150"))
    # 실행 전체 예산(crawl + NLP + save).
    # **마지막 청크가 경계를 넘긴다**는 것을 계산에 넣어야 한다. 예산을 확인한 뒤
    # 시작한 청크는 끝까지 돌기 때문이다 — 510s 예산에 청크 200건(최악 0.667s/건
    # = 133s)이면 643s 로 soft limit 600s 를 넘는다(실측 mobile_review 602.8s).
    # 430 + 150×0.667 = 530s 로 여유를 둔다.
    RUN_BUDGET_SEC: float = float(os.getenv("CRAWL_RUN_BUDGET_SEC", "430"))

    def budget_exceeded(self, collected: int = 0) -> bool:
        """시간 또는 수집량 상한 초과. 긴 루프는 반복마다 확인하고 break 하라."""
        if collected and collected >= self.CRAWL_MAX_ITEMS:
            return True
        return (time.monotonic() - self._started) >= self.CRAWL_BUDGET_SEC

    def run_budget_exceeded(self) -> bool:
        """crawl 이후 단계까지 포함한 전체 예산 초과."""
        return (time.monotonic() - self._started) >= self.RUN_BUDGET_SEC

    # 차단벽 판정 기준 — 요청 대다수가 벽이면 그 실행은 막힌 것으로 본다.
    WALL_RATIO = 0.5
    WALL_MIN_REQ = 3

    def report_blocked(self, reason: str) -> None:
        """크롤러가 **스스로** 차단을 선언한다.

        _BudgetTransport 는 httpx 요청만 본다. Playwright 로 봇 챌린지를 푸는
        크롤러(fmkorea 등)는 그 길목을 지나지 않아 차단이 감지되지 않는다 —
        챌린지에 막혀 0건을 반환해도 "할 말이 없었다"와 구분되지 않았다.
        그런 크롤러는 실패를 아는 지점에서 이걸 불러 사실을 남긴다.
        """
        self._wall_total = max(self._wall_total, self.WALL_MIN_REQ)
        self._wall_hits = self._wall_total          # 비율 조건을 확실히 넘긴다
        self._wall_reason = reason

    def _wall_summary(self) -> Optional[str]:
        """이번 실행이 차단벽에 막혔다고 볼 수 있으면 사유 문자열, 아니면 None.

        호출자(run)가 "수집 0건"일 때만 부른다 — 한 건이라도 긁혔으면
        벽이 좀 보여도 소스는 동작한 것이다(상세 페이지 일부만 403 등).
        """
        # 연결 자체가 안 되는 건 '벽'과 다른 고장이다 — 벽은 UA·쿠키·브라우저로
        # 넘볼 여지라도 있지만 이쪽은 포트가 닫혀 있거나 도메인이 사라진 것이다.
        # 먼저 보지 않으면 _wall_hits 가 0 이라 조용히 None 이 되어 묻힌다.
        if self._conn_fails and self._conn_fails >= self._wall_total * self.WALL_RATIO:
            return (f"연결 실패 — 요청 {self._wall_total}건 중 {self._conn_fails}건이 "
                    f"접속 불가 ({self._conn_fail_reason})")

        if self._wall_total < self.WALL_MIN_REQ or not self._wall_hits:
            return None
        ratio = self._wall_hits / self._wall_total
        if ratio < self.WALL_RATIO:
            return None
        if getattr(self, "_wall_reason", None):
            return self._wall_reason
        return (f"요청 {self._wall_total}건 중 {self._wall_hits}건이 "
                f"차단 응답 ({ratio:.0%})")

    def _translate_deadline(self) -> float:
        """번역 마감시각. 백필 모드면 이미 지난 값을 줘 번역을 건너뛴다."""
        if os.getenv("BACKFILL_MODE", "0") == "1":
            return time.monotonic() - 1
        return self._started + self.RUN_BUDGET_SEC

    def budget_left(self) -> float:
        return max(0.0, self.CRAWL_BUDGET_SEC - (time.monotonic() - self._started))

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
                        RETURNING id
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
                    inserted = result.first()
                    if inserted:
                        saved += 1
                        # 다대다 제품 링크 — 비교글("S26U vs Fold8")에서 두 제품 모두 보존.
                        # product_id 는 primary 하나만 담으므로 여기서 나머지를 링크한다.
                        await self._save_product_links(
                            db, inserted[0], voc.content_original,
                            product_id, _resolve_product_id,
                        )
                        # 구조화 결함(부품·증상·심각도) — 번역본 우선(렉시콘이 영어 중심)
                        await self._save_defects(
                            db, inserted[0],
                            voc.content_translated or voc.content_original,
                        )
                except Exception as e:
                    self.logger.warning(f"VOC 저장 실패 ({voc.external_id}): {e}")

            await db.commit()
        await engine.dispose()
        return saved

    @staticmethod
    async def _save_product_links(db, voc_id, content, primary_pid, resolve) -> None:
        """voc_product_links 채움 — 저장된 product_id 를 primary 로, 본문에서 추론된
        나머지 제품을 compared/mentioned 로 링크한다.

        product_id 는 1행 1제품이라 비교글에서 한쪽만 남는다. 이 링크가 있어야
        "Fold8 vs S26U" 글이 Fold8 신호로도 집계된다."""
        from sqlalchemy import text
        from base.product_match import infer_all_product_codes

        links: dict = {}
        if primary_pid is not None:
            links[primary_pid] = "primary"
        for code, role in infer_all_product_codes(content):
            pid = await resolve(code)
            if pid is None or pid in links:
                continue
            # 저장된 primary 가 이미 있으면 추론된 primary 는 mentioned 로 강등
            links[pid] = role if primary_pid is None else (
                "mentioned" if role == "primary" else role
            )
        for pid, role in links.items():
            await db.execute(text("""
                INSERT INTO voc_product_links (voc_id, product_id, role)
                VALUES (:v, :p, :r)
                ON CONFLICT (voc_id, product_id) DO NOTHING
            """), {"v": voc_id, "p": pid, "r": role})

    # 일반 기술 토론 커뮤니티 — 기기 앵커를 요구해야 SW/인프라 얘기가 결함으로 안 샌다
    # (실측: hackernews 결함 추출 정밀도 8%).
    ANCHOR_PLATFORMS = {"hackernews"}

    async def _save_defects(self, db, voc_id, body) -> None:
        """voc_defects 채움 — 본문에서 (부품·증상·심각도) 삼중항 추출.

        단어 카운트가 아니라 "힌지에 이물 유입(기능저하)" 수준으로 집계하기 위한 것."""
        from sqlalchemy import text
        from nlp.defect_extract import extract_defects
        from nlp.modality import classify as classify_modality

        defects = extract_defects(
            body, require_anchor=(self.platform_code in self.ANCHOR_PLATFORMS))
        if not defects:
            return
        # 양상(1인칭 고장 / 우려 / 질문 / 전언 / 리뷰) — 급등 판정은 firsthand 만 센다
        modality = classify_modality(body)["label"]
        for comp, symp, sev in defects:
            await db.execute(text("""
                INSERT INTO voc_defects (voc_id, component, symptom, severity, modality)
                VALUES (:v, :c, :s, :sev, :mod)
                ON CONFLICT (voc_id, component, symptom) DO NOTHING
            """), {"v": voc_id, "c": comp, "s": symp, "sev": sev, "mod": modality})

    async def run(self) -> dict:
        """전체 크롤링 파이프라인 실행"""
        await self._update_job_status("running")
        try:
            raw_vocs = await self.crawl()
            self.logger.info(f"  수집: {len(raw_vocs)}건")

            # NLP → 저장을 **청크 단위로 커밋**한다. 마지막에 한 번만 저장하면
            # soft time limit 이 도중에 터질 때 전량이 버려진다.
            # 건수 상한만으로는 못 막는다 — 루프 한 바퀴가 수백 건을 한꺼번에
            # 더해 상한을 훌쩍 넘기고(실측 appstore 980건 vs 상한 500), NLP 단가도
            # 소스마다 2.6배 차이난다(dogdrip 0.254s/건 vs mlbpark 0.667s/건,
            # 번역 API 레이트리밋 변동). 그래서 상한이 아니라 **부분 커밋**이
            # 정답이다. 타임아웃돼도 잃는 것은 마지막 청크 하나뿐이다.
            from nlp.pipeline import process_voc_list
            saved = 0
            done_n = 0
            for i in range(0, len(raw_vocs), self.NLP_CHUNK):
                part = raw_vocs[i:i + self.NLP_CHUNK]
                # 번역 마감 — 실행 예산까지. NLP 비용은 건당 추정이 안 된다
                # (실측 telepolis 150건 426초 = 2.84s/건, 가정치 0.667 의 4배).
                # 청크 사이에서만 확인하면 청크 하나가 통째로 넘긴다.
                #
                # **역사 백필은 번역을 통째로 건너뛴다**(BACKFILL_MODE=1).
                # 대량 수집이 번역 서비스를 두드리면 레이트리밋을 유발해
                # 실시간 파이프라인의 할당량까지 갉아먹는다(실측 — youtube 백필이
                # MyMemory 429 를 연달아 맞았다). 원문은 그대로 저장되고 12시간
                # 주기 translation_reprocess 가 나중에 메운다.
                processed = await process_voc_list(
                    [self.normalize(r) for r in part],
                    translate_deadline=self._translate_deadline())
                saved += await self.save(processed)
                done_n += len(part)
                if done_n < len(raw_vocs) and self.run_budget_exceeded():
                    self.logger.warning(
                        f"  실행 예산 초과 — {done_n}/{len(raw_vocs)}건 처리 후 중단"
                        f" (여기까지는 커밋됨)")
                    break
            self.logger.info(f"  신규 저장: {saved}건 (처리 {done_n}/{len(raw_vocs)})")

            # 0건은 "할 말이 없었다"와 "막혔다"를 구분하지 못한다. 차단벽을
            # 봤다면 done 이 아니라 blocked 로 남긴다 — 그러지 않으면
            # androidcentral 처럼 35일간 "정상인데 조용함"으로 묻힌다.
            # 한 건이라도 긁었다면 소스는 동작한 것이다 — 상세 페이지 일부가
            # 403 이어도 막힌 게 아니다. 아무것도 못 긁었을 때만 차단으로 본다.
            if not raw_vocs and self._throttle_hits:
                self.logger.warning(
                    f"  레이트리밋 관측 — 요청 {self._wall_total}건 중 "
                    f"{self._throttle_hits}건이 429. 일시적이므로 done 으로 둔다")
            wall_note = self._wall_summary() if not raw_vocs else None
            if wall_note:
                self.logger.warning(f"  차단벽 관측 — {wall_note}")
                await self._update_job_status(
                    "failed", items_collected=saved, items_fetched=len(raw_vocs),
                    error_message=f"blocked: {wall_note}")
                return {"status": "blocked", "items_collected": saved, "detail": wall_note}

            await self._update_job_status("done", items_collected=saved,
                                          items_fetched=len(raw_vocs))
            return {"status": "done", "items_collected": saved}
        except Exception as e:
            self.logger.exception(f"크롤링 실패: {e}")
            await self._update_job_status("failed", error_message=str(e))
            raise

    async def _update_job_status(
        self, status: str, items_collected: int = 0, error_message: Optional[str] = None,
        items_fetched: Optional[int] = None,
    ):
        """items_fetched 는 **긁은 건수**, items_collected 는 **신규 저장 건수**.

        둘을 구분해야 "못 긁었다"와 "긁었는데 전부 중복이다"를 가를 수 있다 —
        ifixit 는 700건을 긁고 신규 0건인데 헬스 체크가 고장으로 경보했다.
        """
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
                    if items_fetched is not None:
                        params["items_fetched"] = items_fetched

                stmt = text(f"""
                    UPDATE crawl_jobs
                    SET status = :status
                        {extra}
                        {', items_collected = :items_collected' if 'items_collected' in params else ''}
                        {', items_fetched = :items_fetched'     if 'items_fetched'  in params else ''}
                        {', error_message = :error_message'   if 'error_message' in params else ''}
                    WHERE id = :job_id
                """)
                await db.execute(stmt, params)
                await db.commit()
            await engine.dispose()
        except Exception as e:
            self.logger.warning(f"job 상태 업데이트 실패: {e}")

    async def _random_delay(self):
        # 예산이 끝난 뒤의 예의상 대기는 의미가 없다. 남은 반복을 빨리
        # 소진시켜야 crawl() 이 모은 것을 들고 제때 빠져나온다.
        if self.budget_exceeded():
            return
        delay = random.uniform(self.MIN_DELAY, self.MAX_DELAY)
        await asyncio.sleep(delay)

    @staticmethod
    def _random_ua() -> str:
        return random.choice(USER_AGENTS)

    @staticmethod
    def _random_accept_language() -> str:
        return random.choice(ACCEPT_LANGUAGES)

    def _budget_transport(self) -> httpx.AsyncBaseTransport:
        """자체 AsyncClient 를 만드는 크롤러가 transport= 로 끼워 넣는다."""
        return _BudgetTransport(self, httpx.AsyncHTTPTransport(retries=0))

    def _make_httpx_client(self, **kw) -> httpx.AsyncClient:
        """예산 가드가 걸린 httpx 클라이언트. 크롤러는 항상 이걸로 만들어야 한다."""
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        headers = {"User-Agent": self._random_ua()}
        headers.update(kw.pop("headers", None) or {})
        return httpx.AsyncClient(
            headers=headers,
            timeout=kw.pop("timeout", timeout),
            follow_redirects=kw.pop("follow_redirects", True),
            transport=_BudgetTransport(self, httpx.AsyncHTTPTransport(retries=0)),
            **kw,
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
