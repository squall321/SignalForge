"""알림 규칙 — voc_records 를 읽어 조건 충족 시 알림 페이로드 생성.

규칙:
  - sentiment_drop : 제품 24h 부정률이 직전 7일 평균 +30%p 이상 → warning
  - site_dead     : 플랫폼 24h 신규=0 & 직전 7일 평균 ≥ 100건/일 → critical
  - issue_spike   : 카테고리 24h 발생수가 직전 7일 일평균의 5배 이상 → warning
  - daily_summary : run_daily=True 일 때 24h 신규 요약 (매일 09 KST 트리거) → info

DB 접속:
  - asyncpg + SQLAlchemy async (프로젝트의 표준 드라이버 — 별도 psycopg2 불요)
  - 외부에서 보면 동기 함수 (asyncio.run 내부 처리)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


# ─── 규칙 임계값 (조정 가능) ────────────────────────────────────────────
SENTIMENT_DROP_THRESHOLD_PP = 30.0   # +30%p 이상 부정률 상승
SITE_DEAD_BASELINE_MIN = 100         # 직전 7일 일평균 100건 이상이면 활성
ISSUE_SPIKE_MULTIPLIER = 5.0         # 일평균 대비 5배
DAILY_SUMMARY_TOP_N = 5              # 요약에 포함할 상위 항목 수


# ─── DB 헬퍼 ───────────────────────────────────────────────────────────

def _async_db_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if raw:
        # sync URL 이 잘못 들어와도 asyncpg 로 정규화
        return (
            raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
               .replace("postgresql://", "postgresql+asyncpg://")
        )
    user = os.getenv("POSTGRES_USER", "signalforge")
    pwd  = os.getenv("POSTGRES_PASSWORD", "signalforge_pass")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5434")
    db   = os.getenv("POSTGRES_DB", "signalforge")
    return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"


# ─── 규칙 1: sentiment_drop ────────────────────────────────────────────

_SQL_SENTIMENT_DROP = text("""
    WITH last24 AS (
        SELECT
            product_id,
            COUNT(*)                                                     AS n,
            SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*),0) * 100                               AS neg_rate
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '24 hours'
          AND sentiment_label IS NOT NULL
          AND product_id IS NOT NULL
        GROUP BY product_id
        HAVING COUNT(*) >= 20
    ),
    prev7 AS (
        SELECT
            product_id,
            COUNT(*)                                                     AS n,
            SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*),0) * 100                               AS neg_rate
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '8 days'
          AND collected_at <  NOW() - INTERVAL '1 day'
          AND sentiment_label IS NOT NULL
          AND product_id IS NOT NULL
        GROUP BY product_id
        HAVING COUNT(*) >= 50
    )
    SELECT
        p.code, p.name_en,
        l.n                                                   AS n_24h,
        ROUND(l.neg_rate::numeric, 1)                         AS neg_24h,
        ROUND(pr.neg_rate::numeric, 1)                        AS neg_baseline,
        ROUND((l.neg_rate - pr.neg_rate)::numeric, 1)         AS delta_pp
    FROM last24 l
    JOIN prev7  pr USING (product_id)
    JOIN products p ON p.id = l.product_id
    WHERE (l.neg_rate - pr.neg_rate) >= :thr
    ORDER BY delta_pp DESC
""")


async def _check_sentiment_drop(session) -> list[dict[str, Any]]:
    rows = (await session.execute(
        _SQL_SENTIMENT_DROP, {"thr": SENTIMENT_DROP_THRESHOLD_PP}
    )).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "rule": "sentiment_drop",
            "level": "warning",
            "payload": {
                "title": f"[SignalForge] 부정률 급등: {r['code']}",
                "text": (
                    f"24h 부정률 {r['neg_24h']}% (기준 7일 평균 {r['neg_baseline']}%, "
                    f"+{r['delta_pp']}%p)"
                ),
                "fields": {
                    "Product":  r["name_en"] or r["code"],
                    "24h VOC":  r["n_24h"],
                    "Neg 24h":  f"{r['neg_24h']}%",
                    "Baseline": f"{r['neg_baseline']}%",
                    "Delta":    f"+{r['delta_pp']}%p",
                },
                "rule": "sentiment_drop",
            },
        })
    return out


# ─── 규칙 2: site_dead ─────────────────────────────────────────────────

_SQL_SITE_DEAD = text("""
    WITH last24 AS (
        SELECT platform_id, COUNT(*) AS n
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '24 hours'
          AND platform_id IS NOT NULL
        GROUP BY platform_id
    ),
    prev7 AS (
        SELECT platform_id, COUNT(*)::float / 7.0 AS daily_avg
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '8 days'
          AND collected_at <  NOW() - INTERVAL '1 day'
          AND platform_id IS NOT NULL
        GROUP BY platform_id
    )
    SELECT
        pl.code, pl.name,
        COALESCE(l.n, 0)                  AS n_24h,
        ROUND(pr.daily_avg::numeric, 1)   AS baseline
    FROM prev7 pr
    JOIN platforms pl   ON pl.id = pr.platform_id
    LEFT JOIN last24 l  ON l.platform_id = pr.platform_id
    WHERE pr.daily_avg >= :base_min
      AND COALESCE(l.n, 0) = 0
    ORDER BY pr.daily_avg DESC
