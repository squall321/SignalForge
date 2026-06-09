"""
운영 1주 모니터링 자동화 (Track D / R10 → P4 Harvest 3p)
=========================================

매일 09:30 KST (00:30 UTC) Celery beat 가 호출. 직전 7일의 운영 지표를
JSON + Markdown 두 산출로 적재하고, 키 입력 시 Slack 다이제스트를 1단 송출한다.

수집 신호 (6종):
  1. voc_records 일별 count + sentiment_avg     — DB 라이브 쿼리
  2. 사이트별 health                              — /_internal/collection-status
  3. 알림 발화 횟수                                — /_internal/alert-trends
  4. LLM grounding 점수                            — reports/insight_grounding_history.json
  5. 회귀 baseline 9개 metric                     — /_internal/regression-baseline
  6. 신규 사이트 진척 (14d 첫 수집)                — DB voc_records MIN(collected_at)

자동 알림 룰 (5종, 조건 충족 시 ``alerts`` 배열에 push):
  - voc_daily_drop_50pct       : 어제 voc 가 그제 대비 50%+ 감소
  - sentiment_shift_0p2        : 어제 sentiment_avg 가 그제 대비 0.2+ 변화
  - sites_active_below_15      : collection-status active < 15 (정상 23)
  - llm_grounding_below_0p4    : 어제 grounding < 0.4
  - regression_failed          : regression-baseline summary.failed > 0

산출:
  - ``reports/weekly_monitor_YYYY-WW.json`` (주차 누적, 덮어쓰기)
  - ``reports/weekly_monitor_YYYY-MM-DD.md`` (일별 운영자용)
  - Slack 1단 다이제스트 (ALERT_WEBHOOK_URL 또는 SLACK_WEBHOOK_URL 있을 때만)

Slack 정책 (P4 Harvest 3p):
  - 키 미입력 → ``slack={"status": "skipped", "reason": "no webhook"}`` 반환 후 graceful skip
  - HTTP 200/204 → ``status="sent"``
  - 그 외 / 예외 → ``status="failed"`` (task 자체는 깨지지 않음)

CLI:
    python -m insight.weekly_monitor              # 오늘(UTC) 기준 7일 윈도우
    python -m insight.weekly_monitor 2026-06-04   # 명시 일자
    python -m insight.weekly_monitor --days 7     # 윈도우 폭 (기본 7)
    python -m insight.weekly_monitor --no-slack   # Slack 송출 강제 비활성화
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# crawler/ 를 sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
DEFAULT_BASE = os.getenv("SIGNALFORGE_API", "http://127.0.0.1:8000")

# ── 임계값 (R10 운영 정책) ────────────────────────────────────────────────
THRESH_VOC_DROP_PCT = 50.0
THRESH_SENTIMENT_SHIFT = 0.2
THRESH_SITES_ACTIVE_MIN = 15
THRESH_GROUNDING_MIN = 0.4


def _dsn() -> str:
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


# ── 신호 수집 ────────────────────────────────────────────────────────────
async def collect_voc_daily(days: int, target: date) -> List[Dict[str, Any]]:
    """voc_records 일별 count + sentiment_avg (target-days+1 ~ target).

    target 포함 days 일. sentiment 는 sentiment_score IS NOT NULL 행만 평균.
    """
    end = target + timedelta(days=1)       # exclusive
    start = end - timedelta(days=days)
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT date_trunc('day', collected_at)::date AS day,
                   count(*) AS voc_count,
                   avg(sentiment_score) FILTER (WHERE sentiment_score IS NOT NULL)
                     AS sentiment_avg
            FROM voc_records
            WHERE collected_at >= $1::timestamp
              AND collected_at <  $2::timestamp
            GROUP BY 1
            ORDER BY 1
            """,
            start, end,
        )
    finally:
        await conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "day": r["day"].isoformat(),
            "voc_count": int(r["voc_count"] or 0),
            "sentiment_avg": (round(float(r["sentiment_avg"]), 4)
                              if r["sentiment_avg"] is not None else None),
        })
    return out


