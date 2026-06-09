"""운영 상태 7일 누적 트렌드 분석 (R19 트랙 D).

목적
----
``ops_history.py`` 가 매일 09:30 KST 에 ``reports/ops_status_YYYY-MM-DD.json`` 으로
적재하는 일별 운영 스냅샷을 7일치(혹은 N일치) 누적 분석한다.

``/api/v1/_internal/ops-trend`` endpoint 가 *raw 시계열* 만 응답하는 데 반해, 이
모듈은 **운영자가 곧장 읽을 수 있는 한국어 진단 보고서** 를 생성한다:

* 변화율 — 전일 / 시작 vs 끝
* 7일 이동 평균
* 임계 위반 트렌드 — voc 일별 감소·sentiment NULL 증가·grounding 저하·violation 누적
* 회귀 회복/악화 — regression_failed 의 7일 흐름
* 권고 — 데이터 기반 다음 액션

R18 D 트랙은 ops-trend endpoint 와 적재기까지만 완성했고, 이 모듈은 그
*활용/분석* 레이어를 채운다.

CLI::

    python -m insight.ops_trend_analysis                       # 7일 (기본)
    python -m insight.ops_trend_analysis --days 14
    python -m insight.ops_trend_analysis --no-http             # endpoint 안 거치고 파일 직독
    python -m insight.ops_trend_analysis --backfill-from-db    # 빠진 날짜를 DB 에서 재구성
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
DEFAULT_API = os.getenv("SIGNALFORGE_API", "http://127.0.0.1:8000")

# operations_monitor 와 동일한 임계 (단일 진실 원천 — 변경 시 양쪽 함께)
THRESH_VOC_DAILY_DROP_PCT = 50.0
THRESH_SENTIMENT_NULL_RATE = 0.10
THRESH_TOPIC_RATE_DROP_PCT = 20.0
THRESH_GROUNDING_MIN = 0.30
THRESH_REGRESSION_OK_MIN = 1.0


# ── 데이터 수집 ──────────────────────────────────────────────────────────
def _http_get_json(url: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """endpoint 호출. 네트워크/HTTP 오류 시 None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
        logger.warning("[ops-trend-analysis] endpoint 호출 실패 (%s): %s", url, exc)
        return None


def _load_from_files(days: int, report_dir: Path = DEFAULT_REPORT_DIR) -> List[Dict[str, Any]]:
    """``reports/ops_status_*.json`` 최근 ``days`` 개를 *오래된→최신* 순으로 로드.

    endpoint 호출이 가능하면 endpoint 결과를 우선 사용하지만, ``--no-http`` 또는
    endpoint 가 죽었을 때 fallback 으로 동일 데이터를 만들어 준다.
    """
    if not report_dir.is_dir():
        return []
    files = sorted(
        report_dir.glob("ops_status_*.json"),
        key=lambda p: p.name,
    )[-days:]
    out: List[Dict[str, Any]] = []
    for fp in files:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
            out.append(payload)
        except Exception as exc:
            logger.warning("[ops-trend-analysis] %s 파싱 실패: %s", fp.name, exc)
    return out


def _build_series_from_files(days: int, report_dir: Path) -> Dict[str, Any]:
    """파일 직독으로 endpoint 와 *동일 스키마* dict 를 만든다.

    endpoint 가 죽었거나 ``--no-http`` 일 때 fallback. 변화율 / 이동 평균 계산은
    ``analyse`` 가 처리하므로 여기서는 raw series 만 추출.
    """
    snaps = _load_from_files(days, report_dir)
    series: List[Dict[str, Any]] = []
    prev_voc: Optional[float] = None
    for s in snaps:
        v = s.get("voc_last")
        delta = None
        if isinstance(v, (int, float)) and isinstance(prev_voc, (int, float)) and prev_voc:
            delta = round((v - prev_voc) / abs(prev_voc) * 100.0, 2)
        series.append({
            "date": s.get("target_date"),
            "status": s.get("status"),
            "voc_last": v,
            "voc_delta_pct": delta,
            "sentiment_null_rate": s.get("sentiment_null_rate"),
            "topic_rate": s.get("topic_rate"),
            "grounding_last": s.get("grounding_last"),
            "regression_failed": s.get("regression_failed"),
            "violations_count": s.get("violations_count"),
        })
        if isinstance(v, (int, float)):
            prev_voc = v
    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": [s["date"] for s in series if s.get("date")],
        "series": series,
    }