""")


async def _check_site_dead(session) -> list[dict[str, Any]]:
    rows = (await session.execute(
        _SQL_SITE_DEAD, {"base_min": SITE_DEAD_BASELINE_MIN}
    )).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "rule": "site_dead",
            "level": "critical",
            "payload": {
                "title": f"[SignalForge] 사이트 무수집: {r['code']}",
                "text": (
                    f"24h 신규 0건 (직전 7일 일평균 {r['baseline']}건). "
                    f"크롤러 점검 필요."
                ),
                "fields": {
                    "Platform": r["name"] or r["code"],
                    "24h":      r["n_24h"],
                    "Baseline": f"{r['baseline']}/일",
                },
                "rule": "site_dead",
            },
        })
    return out


# ─── 규칙 3: issue_spike ───────────────────────────────────────────────

_SQL_ISSUE_SPIKE = text("""
    WITH last24 AS (
        SELECT unnest(categories) AS category, COUNT(*) AS n
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '24 hours'
          AND categories IS NOT NULL
        GROUP BY category
    ),
    prev7 AS (
        SELECT unnest(categories) AS category, COUNT(*)::float / 7.0 AS daily_avg
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '8 days'
          AND collected_at <  NOW() - INTERVAL '1 day'
          AND categories IS NOT NULL
        GROUP BY category
    )
    SELECT
        l.category,
        l.n                                                 AS n_24h,
        ROUND(pr.daily_avg::numeric, 1)                     AS baseline,
        ROUND((l.n / NULLIF(pr.daily_avg, 0))::numeric, 2)  AS mult
    FROM last24 l
    JOIN prev7  pr USING (category)
    WHERE pr.daily_avg >= 10
      AND l.n >= :mult * pr.daily_avg
    ORDER BY mult DESC
""")


async def _check_issue_spike(session) -> list[dict[str, Any]]:
    rows = (await session.execute(
        _SQL_ISSUE_SPIKE, {"mult": ISSUE_SPIKE_MULTIPLIER}
    )).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "rule": "issue_spike",
            "level": "warning",
            "payload": {
                "title": f"[SignalForge] 이슈 급증: {r['category']}",
                "text": (
                    f"24h {r['n_24h']}건 — 직전 7일 일평균 {r['baseline']}건 대비 "
                    f"{r['mult']}배"
                ),
                "fields": {
                    "Category":   r["category"],
                    "24h":        r["n_24h"],
                    "Baseline":   f"{r['baseline']}/일",
                    "Multiplier": f"x{r['mult']}",
                },
                "rule": "issue_spike",
            },
        })
    return out


# ─── 규칙 4: daily_summary ─────────────────────────────────────────────

_SQL_DAILY_TOTAL = text("""
    SELECT
        COUNT(*)                                                                  AS total,
        SUM(CASE WHEN sentiment_label='positive' THEN 1 ELSE 0 END)               AS pos,
        SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)               AS neg,
        SUM(CASE WHEN sentiment_label='neutral'  THEN 1 ELSE 0 END)               AS neu,
        COUNT(DISTINCT platform_id)                                               AS n_platforms
    FROM voc_records
    WHERE collected_at >= NOW() - INTERVAL '24 hours'
""")

_SQL_DAILY_TOP_PLATFORMS = text("""
    SELECT pl.code, COUNT(*) AS n
    FROM voc_records v JOIN platforms pl ON pl.id = v.platform_id
    WHERE v.collected_at >= NOW() - INTERVAL '24 hours'
    GROUP BY pl.code
    ORDER BY n DESC
    LIMIT :top_n
""")


async def _check_daily_summary(session) -> list[dict[str, Any]]:
    r = (await session.execute(_SQL_DAILY_TOTAL)).mappings().one()
    top_rows = (await session.execute(
        _SQL_DAILY_TOP_PLATFORMS, {"top_n": DAILY_SUMMARY_TOP_N}
    )).all()
    top_str = ", ".join(f"{code}({n})" for code, n in top_rows) or "n/a"

    total = int(r["total"] or 0)
    pos = int(r["pos"] or 0)
    neg = int(r["neg"] or 0)
    neu = int(r["neu"] or 0)
    n_plat = int(r["n_platforms"] or 0)
    pos_rate = round(pos / total * 100, 1) if total else 0.0
    neg_rate = round(neg / total * 100, 1) if total else 0.0

    return [{
        "rule": "daily_summary",
        "level": "info",
        "payload": {
            "title": "[SignalForge] 일일 수집 요약 (지난 24시간)",
            "text": (
                f"총 {total:,}건 / 플랫폼 {n_plat}개 / "
                f"긍정 {pos_rate}% · 부정 {neg_rate}%"
            ),
            "fields": {
                "Total":     f"{total:,}",
                "Platforms": n_plat,
                "Positive":  f"{pos} ({pos_rate}%)",
                "Negative":  f"{neg} ({neg_rate}%)",
                "Neutral":   neu,
                "Top":       top_str,
            },
            "rule": "daily_summary",
        },
    }]


# ─── 통합 진입점 ───────────────────────────────────────────────────────

async def _check_all_async(run_daily: bool) -> list[dict[str, Any]]:
    engine = create_async_engine(_async_db_url(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    out: list[dict[str, Any]] = []
    try:
        async with Session() as session:
            for fn in (_check_sentiment_drop, _check_site_dead, _check_issue_spike):
                try:
                    out.extend(await fn(session))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("규칙 %s 실패: %s", fn.__name__, exc)
            if run_daily:
                try:
                    out.extend(await _check_daily_summary(session))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("daily_summary 실패: %s", exc)
    finally:
        await engine.dispose()
    return out


def check_all_rules(run_daily: bool = False) -> list[dict[str, Any]]:
    """등록된 모든 규칙을 평가하고 알림 리스트 반환.

    동기 함수로 노출 (Celery task 에서 부담 없이 호출). 내부적으로 asyncio.run.

    Args:
        run_daily: True 면 daily_summary 규칙도 포함 (보통 09 KST 트리거)
    """
    return asyncio.run(_check_all_async(run_daily=run_daily))