async def collect_new_site_progress(target: date, lookback_days: int = 14) -> List[Dict[str, Any]]:
    """직전 ``lookback_days`` 일 사이에 *첫 수집* 이 발생한 사이트 진척.

    각 항목::
        {
            "code": str,
            "first_seen": "YYYY-MM-DD",
            "voc_total": int,         # 첫 수집 이후 누적
            "voc_24h": int,           # target 직전 24h
            "active_24h": bool,       # voc_24h > 0
        }
    """
    end = target + timedelta(days=1)           # exclusive
    start = end - timedelta(days=lookback_days)
    last24 = end - timedelta(days=1)
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            """
            WITH first_seen AS (
                SELECT p.code,
                       MIN(v.collected_at) AS first_collected
                FROM platforms p
                JOIN voc_records v ON v.platform_id = p.id
                WHERE p.is_active = TRUE
                GROUP BY p.code
                HAVING MIN(v.collected_at) >= $1::timestamp
                   AND MIN(v.collected_at) <  $2::timestamp
            )
            SELECT fs.code,
                   fs.first_collected::date AS first_seen,
                   count(v.id)                            AS voc_total,
                   count(v.id) FILTER (WHERE v.collected_at >= $3::timestamp
                                         AND v.collected_at <  $2::timestamp) AS voc_24h
            FROM first_seen fs
            JOIN platforms p ON p.code = fs.code
            JOIN voc_records v ON v.platform_id = p.id
            WHERE v.collected_at <  $2::timestamp
            GROUP BY fs.code, fs.first_collected
            ORDER BY fs.first_collected DESC, fs.code
            """,
            start, end, last24,
        )
    finally:
        await conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        voc24 = int(r["voc_24h"] or 0)
        out.append({
            "code": r["code"],
            "first_seen": r["first_seen"].isoformat(),
            "voc_total": int(r["voc_total"] or 0),
            "voc_24h": voc24,
            "active_24h": voc24 > 0,
        })
    return out


def _http_get_json(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)}


def collect_collection_status(base: str = DEFAULT_BASE) -> Dict[str, Any]:
    """active/inactive 요약 + 사이트별 health 카운트만 보존 (json 비대화 방지)."""
    data = _http_get_json(f"{base.rstrip('/')}/api/v1/_internal/collection-status?hours=24")
    if "_error" in data:
        return {"error": data["_error"]}
    summary = data.get("summary", {})
    platforms = data.get("platforms", []) or []
    health_counts: Dict[str, int] = {"active": 0, "slow": 0, "stale": 0, "dead": 0}
    for p in platforms:
        h = p.get("health")
        if h in health_counts:
            health_counts[h] += 1
    return {
        "total_active": int(summary.get("total_active", 0)),
        "total_inactive": int(summary.get("total_inactive", 0)),
        "total_records_24h": int(summary.get("total_records_24h", 0)),
        "health_counts": health_counts,
    }


def collect_alert_trends(base: str = DEFAULT_BASE, days: int = 7) -> Dict[str, Any]:
    data = _http_get_json(f"{base.rstrip('/')}/api/v1/_internal/alert-trends?days={days}")
    if "_error" in data:
        return {"error": data["_error"]}
    rules = data.get("rules", []) or []
    fires_window = sum(int(r.get("fires_window") or 0) for r in rules)
    fires_24h = sum(int(r.get("fires_24h") or 0) for r in rules)
    return {
        "days": int(data.get("days", days)),
        "cooldown_violations_24h": int(data.get("cooldown_violations_24h", 0)),
        "rules_total": len(rules),
        "fires_window": fires_window,
        "fires_24h": fires_24h,
        "silent_rules": sum(1 for r in rules if r.get("silent_window")),
    }


def collect_grounding_history(
    report_dir: Path,
    target: date,
    days: int = 7,
) -> Dict[str, Any]:
    """reports/insight_grounding_history.json 의 최근 days 일 score 발췌."""
    path = report_dir / "insight_grounding_history.json"
    if not path.exists():
        return {"error": "history file missing"}
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"parse failed: {e}"}
    start = target - timedelta(days=days - 1)
    window = [h for h in history
              if h.get("date")
              and start.isoformat() <= h["date"] <= target.isoformat()]
    scores = [float(h["grounding_score"]) for h in window
              if isinstance(h.get("grounding_score"), (int, float))]
    return {
        "window_days": days,
        "entries": window,
        "avg": (round(sum(scores) / len(scores), 4) if scores else None),
        "min": (round(min(scores), 4) if scores else None),
        "max": (round(max(scores), 4) if scores else None),
        "last": (round(float(window[-1]["grounding_score"]), 4) if window else None),
    }