def fetch_trend(days: int,
                *,
                use_http: bool = True,
                api_base: str = DEFAULT_API,
                report_dir: Path = DEFAULT_REPORT_DIR) -> Dict[str, Any]:
    """endpoint 우선, 실패 시 파일 직독 fallback."""
    if use_http:
        payload = _http_get_json(f"{api_base}/api/v1/_internal/ops-trend?days={days}")
        if payload and payload.get("series") is not None:
            return payload
    return _build_series_from_files(days, report_dir)


# ── 분석 핵심 ────────────────────────────────────────────────────────────
def _moving_avg(values: List[Optional[float]], window: int = 7) -> List[Optional[float]]:
    """단순 이동 평균. window 미만은 None. NaN 항목은 평균에서 제외."""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        chunk = [v for v in values[start:i + 1] if isinstance(v, (int, float))]
        if i + 1 < window or not chunk:
            out.append(None)
        else:
            out.append(round(sum(chunk) / len(chunk), 4))
    return out


def _trend_slope(values: List[Optional[float]]) -> Optional[float]:
    """일별 평균 변화량 (last - first) / (n-1). 데이터 < 2 개 시 None."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if len(nums) < 2:
        return None
    return round((nums[-1] - nums[0]) / (len(nums) - 1), 4)


def _count_threshold_breaches(series: List[Dict[str, Any]]) -> Dict[str, int]:
    """일별 임계 위반 카운트 — voc 50%+ 감소·sentiment 10%+·grounding<0.3·regression!=ok."""
    voc_drops = 0
    sent_breach = 0
    topic_drops = 0
    grounding_low = 0
    regression_fail = 0
    prev_topic: Optional[float] = None

    for s in series:
        # voc daily drop
        d = s.get("voc_delta_pct")
        if isinstance(d, (int, float)) and d <= -THRESH_VOC_DAILY_DROP_PCT:
            voc_drops += 1
        # sentiment null rate
        n = s.get("sentiment_null_rate")
        if isinstance(n, (int, float)) and n >= THRESH_SENTIMENT_NULL_RATE:
            sent_breach += 1
        # topic rate drop (vs 전일)
        t = s.get("topic_rate")
        if isinstance(prev_topic, (int, float)) and isinstance(t, (int, float)) and prev_topic > 0:
            drop_pct = (prev_topic - t) / prev_topic * 100.0
            if drop_pct >= THRESH_TOPIC_RATE_DROP_PCT:
                topic_drops += 1
        if isinstance(t, (int, float)):
            prev_topic = t
        # grounding
        g = s.get("grounding_last")
        if isinstance(g, (int, float)) and g < THRESH_GROUNDING_MIN:
            grounding_low += 1
        # regression
        r = s.get("regression_failed")
        if isinstance(r, (int, float)) and r > 0:
            regression_fail += 1

    return {
        "voc_drop_50pct": voc_drops,
        "sentiment_null_breach": sent_breach,
        "topic_rate_drop_20pct": topic_drops,
        "grounding_below_0_30": grounding_low,
        "regression_failed_days": regression_fail,
    }


def analyse(trend: Dict[str, Any]) -> Dict[str, Any]:
    """endpoint 응답 → 분석 dict (보고서 작성에 쓰는 모든 수치를 포함).

    series 가 비어 있어도 graceful — 키 형태만 유지하고 값은 None.
    """
    series: List[Dict[str, Any]] = trend.get("series") or []

    voc = [s.get("voc_last") for s in series]
    sent = [s.get("sentiment_null_rate") for s in series]
    topic = [s.get("topic_rate") for s in series]
    ground = [s.get("grounding_last") for s in series]
    viol = [s.get("violations_count") for s in series]
    regf = [s.get("regression_failed") for s in series]

    voc_nums = [v for v in voc if isinstance(v, (int, float))]
    voc_first = voc_nums[0] if voc_nums else None
    voc_last = voc_nums[-1] if voc_nums else None
    voc_change_7d: Optional[float] = None
    if voc_first not in (None, 0) and voc_last is not None:
        voc_change_7d = round((voc_last - voc_first) / abs(voc_first) * 100.0, 2)

    breaches = _count_threshold_breaches(series)

    return {
        "days_requested": trend.get("days"),
        "days_available": len(series),
        "available_dates": trend.get("available", []),
        "generated_at": trend.get("generated_at"),
        # 핵심 수치
        "voc_first": voc_first,
        "voc_last": voc_last,
        "voc_change_pct_7d": voc_change_7d,
        "voc_min": min(voc_nums) if voc_nums else None,
        "voc_max": max(voc_nums) if voc_nums else None,
        "voc_median": round(statistics.median(voc_nums), 1) if voc_nums else None,
        "voc_slope_per_day": _trend_slope(voc),
        # 품질 트렌드
        "sentiment_null_avg": round(statistics.mean(
            [v for v in sent if isinstance(v, (int, float))]), 4) if any(
            isinstance(v, (int, float)) for v in sent) else None,
        "topic_rate_avg": round(statistics.mean(
            [v for v in topic if isinstance(v, (int, float))]), 4) if any(
            isinstance(v, (int, float)) for v in topic) else None,
        "topic_rate_slope_per_day": _trend_slope(topic),
        "grounding_avg": round(statistics.mean(
            [v for v in ground if isinstance(v, (int, float))]), 4) if any(
            isinstance(v, (int, float)) for v in ground) else None,
        "grounding_slope_per_day": _trend_slope(ground),
        # 누적
        "violations_total": sum(int(v) for v in viol if isinstance(v, (int, float))),
        "regression_failed_total": sum(int(v) for v in regf if isinstance(v, (int, float))),
        # 임계 위반 일수
        "breaches": breaches,
        # 이동 평균 (시계열 그대로)
        "moving_avg_7d": {
            "voc_last": _moving_avg(voc, 7),
            "grounding_last": _moving_avg(ground, 7),
            "violations_count": _moving_avg(viol, 7),
        },
        # series 보존 (보고서 표 출력용)
        "series": series,
    }


def recommendations(analysis: Dict[str, Any]) -> List[str]:
    """데이터 기반 다음 액션 — 임계 위반 패턴별 1-N건."""
    out: List[str] = []
    b = analysis.get("breaches") or {}

    if b.get("voc_drop_50pct", 0) >= 1:
        out.append(
            f"voc 일일 50%+ 감소 {b['voc_drop_50pct']}건 — 크롤러 health-check "
            "(시간대별 site_status, retry queue) 우선 확인."
        )
    if b.get("sentiment_null_breach", 0) >= 1:
        out.append(
            "sentiment_label NULL 비율이 10% 임계 초과 — sentiment 파이프 "
            "(번역 → 분류) 단계별 처리량 점검."
        )
    if b.get("topic_rate_drop_20pct", 0) >= 1:
        out.append(
            "topic 분류율이 전일 대비 20%+ 감소 — topic_classifier 룰 회귀 의심, "
            "최근 PR 또는 정규식 변경 확인."
        )
    if b.get("grounding_below_0_30", 0) >= 1:
        out.append(
            "LLM grounding 점수 0.30 미만 — prompt v4 / context window / "
            "compare_insight 입력 정합성 점검."
        )
    if b.get("regression_failed_days", 0) >= 1:
        out.append(
            f"regression 회귀 실패 누적 {analysis.get('regression_failed_total', 0)}건 — "
            "/_internal/regression-baseline 의 failed 케이스 우선 fix."
        )
    if analysis.get("days_available", 0) < 3:
        out.append(
            "ops_status 적재가 3일 미만 — Celery beat run_ops_history (09:30 KST) "
            "정상 동작 확인 + 누락일 backfill 검토."
        )
    if analysis.get("voc_change_pct_7d") is not None:
        v = analysis["voc_change_pct_7d"]
        if v <= -30.0:
            out.append(
                f"7일 voc 누적 감소 {v}% — 사이트 비활성/언어팩 회귀 등 "
                "구조적 원인 가능성. /collection-health 동반 점검."
            )

    # 위반 0 이면 한 줄 안내
    if not out:
        out.append("임계 위반 없음. 운영 SLO 7일 안정 — 계속 일별 적재 유지.")
    return out


# ── 보고서 ───────────────────────────────────────────────────────────────
def _fmt_pct(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    return f"{v:+.2f}%"


def _fmt_num(v: Optional[float], digits: int = 0) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    return f"{v:,.{digits}f}"


def render_markdown(analysis: Dict[str, Any]) -> str:
    """analysis → 한국어 운영 보고서 markdown."""
    lines: List[str] = []
    today = datetime.now(timezone.utc).date().isoformat()
    lines.append(f"# 운영 상태 7일 누적 트렌드 분석 — {today}\n")

    days_av = analysis.get("days_available") or 0
    days_req = analysis.get("days_requested") or 7
    avail = analysis.get("available_dates") or []
    lines.append(
        f"- 요청 기간: 최근 {days_req}일 / 적재된 스냅샷: **{days_av}일**\n"
        f"- 가용 날짜: {', '.join(avail) if avail else '(없음)'}\n"
        f"- 보고서 생성 시각: {analysis.get('generated_at') or '-'}\n"
    )

    # 1) 핵심 KPI
    lines.append("\n## 1. 핵심 KPI 7일 요약\n")
    lines.append("| 지표 | 시작 | 현재 | 변화 | 7일 평균 변화/일 |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| voc_last (일별 수집량) | {_fmt_num(analysis.get('voc_first'))} | "
        f"{_fmt_num(analysis.get('voc_last'))} | "
        f"{_fmt_pct(analysis.get('voc_change_pct_7d'))} | "
        f"{_fmt_num(analysis.get('voc_slope_per_day'), 1)} |"
    )
    lines.append(
        f"| topic 분류율 | - | {_fmt_num(analysis.get('topic_rate_avg'), 4)} | "
        f"- | {_fmt_num(analysis.get('topic_rate_slope_per_day'), 4)} |"
    )
    lines.append(
        f"| LLM grounding | - | {_fmt_num(analysis.get('grounding_avg'), 4)} | "
        f"- | {_fmt_num(analysis.get('grounding_slope_per_day'), 4)} |"
    )

    # 2) 임계 위반 누적
    lines.append("\n## 2. 임계 위반 누적 (7일)\n")
    b = analysis.get("breaches") or {}
    lines.append("| 위반 항목 | 임계 | 위반 일수 |")
    lines.append("|---|---|---:|")
    lines.append(f"| voc 일일 50%+ 감소 | drop ≥ {THRESH_VOC_DAILY_DROP_PCT}% | {b.get('voc_drop_50pct', 0)} |")
    lines.append(f"| sentiment_label NULL 비율 | ≥ {THRESH_SENTIMENT_NULL_RATE:.0%} | {b.get('sentiment_null_breach', 0)} |")
    lines.append(f"| topic 분류율 전일 대비 감소 | drop ≥ {THRESH_TOPIC_RATE_DROP_PCT}% | {b.get('topic_rate_drop_20pct', 0)} |")
    lines.append(f"| LLM grounding 저하 | < {THRESH_GROUNDING_MIN} | {b.get('grounding_below_0_30', 0)} |")
    lines.append(f"| regression failed > 0 | failed ≥ 1 | {b.get('regression_failed_days', 0)} |")
    lines.append(
        f"\n- 누적 violations: **{analysis.get('violations_total', 0)}**, "
        f"누적 regression failed: **{analysis.get('regression_failed_total', 0)}**"
    )

    # 3) 일별 시계열
    lines.append("\n## 3. 일별 시계열 (오래된 → 최신)\n")
    lines.append("| 날짜 | status | voc_last | Δ% | sent_null | topic_rate | grounding | reg_failed | viol |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for s in analysis.get("series") or []:
        lines.append(
            f"| {s.get('date') or '-'} | {s.get('status') or '-'} | "
            f"{_fmt_num(s.get('voc_last'))} | {_fmt_pct(s.get('voc_delta_pct'))} | "
            f"{_fmt_num(s.get('sentiment_null_rate'), 4)} | "
            f"{_fmt_num(s.get('topic_rate'), 4)} | "
            f"{_fmt_num(s.get('grounding_last'), 4)} | "
            f"{_fmt_num(s.get('regression_failed'))} | "
            f"{_fmt_num(s.get('violations_count'))} |"
        )

    # 4) 7일 이동 평균
    lines.append("\n## 4. 7일 이동 평균\n")
    ma = analysis.get("moving_avg_7d") or {}
    lines.append("| 날짜 | voc_last 7DMA | grounding 7DMA | violations 7DMA |")
    lines.append("|---|---:|---:|---:|")
    dates = [s.get("date") for s in (analysis.get("series") or [])]
    for i, d in enumerate(dates):
        lines.append(
            f"| {d or '-'} | "
            f"{_fmt_num(ma.get('voc_last', [None]*len(dates))[i], 1)} | "
            f"{_fmt_num(ma.get('grounding_last', [None]*len(dates))[i], 4)} | "
            f"{_fmt_num(ma.get('violations_count', [None]*len(dates))[i], 2)} |"
        )

    # 5) 권고
    lines.append("\n## 5. 권고 (데이터 기반)\n")
    for rec in recommendations(analysis):
        lines.append(f"- {rec}")

    lines.append("")
    return "\n".join(lines)


# ── DB backfill (옵션) ───────────────────────────────────────────────────
def backfill_from_db(days: int,
                     *,
                     report_dir: Path = DEFAULT_REPORT_DIR,
                     dry_run: bool = False) -> List[Path]:
    """누락된 ``ops_status_*.json`` 일자를 voc_records / alert_events 에서 재구성.

    안전 장치:
    * 이미 존재하는 날짜는 *덮어쓰지 않는다* (R18 D 권고).
    * ``dry_run`` 이면 파일을 만들지 않고 만들 *예정* 경로만 반환.
    * 핵심 운영 metric (voc_last, voc_prev, sentiment_null_rate, topic_rate,
      violations_count) 만 재구성; grounding / regression 은 history 불가하여
      ``None`` 유지 (실시간 운영지표는 ops_history 정식 적재가 담당).
    """
    import asyncio
    import asyncpg

    async def _run() -> List[Path]:
        dsn = _dsn()
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT
                  to_char(date_trunc('day', collected_at), 'YYYY-MM-DD') AS d,
                  COUNT(*) AS n,
                  ROUND((SUM(CASE WHEN sentiment_label IS NULL THEN 1 ELSE 0 END)::numeric
                         / NULLIF(COUNT(*),0)::numeric), 4) AS null_rate,
                  ROUND((SUM(CASE WHEN topics IS NOT NULL AND array_length(topics, 1) > 0
                                  THEN 1 ELSE 0 END)::numeric
                         / NULLIF(COUNT(*),0)::numeric), 4) AS topic_rate
                FROM voc_records
                WHERE collected_at >= NOW() - ($1::int || ' days')::interval
                GROUP BY 1 ORDER BY 1
                """,
                days,
            )
            alerts = await conn.fetch(
                """
                SELECT
                  to_char(date_trunc('day', fired_at), 'YYYY-MM-DD') AS d,
                  COUNT(*) AS n,
                  SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit
                FROM alert_events
                WHERE fired_at >= NOW() - ($1::int || ' days')::interval
                GROUP BY 1 ORDER BY 1
                """,
                days,
            )
        finally:
            await conn.close()

        alert_by = {r["d"]: dict(r) for r in alerts}

        # 일별 dict — 어제 voc 가 voc_last (operations_monitor 와 같은 의미)
        by_day = [(r["d"], r) for r in rows]
        # voc_prev = 그 전일
        created: List[Path] = []
        prev_n: Optional[int] = None
        for i, (d, r) in enumerate(by_day):
            iso_d = d
            path = report_dir / f"ops_status_{iso_d}.json"
            if path.exists():
                prev_n = int(r["n"])
                continue  # R18 권고 — 기존 보호
            voc_last_n = int(r["n"])
            null_rate = float(r["null_rate"]) if r["null_rate"] is not None else None
            topic_rate = float(r["topic_rate"]) if r["topic_rate"] is not None else None
            arow = alert_by.get(iso_d) or {}
            viol_count = int(arow.get("n") or 0)
            status = "critical" if (arow.get("crit") or 0) > 0 else (
                "warning" if viol_count > 0 else "ok")

            payload = {
                "captured_at": f"{iso_d}T00:30:00+00:00",
                "target_date": iso_d,
                "status": status,
                "voc_last": voc_last_n,
                "voc_prev": prev_n,
                "sentiment_null_rate": null_rate,
                "topic_rate": topic_rate,
                "grounding_last": None,        # history 불가 — 정식 ops_history 만 채움
                "regression_ok_ratio": None,
                "regression_failed": None,
                "violations_count": viol_count,
                "violations": [],
                "_source": "backfill_from_db",
            }
            if not dry_run:
                report_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            created.append(path)
            prev_n = voc_last_n
        return created

    return asyncio.run(_run())


