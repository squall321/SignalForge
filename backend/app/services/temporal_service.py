"""
T2 시계열 + LLM 분석 서비스 (P2-3).

데이터 소스:
- mv_voc_daily      : 일별 product/platform/country/lang 집계 (P1-3)
- category_daily    : 일별 product × category × country × lang 집계 (P2-1)
- timeline_events   : Galaxy 라인업 출시일 등 이벤트 마커

기능:
- temporal-series  : 단일 시계열(+ events + change-points)
- temporal-compare : 2 키 비교 시계열 + diff
- LLM narrative    : crawler.insight.llm_provider 사용, Redis 24h 캐시
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.temporal import (
    ChangePoint,
    CompareSeries,
    DiffPoint,
    LLMNarrativeResponse,
    NarrativeCitation,
    SeriesPoint,
    TemporalCompareResponse,
    TemporalSeriesResponse,
    TimelineEvent,
)


logger = logging.getLogger(__name__)


# crawler 패키지 import (llm_provider 재사용)
# SignalForge/crawler 디렉토리를 sys.path 에 추가.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_CRAWLER_ROOT = os.path.join(_REPO_ROOT, "crawler")
if _CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, _CRAWLER_ROOT)


# ── change-point detection ─────────────────────────────────────────
def detect_changepoints(
    series: List[Dict[str, Any]],
    window: int = 3,
    threshold_k: float = 2.0,
) -> List[ChangePoint]:
    """단순 sliding-window mean-diff 알고리즘.

    각 시점 i 에 대해 [i-window, i) 평균과 [i, i+window) 평균을 비교,
    절대 차이 > k * std(before+after 묶음, 윈도우 로컬) 인 경우 change-point.

    윈도우 로컬 std 를 쓰는 이유:
    - 전체 std 는 변화점 자체에 의해 부풀려져 thresh 가 과도해짐.
    - 윈도우별 로컬 변동성 대비 mean 차이가 큰 지점을 검출하는 것이 표준 접근.

    metrics: 'count' / 'sent_avg' 두 시리즈 모두 평가.
    """
    points: List[ChangePoint] = []
    n = len(series)
    if n < window * 2:
        return points

    for metric in ("count", "sent_avg"):
        values = [float(p[metric]) for p in series]
        # 전체 변동성도 floor 로 사용 (모두 동일하면 skip)
        try:
            std_all = statistics.pstdev(values) or 0.0
        except statistics.StatisticsError:
            std_all = 0.0
        if std_all == 0:
            continue

        for i in range(window, n - window):
            before = values[i - window : i]
            after = values[i : i + window]
            mean_before = statistics.mean(before)
            mean_after = statistics.mean(after)
            delta = mean_after - mean_before
            # 로컬 std: before/after 각각 — 두 구간이 모두 안정적일수록 작아져 변화점 감지가 잘됨.
            # before+after 묶음 std 는 변화 자체에 부풀려져 threshold 가 과도해진다.
            std_b = statistics.pstdev(before) if len(before) > 1 else 0.0
            std_a = statistics.pstdev(after) if len(after) > 1 else 0.0
            local_std = max(std_b, std_a)
            # 임계: k * max(local_std, 전체 std * 0.05) — 잡음 floor 보장
            threshold = threshold_k * max(local_std, std_all * 0.05)
            if abs(delta) > threshold:
                points.append(
                    ChangePoint(
                        date=series[i]["date"],
                        metric=metric,  # type: ignore[arg-type]
                        magnitude=round(abs(delta), 4),
                        direction="up" if delta > 0 else "down",
                    )
                )
    return points


# ── helper ─────────────────────────────────────────────────────────
def _parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def _bucket_trunc(bucket: str) -> str:
    """SQL date_trunc unit for bucket. day|week|month."""
    if bucket not in {"day", "week", "month"}:
        raise ValueError(f"unknown bucket: {bucket}")
    return bucket


# ── service ────────────────────────────────────────────────────────
class TemporalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ----------------------------------------------------------------
    # 1) temporal-series
    # ----------------------------------------------------------------
    async def get_series(
        self,
        product: Optional[str],
        categories: Optional[List[str]],
        from_date: str,
        to_date: str,
        bucket: str = "day",
        metric: str = "both",
        lang: Optional[str] = None,
        include_events: bool = True,
        include_changepoints: bool = True,
    ) -> TemporalSeriesResponse:
        d_from = _parse_date(from_date)
        d_to = _parse_date(to_date)
        if d_from > d_to:
            raise ValueError("from_date > to_date")
        trunc = _bucket_trunc(bucket)

        params: Dict[str, Any] = {
            "from_date": d_from,
            "to_date": d_to,
        }

        # category_daily 사용 (categories 필터가 있을 때) 또는 mv_voc_daily (없을 때)
        if categories:
            params["categories"] = [c.lower() for c in categories]
            # category_daily: product_id, category, country_code, language_detected
            where_parts = [
                "day >= :from_date",
                "day <= :to_date",
                "lower(category) = ANY(:categories)",
            ]
            join = ""
            if product:
                params["product"] = product.upper()
                join = "JOIN products p ON p.id = cd.product_id"
                where_parts.append("p.code = :product")
            if lang:
                params["lang"] = lang
                where_parts.append("cd.language_detected = :lang")
            where_sql = " AND ".join(where_parts)
            sql = f"""
                SELECT
                    date_trunc('{trunc}', cd.day)::date AS d,
                    SUM(cd.n)                            AS cnt,
                    CASE WHEN SUM(cd.n) > 0 THEN
                        ROUND( (SUM(cd.sent_avg * cd.n) / SUM(cd.n))::numeric, 4)
                    ELSE 0 END                           AS sent_avg,
                    SUM(cd.neg_cnt)                      AS neg_cnt,
                    SUM(cd.pos_cnt)                      AS pos_cnt
                FROM category_daily cd
                {join}
                WHERE {where_sql}
                GROUP BY d
                ORDER BY d
            """
        else:
            # mv_voc_daily 사용
            where_parts = ["day >= :from_date", "day <= :to_date"]
            join = ""
            if product:
                params["product"] = product.upper()
                join = "JOIN products p ON p.id = mv.product_id"
                where_parts.append("p.code = :product")
            if lang:
                params["lang"] = lang
                where_parts.append("mv.language_detected = :lang")
            where_sql = " AND ".join(where_parts)
            sql = f"""
                SELECT
                    date_trunc('{trunc}', mv.day)::date AS d,
                    SUM(mv.n)                            AS cnt,
                    CASE WHEN SUM(mv.n) > 0 THEN
                        ROUND( (SUM(mv.sent_avg * mv.n) / SUM(mv.n))::numeric, 4)
                    ELSE 0 END                           AS sent_avg,
                    SUM(mv.neg_cnt)                      AS neg_cnt,
                    SUM(mv.pos_cnt)                      AS pos_cnt
                FROM mv_voc_daily mv
                {join}
                WHERE {where_sql}
                GROUP BY d
                ORDER BY d
            """

        rows = (await self.db.execute(text(sql), params)).all()

        series: List[SeriesPoint] = []
        series_dicts: List[Dict[str, Any]] = []
        for r in rows:
            cnt = int(r.cnt or 0)
            neg = int(r.neg_cnt or 0)
            pos = int(r.pos_cnt or 0)
            neg_rate = round((neg / cnt) * 100, 2) if cnt else 0.0
            pos_rate = round((pos / cnt) * 100, 2) if cnt else 0.0
            sent = float(r.sent_avg or 0)
            sp = SeriesPoint(
                date=str(r.d),
                count=cnt,
                sent_avg=sent,
                neg_rate=neg_rate,
                pos_rate=pos_rate,
            )
            # metric 필터 — 응답 형태는 유지, count/sent_avg 가 의미 없으면 0 으로 마스킹
            if metric == "count":
                sp = sp.model_copy(update={"sent_avg": 0.0})
            elif metric == "sent_avg":
                sp = sp.model_copy(update={"count": 0})
            series.append(sp)
            series_dicts.append(
                {"date": str(r.d), "count": cnt, "sent_avg": sent}
            )

        # events
        events: List[TimelineEvent] = []
        if include_events:
            ev_params: Dict[str, Any] = {
                "from_date": d_from,
                "to_date": d_to,
            }
            ev_where = ["event_date >= :from_date", "event_date <= :to_date"]
            if product:
                ev_params["product"] = product.upper()
                # product_code 일치 또는 NULL(전체 이벤트) 둘 다 포함
                ev_where.append("(product_code = :product OR product_code IS NULL)")
            ev_sql = f"""
                SELECT event_date, event_type, title, product_code, source_url
                FROM timeline_events
                WHERE {' AND '.join(ev_where)}
                ORDER BY event_date
            """
            ev_rows = (await self.db.execute(text(ev_sql), ev_params)).all()
            events = [
                TimelineEvent(
                    date=str(r.event_date),
                    type=r.event_type,
                    title=r.title,
                    product_code=r.product_code,
                    source_url=r.source_url,
                )
                for r in ev_rows
            ]

        # change-points
        changepoints: List[ChangePoint] = []
        if include_changepoints and series_dicts:
            changepoints = detect_changepoints(series_dicts)

        return TemporalSeriesResponse(
            series=series,
            events=events,
            changepoints=changepoints,
            meta={
                "product": product,
                "categories": categories or [],
                "from_date": from_date,
                "to_date": to_date,
                "bucket": bucket,
                "metric": metric,
                "lang": lang,
                "source": "category_daily" if categories else "mv_voc_daily",
            },
        )

    # ----------------------------------------------------------------
    # 2) temporal-compare
    # ----------------------------------------------------------------
    async def compare(
        self,
        mode: str,
        keys: List[str],
        from_date: str,
        to_date: str,
        bucket: str = "day",
    ) -> TemporalCompareResponse:
        if mode not in {"products", "periods", "categories"}:
            raise ValueError(f"unknown mode: {mode}")
        if not keys or len(keys) < 2:
            raise ValueError("keys must contain >=2 entries")

        key_a, key_b = keys[0], keys[1]

        # 시리즈 두 개 가져오기
        async def _one(key: str) -> List[SeriesPoint]:
            if mode == "products":
                resp = await self.get_series(
                    product=key,
                    categories=None,
                    from_date=from_date,
                    to_date=to_date,
                    bucket=bucket,
                    metric="both",
                    include_events=False,
                    include_changepoints=False,
                )
                return resp.series
            elif mode == "categories":
                resp = await self.get_series(
                    product=None,
                    categories=[key],
                    from_date=from_date,
                    to_date=to_date,
                    bucket=bucket,
                    metric="both",
                    include_events=False,
                    include_changepoints=False,
                )
                return resp.series
            else:  # periods
                # key 형식: "YYYY-MM-DD..YYYY-MM-DD"
                if ".." not in key:
                    raise ValueError(
                        f"periods mode key must be 'from..to': {key}"
                    )
                p_from, p_to = key.split("..", 1)
                resp = await self.get_series(
                    product=None,
                    categories=None,
                    from_date=p_from,
                    to_date=p_to,
                    bucket=bucket,
                    metric="both",
                    include_events=False,
                    include_changepoints=False,
                )
                return resp.series

        a_pts = await _one(key_a)
        b_pts = await _one(key_b)

        # diff — date 기준 정렬해 inner-join 비슷하게 매칭
        b_by_date = {p.date: p for p in b_pts}
        diff: List[DiffPoint] = []
        for pa in a_pts:
            pb = b_by_date.get(pa.date)
            if pb is None:
                continue
            diff.append(
                DiffPoint(
                    date=pa.date,
                    delta_count=pa.count - pb.count,
                    delta_sent=round(pa.sent_avg - pb.sent_avg, 4),
                )
            )

        return TemporalCompareResponse(
            mode=mode,  # type: ignore[arg-type]
            a=CompareSeries(key=key_a, points=a_pts),
            b=CompareSeries(key=key_b, points=b_pts),
            diff=diff,
        )

    # ----------------------------------------------------------------
    # 3) llm-narrative
    # ----------------------------------------------------------------
    # 캐시 통계 — 프로세스 수명 동안 누적, /metrics 등에서 노출 가능.
    _cache_stats: Dict[str, int] = {"hit": 0, "miss": 0}

    async def llm_narrative(
        self,
        series_payload: Dict[str, Any],
        lang: str = "ko",
    ) -> LLMNarrativeResponse:
        # llm_provider 모듈 — PROMPT_VERSION 을 캐시 키에 포함시켜 prompt 갱신 시 자동 무효화.
        try:
            from insight.llm_provider import (  # type: ignore
                get_provider,
                PROMPT_VERSION,
            )
        except ImportError as e:
            logger.error("llm_provider import 실패: %s", e)
            return LLMNarrativeResponse(
                summary="(LLM provider 미설치)",
                citations=[],
                cached=False,
                provider=None,
            )

        # 캐시 키 = sha1(payload + prompt_version + lang)
        norm = json.dumps(series_payload, sort_keys=True, default=str).encode("utf-8")
        digest_src = norm + PROMPT_VERSION.encode("utf-8") + lang.encode("utf-8")
        digest = hashlib.sha1(digest_src).hexdigest()
        cache_key = f"p2:llm-narr:{PROMPT_VERSION}:{lang}:{digest}"

        # Redis 캐시 조회 (inline best-effort)
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    payload = json.loads(cached)
                    self._cache_stats["hit"] = self._cache_stats.get("hit", 0) + 1
                    return LLMNarrativeResponse(
                        summary=payload["summary"],
                        citations=[
                            NarrativeCitation(**c)
                            for c in payload.get("citations", [])
                        ],
                        cached=True,
                        provider=payload.get("provider"),
                    )
            except Exception as e:
                logger.warning("redis get failed: %s", e)

        self._cache_stats["miss"] = self._cache_stats.get("miss", 0) + 1

        prov = get_provider()
        if prov is None:
            return LLMNarrativeResponse(
                summary="(LLM key 미설정)",
                citations=[],
                cached=False,
                provider=None,
            )

        # grounding: payload → markdown 표 → LLM
        meta = series_payload.get("meta") or {}
        schema_desc = (
            "SignalForge 시계열 분석 결과. meta(필터 조건), 요약 통계, 시계열, "
            "이벤트, 변곡점 표를 순서대로 제시합니다. "
            f"기간 {meta.get('from_date', '?')} ~ {meta.get('to_date', '?')}, "
            f"제품 {meta.get('product') or '전체'}."
        )
        instructions = (
            "위 표를 근거로 한국어 3-5 문단의 자연어 narrative 를 작성하세요.\n"
            "1) 첫 문단: 핵심 헤드라인 1-2문장 — 가장 두드러진 변화/숫자.\n"
            "2) 이어서 (a) 수량 추세 (peak/trough/평균), (b) 감성 변화, "
            "(c) 변곡점이 있다면 그 의미, (d) 이벤트와의 시간적 연관성, "
            "(e) 다음 모니터링 포인트, 순으로 다룹니다.\n"
            "3) 모든 수치는 표에 적힌 그대로 인용. 합계·비율을 새로 계산할 때도 표의 원본 수치만 사용.\n"
            "4) 표에 없는 사실은 추측 금지 — '데이터 없음' 으로 명시.\n"
            "5) 중국어 한자 / 일본어 가나 / 약어 아닌 영어 토큰 절대 금지."
        )

        summary_raw: Optional[str]
        try:
            summary_raw = prov.summarize_json(
                series_payload, schema_desc, instructions
            )
        except Exception as e:
            logger.warning("summarize_json 실패 → summarize 폴백: %s", e)
            summary_raw = prov.summarize(
                self._build_prompt(series_payload, lang)
            )

        # grounding 검증
        try:
            from insight.grounding import validate_response  # type: ignore
        except Exception:
            validate_response = None  # type: ignore

        score: Optional[float] = None
        if summary_raw and validate_response is not None:
            score = validate_response(summary_raw, series_payload)
            # 점수가 너무 낮으면 1회 재요청 (수치 인용 강조)
            if score < 0.5:
                logger.warning(
                    "grounding score 낮음(%.2f) — 강화된 지시로 재요청", score
                )
                try:
                    stronger = (
                        instructions
                        + "\n\n[CRITICAL] 직전 응답에서 표의 수치 인용이 부족했습니다. "
                        "이번에는 표의 peak/trough/total/변곡점 |Δ| 같은 핵심 수치를 "
                        "반드시 본문에 그대로 인용하세요."
                    )
                    retry = prov.summarize_json(
                        series_payload, schema_desc, stronger
                    )
                    if retry:
                        retry_score = validate_response(retry, series_payload)
                        if retry_score > score:
                            summary_raw = retry
                            score = retry_score
                except Exception as e:
                    logger.warning("재요청 실패: %s", e)

        summary = summary_raw or "(LLM 응답 없음)"
        if score is not None:
            summary = (
                f"{summary.rstrip()}\n\n---\n"
                f"_LLM grounding score: {score:.2f} (provider: "
                f"{getattr(prov, 'name', '?')}, prompt: {PROMPT_VERSION})_"
            )

        # citations: payload 안의 events 그대로 인용
        citations: List[NarrativeCitation] = []
        for ev in series_payload.get("events", [])[:10]:
            citations.append(
                NarrativeCitation(
                    event_date=str(ev.get("date") or ev.get("event_date") or ""),
                    source_url=ev.get("source_url"),
                    title=ev.get("title"),
                )
            )

        resp = LLMNarrativeResponse(
            summary=summary,
            citations=citations,
            cached=False,
            provider=getattr(prov, "name", None),
        )

        # 캐시 저장 (24h)
        if redis_client is not None:
            try:
                redis_client.setex(
                    cache_key,
                    24 * 3600,
                    json.dumps(
                        {
                            "summary": resp.summary,
                            "citations": [c.model_dump() for c in resp.citations],
                            "provider": resp.provider,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                logger.warning("redis set failed: %s", e)

        return resp

    # ----------------------------------------------------------------
    # internal helpers
    # ----------------------------------------------------------------
    @staticmethod
    def _build_prompt(payload: Dict[str, Any], lang: str) -> str:
        """series_payload → LLM 프롬프트 (한국어 분석 요청)."""
        series = payload.get("series", [])
        events = payload.get("events", [])
        changepoints = payload.get("changepoints", [])
        meta = payload.get("meta", {})

        # 요약 통계
        if series:
            counts = [int(p.get("count", 0)) for p in series]
            sents = [float(p.get("sent_avg", 0)) for p in series]
            total = sum(counts)
            avg_sent = sum(sents) / len(sents) if sents else 0
            peak = max(series, key=lambda p: int(p.get("count", 0)))
        else:
            total = 0
            avg_sent = 0
            peak = None

        lines: List[str] = []
        lines.append(
            "다음은 SignalForge 시계열 분석 결과입니다. 한국어로 3-5 문단의 "
            "자연어 narrative 를 작성해주세요. 각 문단은 수치 근거를 동반해야 합니다."
        )
        lines.append("")
        lines.append("[메타]")
        for k, v in meta.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("[요약 통계]")
        lines.append(f"- 총 VOC 수: {total}")
        lines.append(f"- 평균 sentiment: {avg_sent:.3f}")
        if peak:
            lines.append(
                f"- 최다 발생일: {peak['date']} ({peak['count']}건)"
            )
        lines.append("")
        if series[:30]:
            lines.append("[시계열 (최대 30 포인트)]")
            for p in series[:30]:
                lines.append(
                    f"- {p['date']}: cnt={p['count']} sent={p['sent_avg']:.3f} "
                    f"neg={p.get('neg_rate', 0)}% pos={p.get('pos_rate', 0)}%"
                )
        lines.append("")
        if events:
            lines.append("[이벤트]")
            for ev in events[:10]:
                lines.append(
                    f"- {ev.get('date')}: [{ev.get('type')}] {ev.get('title')}"
                )
            lines.append("")
        if changepoints:
            lines.append("[change-points]")
            for cp in changepoints[:10]:
                lines.append(
                    f"- {cp.get('date')}: {cp.get('metric')} "
                    f"{cp.get('direction')} (|Δ|={cp.get('magnitude')})"
                )
            lines.append("")
        lines.append(
            "출력 형식: 3-5 개 단락. 중국어 한자/일본어 가나 절대 금지. "
            "절대 100% 한국어로 작성. 제품명/사이트명은 영문 허용."
        )
        return "\n".join(lines)

    _redis_cached: Any = None

    @classmethod
    def _get_redis(cls):
        """레이지 redis 클라이언트. 실패 시 None."""
        if cls._redis_cached is not None:
            return cls._redis_cached
        try:
            import redis  # type: ignore

            url = os.getenv("REDIS_URL", "").strip()
            if not url:
                host = os.getenv("REDIS_HOST", "127.0.0.1")
                port = int(os.getenv("REDIS_PORT", "6379"))
                pwd = os.getenv("REDIS_PASSWORD", "") or None
                client = redis.Redis(
                    host=host, port=port, password=pwd, decode_responses=True,
                    socket_timeout=2, socket_connect_timeout=2,
                )
            else:
                client = redis.Redis.from_url(url, decode_responses=True,
                                              socket_timeout=2,
                                              socket_connect_timeout=2)
            client.ping()
            cls._redis_cached = client
            return client
        except Exception as e:
            logger.warning("redis 비활성: %s", e)
            cls._redis_cached = None
            return None


__all__ = ["TemporalService", "detect_changepoints"]