def collect_regression(base: str = DEFAULT_BASE) -> Dict[str, Any]:
    data = _http_get_json(f"{base.rstrip('/')}/api/v1/_internal/regression-baseline")
    if "_error" in data:
        return {"error": data["_error"]}
    checks = data.get("checks", []) or []
    summary = data.get("summary", {}) or {}
    # 9 metric (8 + alembic) 핵심만 보존
    compact = [
        {
            "name": c.get("name"),
            "current": c.get("current"),
            "baseline_r8": c.get("baseline_r8"),
            "threshold": c.get("threshold"),
            "ok": bool(c.get("ok")),
        }
        for c in checks
    ]
    return {
        "checks": compact,
        "summary": {
            "total": int(summary.get("total", 0)),
            "ok": int(summary.get("ok", 0)),
            "failed": int(summary.get("failed", 0)),
        },
        "alembic_head": data.get("alembic_head"),
        "alembic_ok": bool(data.get("alembic_ok", False)),
    }


# ── 알림 룰 평가 ──────────────────────────────────────────────────────────
def evaluate_alerts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """5종 룰을 평가하여 발생한 알림만 반환.

    voc / sentiment 룰은 "어제 vs 그제" 만 비교. 오늘(target_date) 은 진행 중이라
    값이 작아 false positive 의 원인이 되므로 제외한다.
    """
    alerts: List[Dict[str, Any]] = []
    voc_full = payload.get("voc_daily", []) or []
    today = payload.get("target_date")
    voc = [v for v in voc_full if v.get("day") != today]

    # 1. voc 50%+ 감소 (어제 vs 그제)
    if len(voc) >= 2:
        prev, last = voc[-2], voc[-1]
        prev_c = int(prev.get("voc_count", 0))
        last_c = int(last.get("voc_count", 0))
        if prev_c > 0:
            drop_pct = (prev_c - last_c) / prev_c * 100.0
            if drop_pct >= THRESH_VOC_DROP_PCT:
                alerts.append({
                    "rule": "voc_daily_drop_50pct",
                    "severity": "warning",
                    "message": f"voc {prev['day']}={prev_c} → {last['day']}={last_c} "
                               f"({drop_pct:.1f}% 감소)",
                })

    # 2. sentiment 0.2+ shift
    if len(voc) >= 2:
        prev, last = voc[-2], voc[-1]
        ps = prev.get("sentiment_avg")
        ls = last.get("sentiment_avg")
        if isinstance(ps, (int, float)) and isinstance(ls, (int, float)):
            shift = abs(float(ls) - float(ps))
            if shift >= THRESH_SENTIMENT_SHIFT:
                alerts.append({
                    "rule": "sentiment_shift_0p2",
                    "severity": "warning",
                    "message": f"sentiment {prev['day']}={ps:.3f} → "
                               f"{last['day']}={ls:.3f} (|Δ|={shift:.3f})",
                })

    # 3. active < 15
    cs = payload.get("collection_status", {}) or {}
    active = int(cs.get("total_active", 0))
    if cs and "error" not in cs and active > 0 and active < THRESH_SITES_ACTIVE_MIN:
        alerts.append({
            "rule": "sites_active_below_15",
            "severity": "critical",
            "message": f"active 사이트 {active} < {THRESH_SITES_ACTIVE_MIN}",
        })

    # 4. grounding < 0.4
    gr = payload.get("grounding", {}) or {}
    last_g = gr.get("last")
    if isinstance(last_g, (int, float)) and last_g < THRESH_GROUNDING_MIN:
        alerts.append({
            "rule": "llm_grounding_below_0p4",
            "severity": "warning",
            "message": f"grounding last={last_g:.3f} < {THRESH_GROUNDING_MIN}",
        })

    # 5. regression 실패
    reg = payload.get("regression", {}) or {}
    failed = int((reg.get("summary") or {}).get("failed", 0))
    if failed > 0:
        names = [c.get("name") for c in (reg.get("checks") or []) if not c.get("ok")]
        if not reg.get("alembic_ok", True):
            names.append("alembic_head")
        alerts.append({
            "rule": "regression_failed",
            "severity": "critical",
            "message": f"regression failed={failed} ({', '.join(names) or '?'})",
        })

    return alerts