def _dsn() -> str:
    """DATABASE_URL → asyncpg 용으로 정리."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
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


# ── CLI ──────────────────────────────────────────────────────────────────
def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ops_trend_analysis")
    p.add_argument("--days", type=int, default=7, help="분석 기간 (기본 7)")
    p.add_argument("--no-http", action="store_true",
                   help="endpoint 안 거치고 reports/ 직독")
    p.add_argument("--api-base", default=DEFAULT_API)
    p.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    p.add_argument("--out", default=None,
                   help="보고서 출력 경로 (기본 reports/ops_trend_analysis_YYYY-MM-DD.md)")
    p.add_argument("--backfill-from-db", action="store_true",
                   help="누락된 ops_status_*.json 을 DB 에서 재구성 (기존 보호)")
    p.add_argument("--dry-run", action="store_true",
                   help="--backfill-from-db 와 함께 — 파일 생성 없이 예정 경로만 출력")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    report_dir = Path(args.report_dir)

    if args.backfill_from_db:
        created = backfill_from_db(args.days, report_dir=report_dir, dry_run=args.dry_run)
        verb = "would-create" if args.dry_run else "created"
        print(f"[ops-trend-analysis] backfill {verb}={len(created)}")
        for p in created:
            print(f"  - {p}")

    trend = fetch_trend(args.days, use_http=not args.no_http,
                        api_base=args.api_base, report_dir=report_dir)
    analysis = analyse(trend)
    md = render_markdown(analysis)

    out_path = Path(args.out) if args.out else (
        report_dir / f"ops_trend_analysis_{datetime.now(timezone.utc).date().isoformat()}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[ops-trend-analysis] saved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
