"""
Daily Insight — 어제(혹은 지정 일자) VOC 데이터를 LLM 으로 요약.

흐름:
    1. PostgreSQL 에서 일자 별 핵심 메트릭 집계 (제품 별 / 카테고리 별 /
       플랫폼 별 / 감성 별 + TOP 부정/긍정 샘플 문장).
    2. 한국어 프롬프트로 직렬화 → LLMProvider.summarize().
    3. reports/insight_YYYY-MM-DD.md 저장 + stdout 출력.

키가 둘 다 없으면:
    - LLM 호출은 skip.
    - "사람이 읽을 수 있는 raw 요약" 만으로도 의미 있는 .md 가 생성됨.

CLI:
    python -m insight.daily_insight             # 어제 (UTC) 자동
    python -m insight.daily_insight 2026-05-31  # 명시 일자
    python -m insight.daily_insight --days-back 2

환경변수:
    DATABASE_URL  (없으면 host/port/user/pass/db 변수에서 조합)
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    REPORT_DIR    (기본: <repo>/reports)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# crawler/ 를 sys.path 에 보장 (단독 실행 시)
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg

from insight.llm_provider import PROMPT_VERSION, get_provider  # noqa: E402

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# 설정 / 상수
# ────────────────────────────────────────────────────────────────────────────
REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

SAMPLE_LIMIT_PER_BUCKET = 6  # TOP 부정/긍정 별 샘플 문장 수
TOP_PRODUCT_LIMIT = 10
TOP_PLATFORM_LIMIT = 10
TOP_CATEGORY_LIMIT = 12  # 카테고리 12개 전체


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # asyncpg.connect() 는 'postgresql://' 또는 'postgres://' 만 받음.
        # SQLAlchemy 용 'postgresql+asyncpg://' DSN 을 그대로 받으면 에러 — 정규화.
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres+asyncpg://"):
            url = "postgres://" + url[len("postgres+asyncpg://"):]
        return url
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5434")
    user = os.getenv("POSTGRES_USER", "signalforge")
    pwd = os.getenv("POSTGRES_PASSWORD", "signalforge_pass")
    db = os.getenv("POSTGRES_DB", "signalforge")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


# ────────────────────────────────────────────────────────────────────────────
# 데이터 모델
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class DailyMetrics:
    target_date: date
    total: int = 0
    by_sentiment: Dict[str, int] = field(default_factory=dict)
    by_category: List[Dict[str, Any]] = field(default_factory=list)
    by_category_neg: List[Dict[str, Any]] = field(default_factory=list)
    by_product: List[Dict[str, Any]] = field(default_factory=list)
    by_platform: List[Dict[str, Any]] = field(default_factory=list)
    by_country: List[Dict[str, Any]] = field(default_factory=list)
    new_products_today: List[Dict[str, Any]] = field(default_factory=list)
    top_negative: List[Dict[str, Any]] = field(default_factory=list)
    top_positive: List[Dict[str, Any]] = field(default_factory=list)
    sentiment_score_avg: Optional[float] = None
    avg_engagement: Optional[float] = None


# ────────────────────────────────────────────────────────────────────────────
# 데이터 추출
# ────────────────────────────────────────────────────────────────────────────
async def collect_metrics(conn: asyncpg.Connection, target: date) -> DailyMetrics:
    m = DailyMetrics(target_date=target)

    # 총량 + 감성 합산 + 평균
    row = await conn.fetchrow(
        """
        SELECT count(*) AS total,
               avg(sentiment_score) AS sscore,
               avg(engagement_score) AS escore
          FROM voc_records
         WHERE collected_at::date = $1
        """,
        target,
    )
    m.total = int(row["total"]) if row else 0
    m.sentiment_score_avg = float(row["sscore"]) if row and row["sscore"] is not None else None
    m.avg_engagement = float(row["escore"]) if row and row["escore"] is not None else None

    if m.total == 0:
        return m

    # 감성 분포
    rows = await conn.fetch(
        """
        SELECT coalesce(sentiment_label, 'unknown') AS label, count(*) AS n
          FROM voc_records
         WHERE collected_at::date = $1
         GROUP BY 1
        """,
        target,
    )
    m.by_sentiment = {r["label"]: int(r["n"]) for r in rows}

    # 카테고리 (전체)
    rows = await conn.fetch(
        """
        SELECT cat AS code, c.name_ko AS name_ko, count(*) AS n
          FROM (
            SELECT unnest(categories) AS cat
              FROM voc_records
             WHERE collected_at::date = $1
          ) t
          LEFT JOIN voc_categories c ON c.code = t.cat
         GROUP BY 1, 2
         ORDER BY 3 DESC
         LIMIT $2
        """,
        target,
        TOP_CATEGORY_LIMIT,
    )
    m.by_category = [
        {"code": r["code"], "name_ko": r["name_ko"], "n": int(r["n"])}
        for r in rows
    ]

    # 카테고리 (부정)
    rows = await conn.fetch(
        """
        SELECT cat AS code, c.name_ko AS name_ko, count(*) AS n
          FROM (
            SELECT unnest(categories) AS cat
              FROM voc_records
             WHERE collected_at::date = $1 AND sentiment_label = 'negative'
          ) t
          LEFT JOIN voc_categories c ON c.code = t.cat
         GROUP BY 1, 2
         ORDER BY 3 DESC
         LIMIT $2
        """,
        target,
        TOP_CATEGORY_LIMIT,
    )
    m.by_category_neg = [
        {"code": r["code"], "name_ko": r["name_ko"], "n": int(r["n"])}
        for r in rows
    ]

    # 제품 TOP — 부정 비율 동반
    rows = await conn.fetch(
        """
        SELECT p.code, p.name_ko,
               count(*) AS n,
               sum(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END) AS neg,
               sum(CASE WHEN v.sentiment_label='positive' THEN 1 ELSE 0 END) AS pos
          FROM voc_records v
          JOIN products p ON p.id = v.product_id
         WHERE v.collected_at::date = $1
         GROUP BY p.code, p.name_ko
         ORDER BY 3 DESC
         LIMIT $2
        """,
        target,
        TOP_PRODUCT_LIMIT,
    )
    m.by_product = [
        {
            "code": r["code"],
            "name_ko": r["name_ko"],
            "n": int(r["n"]),
            "neg": int(r["neg"]),
            "pos": int(r["pos"]),
        }
        for r in rows
    ]

    # 플랫폼 TOP
    rows = await conn.fetch(
        """
        SELECT pl.code, pl.name, pl.region, count(*) AS n,
               sum(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END) AS neg
          FROM voc_records v
          JOIN platforms pl ON pl.id = v.platform_id
         WHERE v.collected_at::date = $1
         GROUP BY pl.code, pl.name, pl.region
         ORDER BY 4 DESC
         LIMIT $2
        """,
        target,
        TOP_PLATFORM_LIMIT,
    )
    m.by_platform = [
        {
            "code": r["code"],
            "name": r["name"],
            "region": r["region"],
            "n": int(r["n"]),
            "neg": int(r["neg"]),
        }
        for r in rows
    ]

    # 국가
    rows = await conn.fetch(
        """
        SELECT coalesce(country_code, 'unknown') AS cc, count(*) AS n
          FROM voc_records
         WHERE collected_at::date = $1
         GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """,
        target,
    )
    m.by_country = [{"cc": r["cc"], "n": int(r["n"])} for r in rows]

    # 신규 제품 — 이 제품의 첫 등장이 이 일자인 경우만
    rows = await conn.fetch(
        """
        WITH first_seen AS (
          SELECT product_id, min(collected_at::date) AS d
            FROM voc_records
           WHERE product_id IS NOT NULL
           GROUP BY 1
        )
        SELECT p.code, p.name_ko, fs.d
          FROM first_seen fs
          JOIN products p ON p.id = fs.product_id
         WHERE fs.d = $1
         ORDER BY p.code
        """,
        target,
    )
    m.new_products_today = [
        {"code": r["code"], "name_ko": r["name_ko"]} for r in rows
    ]

    # 대표 부정 문장 (engagement 큰 것 우선)
    rows = await conn.fetch(
        """
        SELECT coalesce(content_translated, content_original) AS text,
               p.code AS prod, pl.code AS plat,
               v.sentiment_score, v.engagement_score
          FROM voc_records v
          LEFT JOIN products p ON p.id = v.product_id
          LEFT JOIN platforms pl ON pl.id = v.platform_id
         WHERE v.collected_at::date = $1
           AND v.sentiment_label = 'negative'
           AND length(coalesce(content_translated, content_original)) BETWEEN 30 AND 600
         ORDER BY v.engagement_score DESC NULLS LAST, v.sentiment_score ASC
         LIMIT $2
        """,
        target,
        SAMPLE_LIMIT_PER_BUCKET,
    )
    m.top_negative = [
        {
            "text": _short(r["text"]),
            "product": r["prod"],
            "platform": r["plat"],
            "score": float(r["sentiment_score"]) if r["sentiment_score"] is not None else None,
        }
        for r in rows
    ]

    # 대표 긍정 문장
    rows = await conn.fetch(
        """
        SELECT coalesce(content_translated, content_original) AS text,
               p.code AS prod, pl.code AS plat,
               v.sentiment_score, v.engagement_score
          FROM voc_records v
          LEFT JOIN products p ON p.id = v.product_id
          LEFT JOIN platforms pl ON pl.id = v.platform_id
         WHERE v.collected_at::date = $1
           AND v.sentiment_label = 'positive'
           AND length(coalesce(content_translated, content_original)) BETWEEN 30 AND 600
         ORDER BY v.engagement_score DESC NULLS LAST, v.sentiment_score DESC
         LIMIT $2
        """,
        target,
        SAMPLE_LIMIT_PER_BUCKET,
    )
    m.top_positive = [
        {
            "text": _short(r["text"]),
            "product": r["prod"],
            "platform": r["plat"],
            "score": float(r["sentiment_score"]) if r["sentiment_score"] is not None else None,
        }
        for r in rows
    ]
    return m


def _short(s: Optional[str], maxlen: int = 280) -> str:
    """LLM 프롬프트에 들어갈 문장 길이 제한."""
    if not s:
        return ""
    s = " ".join(s.split())
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


# ────────────────────────────────────────────────────────────────────────────
# 프롬프트 / 폴백 요약 직렬화
# ────────────────────────────────────────────────────────────────────────────
def render_raw_summary(m: DailyMetrics) -> str:
    """사람이 읽을 수 있는 raw 요약 (LLM 입력 + 폴백 출력 양쪽 사용)."""
    lines: List[str] = []
    lines.append(f"# SignalForge Raw Summary — {m.target_date.isoformat()}")
    lines.append("")
    lines.append(f"- 수집 총량: **{m.total:,}**건")
    if m.sentiment_score_avg is not None:
        lines.append(f"- 평균 감성 점수: {m.sentiment_score_avg:+.3f}")
    if m.avg_engagement is not None:
        lines.append(f"- 평균 engagement 점수: {m.avg_engagement:.2f}")
    if m.by_sentiment:
        parts = [f"{k}={v:,}" for k, v in sorted(m.by_sentiment.items(), key=lambda x: -x[1])]
        lines.append(f"- 감성 분포: {', '.join(parts)}")
    lines.append("")

    if m.by_category:
        lines.append("## 카테고리 TOP (전체)")
        for c in m.by_category:
            label = c["name_ko"] or c["code"]
            lines.append(f"- {label} ({c['code']}): {c['n']:,}건")
        lines.append("")

    if m.by_category_neg:
        lines.append("## 부정 언급 카테고리 TOP")
        for c in m.by_category_neg:
            label = c["name_ko"] or c["code"]
            lines.append(f"- {label}: {c['n']:,}건")
        lines.append("")

    if m.by_product:
        lines.append("## 제품 TOP")
        for p in m.by_product:
            ratio = (p["neg"] / p["n"] * 100) if p["n"] else 0
            lines.append(
                f"- {p['name_ko']} ({p['code']}): {p['n']:,}건 "
                f"(부정 {p['neg']:,} / 긍정 {p['pos']:,}, 부정률 {ratio:.1f}%)"
            )
        lines.append("")

    if m.by_platform:
        lines.append("## 플랫폼 TOP")
        for p in m.by_platform:
            lines.append(
                f"- {p['name']} ({p['code']}, {p['region']}): "
                f"{p['n']:,}건 (부정 {p['neg']:,})"
            )
        lines.append("")

    if m.by_country:
        lines.append("## 국가 분포")
        for c in m.by_country:
            lines.append(f"- {c['cc']}: {c['n']:,}건")
        lines.append("")

    if m.new_products_today:
        lines.append("## 오늘 첫 등장 제품")
        for p in m.new_products_today:
            lines.append(f"- {p['name_ko']} ({p['code']})")
        lines.append("")

    if m.top_negative:
        lines.append("## 부정 대표 문장")
        for s in m.top_negative:
            score = f"{s['score']:+.2f}" if s["score"] is not None else "n/a"
            lines.append(
                f"- [{s.get('product') or '?'}/{s.get('platform') or '?'} "
                f"score={score}] {s['text']}"
            )
        lines.append("")

    if m.top_positive:
        lines.append("## 긍정 대표 문장")
        for s in m.top_positive:
            score = f"{s['score']:+.2f}" if s["score"] is not None else "n/a"
            lines.append(
                f"- [{s.get('product') or '?'}/{s.get('platform') or '?'} "
                f"score={score}] {s['text']}"
            )
        lines.append("")

    return "\n".join(lines)


def build_prompt(m: DailyMetrics) -> str:
    """LLM 용 prompt: raw 요약을 컨텍스트로, 작성 지시는 명확하게."""
    raw = render_raw_summary(m)
    instructions = (
        "위 데이터는 어제 하루(UTC 기준) SignalForge 가 수집한 "
        f"Galaxy / 삼성 관련 VOC {m.total:,}건의 통계 요약입니다.\n\n"
        "다음 지침으로 '오늘의 SignalForge 인사이트' 한국어 보고서를 작성하세요:\n"
        "1) 8-12 문단, Markdown 사용.\n"
        "2) 첫 문단은 핵심 헤드라인(1-2문장) — 의사결정자에게 우선 알려야 할 사실.\n"
        "3) 이어서 (a) 수집 규모/감성 분포, (b) 부정 신호 TOP 카테고리·"
        "제품, (c) 긍정 강점, (d) 플랫폼/국가 별 특이점, (e) 오늘 첫 등장한 "
        "제품 의미, (f) 대표 부정·긍정 문장에서 읽히는 사용자 우려/만족, "
        "(g) 내일·다음 주 모니터링 우선순위, 순으로 다룹니다.\n"
        "4) 모든 주장에 수치(절대 수, %, 또는 비율)를 동반하세요. "
        "데이터에 없는 사실은 추측하지 말고 명시적으로 '데이터로 확인되지 않음'을 표기.\n"
        "5) 부정 비율, 카테고리 가중치 등은 위 통계에서 직접 계산해 인용하세요.\n"
        "6) 문체는 사실 중심, 간결, 분석적 ('-습니다'/'-입니다' 체).\n"
        "7) 마지막에 '## 권장 액션' 섹션으로 3-5개 bullet 액션 아이템.\n"
    )
    return f"{raw}\n\n---\n\n{instructions}"


# ────────────────────────────────────────────────────────────────────────────
# 보고서 작성 (LLM or fallback)
# ────────────────────────────────────────────────────────────────────────────
def render_report(m: DailyMetrics, llm_output: Optional[str]) -> str:
    header = [
        f"# 오늘의 SignalForge 인사이트 — {m.target_date.isoformat()}",
        "",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 어제 수집 총량: {m.total:,}건",
    ]
    if m.by_sentiment:
        parts = [f"{k} {v:,}" for k, v in sorted(m.by_sentiment.items(), key=lambda x: -x[1])]
        header.append(f"- 감성 분포: {' / '.join(parts)}")
    header.extend(["", "---", ""])

    if llm_output:
        body = ["## LLM 분석", "", llm_output.strip(), "", "---", ""]
    else:
        body = [
            "## LLM 분석",
            "",
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY 가 설정되지 않아 LLM 분석은 생략. "
            "아래의 raw 요약을 참고하세요.)",
            "",
            "---",
            "",
        ]

    body.extend(["## Raw 요약 (자동 집계)", "", render_raw_summary(m)])
    return "\n".join(header + body)


# ────────────────────────────────────────────────────────────────────────────
# 엔트리
# ────────────────────────────────────────────────────────────────────────────
async def run(
    target: Optional[date] = None,
    *,
    db_url: Optional[str] = None,
    report_dir: Optional[Path] = None,
) -> Path:
    if target is None:
        target = (datetime.now(timezone.utc).date() - timedelta(days=1))
    if report_dir is None:
        report_dir = Path(os.getenv("REPORT_DIR", str(DEFAULT_REPORT_DIR)))
    report_dir.mkdir(parents=True, exist_ok=True)

    dsn = db_url or _resolve_database_url()
    conn = await asyncpg.connect(dsn)
    try:
        metrics = await collect_metrics(conn, target)
    finally:
        await conn.close()

    if metrics.total == 0:
        logger.warning("대상 일자 %s 데이터 0건 — 빈 리포트만 작성", target)

    # 사용자 정책(2026-06-01): LLM 키 없거나 호출 실패 시 fallback 보고서를 만들지 않는다.
    # — 사용자가 "키 없으면 기능 동작 안 함" 명시.
    #
    # P3.7 라우팅: 일일 high tier 1회 시도 → 실패 시 fast (Ollama 포함) 폴백.
    # used_tier 는 footer 에 기록한다.
    provider, used_tier = _select_provider_with_tier()
    if provider is None:
        logger.error(
            "LLM provider 미설정 — insight 비활성. .env 에 ANTHROPIC_API_KEY 또는 "
            "OPENAI_API_KEY(+ 선택적 OPENAI_BASE_URL) 설정 필요."
        )
        return None
    if metrics.total == 0:
        logger.info("데이터 0건 — LLM 호출 skip, 보고서 생성 X")
        return None

    # grounded 호출: DailyMetrics → dict payload → summarize_json
    payload = _metrics_to_payload(metrics)
    schema_desc = (
        f"SignalForge 일간 VOC 집계 — 대상 일자 {metrics.target_date.isoformat()} "
        f"(UTC). 표에는 총량, 감성 분포, 카테고리/제품/플랫폼/국가 TOP, "
        "신규 제품, 부정·긍정 대표 문장이 포함됩니다."
    )
    # R11 트랙 B — 필수 인용 항목을 헤더에 명시하여 grounding 회복
    top_cat = (metrics.by_category[0]["name_ko"] if metrics.by_category else None)
    top_prod = (metrics.by_product[0]["name_ko"] if metrics.by_product else None)
    top_plat = (metrics.by_platform[0]["name"] if metrics.by_platform else None)
    must_cite = (
        f"[필수 인용 — 반드시 본문에 정확히 등장]\n"
        f"- 총 수집: {metrics.total}건\n"
        f"- 감성 평균: {metrics.sentiment_score_avg if metrics.sentiment_score_avg is not None else '-'}\n"
        f"- 가장 활발한 카테고리: {top_cat or '-'}\n"
        f"- 가장 활발한 제품: {top_prod or '-'}\n"
        f"- 가장 활발한 사이트: {top_plat or '-'}\n"
    )
    instructions = (
        must_cite + "\n"
        "위 5개 항목 모두를 본문에서 그대로 인용하세요.\n"
        "또한 표를 근거로 한국어 '오늘의 SignalForge 인사이트' 보고서를 작성:\n"
        "1) 8-12 문단, Markdown.\n"
        "2) 첫 문단 핵심 헤드라인 1-2문장. 총 수집과 감성 평균을 첫 문단에 반드시 명시.\n"
        "3) (a) 수집 규모/감성 분포, (b) 부정 신호 TOP 카테고리/제품, "
        "(c) 긍정 강점, (d) 플랫폼/국가 별 특이점, (e) 오늘 첫 등장 제품, "
        "(f) 대표 문장에서 읽히는 사용자 우려/만족, (g) 내일 모니터링 우선순위.\n"
        "4) 모든 수치는 표의 값을 그대로 인용. 표에 없는 사실은 '데이터 없음' 명시.\n"
        "5) 문체: 사실 중심·간결·'-습니다/입니다' 체.\n"
        "6) 마지막에 '## 권장 액션' 섹션으로 3-5 bullet."
    )

    logger.info("LLM 호출(grounded): provider=%s tier=%s", provider.name, used_tier)
    llm_text = provider.summarize_json(payload, schema_desc, instructions)

    # grounding 검증 + 점수 < 0.5 면 1회 재요청
    try:
        from insight.grounding import validate_response  # type: ignore
    except Exception:
        validate_response = None  # type: ignore

    score: Optional[float] = None
    if llm_text and validate_response is not None:
        score = validate_response(llm_text, payload)
        if score < 0.5:
            logger.warning("grounding score 낮음(%.2f) — 강화 재요청", score)
            stronger = (
                instructions
                + "\n\n[CRITICAL] 직전 응답에서 수치 인용이 부족했습니다. "
                "표의 총량/제품 TOP/카테고리 TOP/감성 분포 수치를 본문에 그대로 인용하세요."
            )
            retry = provider.summarize_json(payload, schema_desc, stronger)
            if retry:
                retry_score = validate_response(retry, payload)
                if retry_score > score:
                    llm_text = retry
                    score = retry_score

    if llm_text is None:
        logger.error("LLM 호출 실패 — insight 보고서 미생성 (정책: fallback 거부)")
        return None

    # COMPARE_TIERS=true 면 fast 도 함께 호출해 두 tier 의 grounding 을 비교 로깅하고
    # 보고서 본문에 두 섹션으로 병기한다 (high tier 가 used_tier 일 때만 의미 있음).
    compare_section: Optional[str] = None
    if (
        os.getenv("COMPARE_TIERS", "").strip().lower() == "true"
        and used_tier == "high"
        and validate_response is not None
    ):
        fast_prov = get_provider(tier="fast")
        if fast_prov is not None:
            try:
                fast_text = fast_prov.summarize_json(payload, schema_desc, instructions)
                if fast_text:
                    fast_score = validate_response(fast_text, payload)
                    logger.info(
                        "COMPARE_TIERS: high(%s/%s)=%.3f vs fast(%s/%s)=%.3f",
                        provider.name, getattr(provider, "tier_label", None) or "?",
                        score or 0.0,
                        fast_prov.name, getattr(fast_prov, "tier_label", None) or "?",
                        fast_score,
                    )
                    fast_label = getattr(fast_prov, "tier_label", None) or "fast"
                    compare_section = (
                        f"\n\n---\n\n## 비교: fast tier 출력 ({fast_label})\n\n"
                        f"_grounding score: {fast_score:.2f} "
                        f"(provider: {fast_prov.name})_\n\n"
                        f"{fast_text.strip()}"
                    )
            except Exception as e:
                logger.warning("COMPARE_TIERS fast 호출 실패: %s", e)

    # footer 에 used_tier + prompt_version + grounding score 표기.
    score_str = f"{score:.2f}" if score is not None else "n/a"
    tier_label = getattr(provider, "tier_label", None) or used_tier
    llm_text = (
        f"{llm_text.rstrip()}\n\n---\n"
        f"_LLM grounding score: {score_str} "
        f"(provider: {provider.name}, used_tier: {used_tier}, "
        f"tier_label: {tier_label}, prompt_version: {PROMPT_VERSION})_"
    )
    if compare_section:
        llm_text = f"{llm_text}{compare_section}"

    md = render_report(metrics, llm_text)
    out_path = report_dir / f"insight_{target.isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("보고서 저장: %s (grounding=%s)", out_path, score)

    # 7일 grounding 추이 JSON 갱신 (모니터링/대시보드 용).
    try:
        _update_grounding_history(
            report_dir=report_dir,
            target_date=target,
            score=score,
            provider_name=provider.name,
            used_tier=used_tier,
            tier_label=tier_label,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as e:  # pragma: no cover
        logger.warning("grounding history 갱신 실패: %s", e)
    return out_path


def _update_grounding_history(
    *,
    report_dir: Path,
    target_date: date,
    score: Optional[float],
    provider_name: str,
    used_tier: str,
    tier_label: str,
    prompt_version: str,
    keep_days: int = 7,
) -> Path:
    """reports/insight_grounding_history.json 에 마지막 keep_days 일치 기록.

    같은 날짜 entry 가 이미 있으면 덮어쓴다. 7개 초과 시 오래된 것부터 제거.
    """
    path = report_dir / "insight_grounding_history.json"
    history: List[Dict[str, Any]] = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    new_entry = {
        "date": target_date.isoformat(),
        "grounding_score": score,
        "provider": provider_name,
        "used_tier": used_tier,
        "tier_label": tier_label,
        "prompt_version": prompt_version,
    }
    # 같은 날짜 제거 후 추가.
    history = [h for h in history if h.get("date") != target_date.isoformat()]
    history.append(new_entry)
    # 날짜순 정렬 + 최신 keep_days 유지.
    history.sort(key=lambda h: str(h.get("date") or ""))
    history = history[-keep_days:]
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _select_provider_with_tier():
    """P3.7 라우팅: high → fast 폴백 + 실제 사용된 tier 라벨 동시 반환.

    반환: (provider | None, used_tier: 'high'|'fast'|'none')
    """
    high = get_provider(tier="high")
    if high is not None:
        return high, "high"
    fast = get_provider(tier="fast")
    if fast is not None:
        return fast, "fast"
    return None, "none"


def _metrics_to_payload(m: DailyMetrics) -> Dict[str, Any]:
    """DailyMetrics → grounding.metrics_to_markdown 이 이해하는 dict."""
    return {
        "target_date": m.target_date.isoformat(),
        "total": m.total,
        "sentiment_score_avg": m.sentiment_score_avg,
        "avg_engagement": m.avg_engagement,
        "by_sentiment": dict(m.by_sentiment),
        "by_category": list(m.by_category),
        "by_category_neg": list(m.by_category_neg),
        "by_product": list(m.by_product),
        "by_platform": list(m.by_platform),
        "by_country": list(m.by_country),
        "new_products_today": list(m.new_products_today),
        "top_negative": list(m.top_negative),
        "top_positive": list(m.top_positive),
    }


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    ap = argparse.ArgumentParser(description="SignalForge daily LLM insight")
    ap.add_argument("date", nargs="?", help="대상 일자 (YYYY-MM-DD, 기본=어제)")
    ap.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="어제 외 N일 전 자동 선택 (1=어제, 2=그제…)",
    )
    args = ap.parse_args(argv)

    if args.date:
        target = _parse_date(args.date)
    elif args.days_back is not None:
        target = datetime.now(timezone.utc).date() - timedelta(days=args.days_back)
    else:
        target = datetime.now(timezone.utc).date() - timedelta(days=1)

    out = asyncio.run(run(target=target))
    print(f"OK: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