# ── Markdown 보고서 (운영자용 일별) ──────────────────────────────────────
def _classify_site_state(voc_count: int) -> str:
    """단일 일별 voc_count → 정상/저조/죽음 라벨.

    임계는 collection_trend.py 의 일평균 정책을 일별로 환산::
      ≥ 50  → 정상
      1~49  → 저조
      0     → 죽음
    """
    if voc_count >= 50:
        return "정상"
    if voc_count >= 1:
        return "저조"
    return "죽음"


def render_markdown_report(payload: Dict[str, Any]) -> str:
    """payload → 운영자용 일별 markdown.

    구조::
        1. 헤더 (target_date / days / iso_year_week / alerts 개수)
        2. 7일 추세 (voc + sentiment + 일별 상태 자동 분류)
        3. 사이트 상태 요약 (active/inactive/health_counts)
        4. 알림 발화 / regression / grounding 요약
        5. 신규 사이트 진척 (active_24h 표시)
        6. 이상치 자동 탐지 (payload["alerts"])
    """
    lines: List[str] = []
    target = payload.get("target_date") or ""
    days = int(payload.get("window_days") or 7)
    iso = payload.get("iso_year_week") or ""
    alerts = payload.get("alerts") or []
    voc_daily = payload.get("voc_daily") or []
    cs = payload.get("collection_status") or {}
    at = payload.get("alert_trends") or {}
    gr = payload.get("grounding") or {}
    reg = payload.get("regression") or {}
    new_sites = payload.get("new_sites") or []

    # 1. 헤더
    lines.append(f"# 운영 1주 모니터 — {target} ({days}d)")
    lines.append("")
    lines.append(f"- 생성: `{payload.get('generated_at')}`")
    lines.append(f"- 주차: `{iso}`")
    lines.append(f"- 알림: **{len(alerts)}** 건")
    lines.append("")

    # 2. 7일 추세
    lines.append("## 1. 7일 추세")
    lines.append("")
    lines.append("| 날짜 | voc | sentiment_avg | 상태 |")
    lines.append("|---|---|---|---|")
    for v in voc_daily:
        cnt = int(v.get("voc_count", 0))
        sent = v.get("sentiment_avg")
        sent_str = f"{sent:.3f}" if isinstance(sent, (int, float)) else "—"
        state = _classify_site_state(cnt)
        lines.append(f"| {v.get('day')} | {cnt:,} | {sent_str} | {state} |")
    lines.append("")

    # 3. 사이트 상태 요약
    lines.append("## 2. 사이트 상태")
    lines.append("")
    if "error" in cs:
        lines.append(f"- 조회 실패: `{cs['error']}`")
    else:
        hc = cs.get("health_counts") or {}
        lines.append(f"- active: **{cs.get('total_active', 0)}** / inactive: {cs.get('total_inactive', 0)}")
        lines.append(f"- 24h 총 voc: **{cs.get('total_records_24h', 0):,}**")
        lines.append(
            f"- health: active={hc.get('active', 0)} slow={hc.get('slow', 0)} "
            f"stale={hc.get('stale', 0)} dead={hc.get('dead', 0)}"
        )
    lines.append("")

    # 4. 알림 / regression / grounding
    lines.append("## 3. 알림·LLM·회귀")
    lines.append("")
    if "error" in at:
        lines.append(f"- 알림 트렌드 조회 실패: `{at['error']}`")
    else:
        lines.append(
            f"- 알림 발화: window={at.get('fires_window', 0)} "
            f"/ 24h={at.get('fires_24h', 0)} / silent_rules={at.get('silent_rules', 0)}"
        )
        lines.append(f"- cooldown_violations_24h: {at.get('cooldown_violations_24h', 0)}")
    if "error" in gr:
        lines.append(f"- grounding 조회 실패: `{gr['error']}`")
    else:
        last_g = gr.get("last")
        avg_g = gr.get("avg")
        last_str = f"{last_g:.3f}" if isinstance(last_g, (int, float)) else "—"
        avg_str = f"{avg_g:.3f}" if isinstance(avg_g, (int, float)) else "—"
        lines.append(f"- LLM grounding: last={last_str} / avg7d={avg_str}")
    if "error" in reg:
        lines.append(f"- regression 조회 실패: `{reg['error']}`")
    else:
        summary = reg.get("summary") or {}
        lines.append(
            f"- regression: ok={summary.get('ok', 0)}/{summary.get('total', 0)} "
            f"(failed={summary.get('failed', 0)}) "
            f"alembic={'OK' if reg.get('alembic_ok') else 'FAIL'}"
        )
    lines.append("")

    # 5. 신규 사이트 진척
    lines.append("## 4. 신규 사이트 진척 (14d)")
    lines.append("")
    if not new_sites:
        lines.append("- 신규 진입 사이트 없음")
    else:
        lines.append("| code | first_seen | voc_total | voc_24h | active_24h |")
        lines.append("|---|---|---|---|---|")
        for s in new_sites:
            mark = "yes" if s.get("active_24h") else "no"
            lines.append(
                f"| {s.get('code')} | {s.get('first_seen')} | "
                f"{int(s.get('voc_total', 0)):,} | {int(s.get('voc_24h', 0)):,} | {mark} |"
            )
    lines.append("")

    # 6. 이상치 자동 탐지
    lines.append("## 5. 이상치 자동 탐지")
    lines.append("")
    if not alerts:
        lines.append("- 발화 없음 — 5종 룰 모두 통과")
    else:
        lines.append("| rule | severity | message |")
        lines.append("|---|---|---|")
        for a in alerts:
            lines.append(
                f"| {a.get('rule')} | {a.get('severity')} | {a.get('message')} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


# ── Slack 다이제스트 (1단 요약 + URL) ─────────────────────────────────────
def _slack_webhook_url() -> str:
    """ALERT_WEBHOOK_URL 우선, SLACK_WEBHOOK_URL fallback (둘 다 trim).

    slack_notifier 와 동일 정책 → 운영자가 키를 한 곳에만 두면 모두 활성.
    """
    url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    if url:
        return url
    return (os.getenv("SLACK_WEBHOOK_URL") or "").strip()


def _dashboard_url() -> str:
    """다이제스트에 첨부할 운영 대시보드 URL.

    SIGNALFORGE_DASHBOARD_URL 환경 변수 미설정 시 로컬 frontend (3000) 기본값.
    """
    return (os.getenv("SIGNALFORGE_DASHBOARD_URL") or "http://localhost:3000").rstrip("/")


def build_slack_digest_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """1단 요약 + URL 만 담은 Slack incoming webhook payload.

    block kit 헤더 + section (key metric) + context (URL 링크 포함).
    """
    target = payload.get("target_date") or "?"
    alerts = payload.get("alerts") or []
    voc_daily = payload.get("voc_daily") or []
    last_voc = voc_daily[-1] if voc_daily else {}
    cs = payload.get("collection_status") or {}
    reg = (payload.get("regression") or {}).get("summary") or {}
    new_sites = payload.get("new_sites") or []
    new_active = sum(1 for s in new_sites if s.get("active_24h"))

    summary = (
        f"voc24h={last_voc.get('voc_count', 0):,} • "
        f"active={cs.get('total_active', '?')} • "
        f"alerts={len(alerts)} • "
        f"new_sites_active={new_active}/{len(new_sites)} • "
        f"regression {reg.get('ok', 0)}/{reg.get('total', 0)}"
    )
    dash = _dashboard_url()
    severity = "critical" if any(a.get("severity") == "critical" for a in alerts) else (
        "warning" if alerts else "info"
    )
    color = {"critical": "#d72631", "warning": "#f4b400", "info": "#1f77b4"}[severity]

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"[SignalForge] 1주 모니터 {target}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"<{dash}|대시보드 열기> • iso_week=`{payload.get('iso_year_week')}`"},
        ]},
    ]
    text = f"[SignalForge][weekly-monitor] {target} — {summary}"
    return {"text": text, "attachments": [{"color": color, "blocks": blocks}]}


def post_slack_digest(
    payload: Dict[str, Any],
    *,
    force_skip: bool = False,
    timeout_sec: float = 5.0,
    _opener: Optional[Any] = None,
) -> Dict[str, Any]:
    """webhook 으로 1단 다이제스트 POST.

    Parameters
    ----------
    force_skip:
        True 면 키 유무와 무관하게 송출하지 않음 (CLI ``--no-slack``).
    _opener:
        urllib.request.urlopen 호환 callable.  단위 테스트에서 mock 주입 용도.

    Returns
    -------
    ``{"status": "sent" | "skipped" | "failed", "reason": str, "http_status": int|None}``

    동작 정책
    ---------
    - 키 미입력 / force_skip → ``status="skipped"`` (graceful)
    - HTTP 200~299           → ``status="sent"``
    - 그 외 / 예외            → ``status="failed"`` (raise 안함)
    """
    if force_skip:
        return {"status": "skipped", "reason": "force_skip", "http_status": None}
    url = _slack_webhook_url()
    if not url:
        return {"status": "skipped", "reason": "no webhook", "http_status": None}
    digest = build_slack_digest_payload(payload)
    data = json.dumps(digest, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = _opener if _opener is not None else urllib.request.urlopen
    try:
        with opener(req, timeout=timeout_sec) as resp:
            code = int(getattr(resp, "status", getattr(resp, "code", 0)) or 0)
            if 200 <= code < 300:
                return {"status": "sent", "reason": f"HTTP {code}", "http_status": code}
            return {"status": "failed", "reason": f"HTTP {code}", "http_status": code}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "reason": f"HTTPError {e.code}", "http_status": int(e.code)}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "reason": str(e), "http_status": None}


# ── 메인 실행 ─────────────────────────────────────────────────────────────
async def build_payload(
    target: Optional[date] = None,
    days: int = 7,
    base: str = DEFAULT_BASE,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Dict[str, Any]:
    target = target or datetime.now(timezone.utc).date()
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "window_days": days,
        "iso_year_week": f"{target.isocalendar().year}-{target.isocalendar().week:02d}",
    }
    payload["voc_daily"] = await collect_voc_daily(days, target)
    payload["collection_status"] = collect_collection_status(base)
    payload["alert_trends"] = collect_alert_trends(base, days=days)
    payload["grounding"] = collect_grounding_history(report_dir, target, days=days)
    payload["regression"] = collect_regression(base)
    payload["new_sites"] = await collect_new_site_progress(target, lookback_days=14)
    payload["alerts"] = evaluate_alerts(payload)
    return payload


def save_payload(
    payload: Dict[str, Any],
    report_dir: Path = DEFAULT_REPORT_DIR,
    *,
    write_md: bool = True,
) -> Dict[str, Path]:
    """JSON (주차 누적) + MD (일별 운영자용) 저장.

    Returns
    -------
    ``{"json": Path, "md": Path | None}``
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    iso_week = payload.get("iso_year_week", "0000-00")
    json_path = report_dir / f"weekly_monitor_{iso_week}.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path: Optional[Path] = None
    if write_md:
        target_str = payload.get("target_date") or date.today().isoformat()
        md_path = report_dir / f"weekly_monitor_{target_str}.md"
        md_path.write_text(render_markdown_report(payload), encoding="utf-8")
    return {"json": json_path, "md": md_path}


async def run(
    target: Optional[date] = None,
    days: int = 7,
    base: str = DEFAULT_BASE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    *,
    slack: bool = True,
) -> Dict[str, Any]:
    """1회 실행 — payload 구축 → JSON+MD 저장 → Slack 다이제스트 (옵션).

    반환::
        {
            "json_path": Path,
            "md_path": Path | None,
            "alerts": int,
            "slack": {"status": "sent|skipped|failed", ...},
        }
    """
    payload = await build_payload(target=target, days=days, base=base, report_dir=report_dir)
    paths = save_payload(payload, report_dir=report_dir)
    slack_result = post_slack_digest(payload, force_skip=not slack)
    logger.info(
        "[weekly-monitor] saved json=%s md=%s, alerts=%d, slack=%s",
        paths["json"], paths.get("md"),
        len(payload.get("alerts", [])),
        slack_result.get("status"),
    )
    return {
        "json_path": paths["json"],
        "md_path": paths.get("md"),
        "alerts": len(payload.get("alerts", [])),
        "slack": slack_result,
    }


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="weekly_monitor")
    p.add_argument("target_date", nargs="?", help="YYYY-MM-DD (기본 오늘 UTC)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--no-slack", action="store_true", help="Slack 다이제스트 강제 비활성화")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    target = (datetime.fromisoformat(args.target_date).date()
              if args.target_date else datetime.now(timezone.utc).date())
    result = asyncio.run(run(
        target=target, days=args.days, base=args.base, slack=not args.no_slack,
    ))
    print(
        f"[weekly-monitor] saved json={result['json_path']} md={result['md_path']} "
        f"alerts={result['alerts']} slack={result['slack'].get('status')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
