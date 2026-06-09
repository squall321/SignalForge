"""
운영 품질 일일 보고 (Track E)
================================

매일 09:30 KST (00:30 UTC, daily_insight 직후) Celery beat 가 호출.
한 장의 reports/quality_YYYY-MM-DD.md 에 다음 4개 신호를 묶어 보고한다:

  1. Redis 캐시 hit/miss/ratio        — backend /_internal/cache-stats
  2. LLM grounding 점수 24h 분포        — reports/insight_YYYY-MM-DD.md 파싱
                                          (footer: "_LLM grounding score: 0.42_")
  3. 39 endpoint p95 / 200ms 충족 비율   — tests/qa/perf_check 의 fast 모드 재사용
                                          (runs=10 으로 축소 → 1분 내)
  4. 머티리얼라이즈드 뷰 신선도         — pg_stat_user_tables / pg_class

LLM provider 가 있으면 한국어 1문단 요약을 헤더에 덧붙이고, 없으면 raw 표만.

CLI:
    python -m insight.quality_report             # 오늘 (UTC) 보고
    python -m insight.quality_report 2026-06-02  # 명시 일자
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 경로/환경 ────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

DEFAULT_BASE = os.getenv("SIGNALFORGE_API", "http://127.0.0.1:8000")

# 모니터링할 MV — 신선도(분 단위 지연) 체크 대상
WATCHED_MVS = (
    "mv_voc_daily",
    "category_daily",
    "kg_edges_daily",
    "platform_health",
    "country_daily",
)

# 일자 sentinel — insight_*.md footer 파싱용 정규식
_GROUNDING_RE = re.compile(r"grounding score:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
# used_tier 추출 — footer 예: "(provider: openai, used_tier: fast)"
_USED_TIER_RE = re.compile(r"used_tier:\s*([a-zA-Z_-]+)")
_PROVIDER_RE = re.compile(r"provider:\s*([a-zA-Z_-]+)")


# ── 자료구조 ─────────────────────────────────────────────────────────────
@dataclass
class CacheStats:
    enabled: bool = False
    hits: int = 0
    misses: int = 0
    ratio: Optional[float] = None
    error: Optional[str] = None


@dataclass
class GroundingStats:
    days_inspected: int = 0
    scores: List[float] = field(default_factory=list)
    avg: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    files: List[str] = field(default_factory=list)
    # P3.7: (used_tier, score) 짝 — tier 분포 표 작성용
    tier_score_pairs: List[Tuple[str, float]] = field(default_factory=list)


@dataclass
class LLMTierStatus:
    """LLM tier 가용성 스냅샷.

    high_available = ANTHROPIC_API_KEY 또는 sk- 로 시작하는 OPENAI_API_KEY 존재.
    last_high_used_at = 최근 7일 insight 보고서 중 used_tier=high 가 있던 가장 최근 날짜.
    """
    high_available: bool = False
    has_anthropic_key: bool = False
    has_openai_sk_key: bool = False
    last_high_used_date: Optional[str] = None
    last_fast_used_date: Optional[str] = None


@dataclass
class AlertOpsStats:
    """알림 운영 상태 — backend /_internal/alert-trends 스냅샷."""
    days: int = 7
    cooldown_violations_24h: int = 0
    rules: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AlertMonitorStats:
    """알림 운영 모니터링 스냅샷 — backend /_internal/alert-monitor.

    `AlertOpsStats` 의 상위 집계:
      - summary: active_rules / fires_24h / fires_7d / cooldown_violations_24h
      - rules[]: health 분류 포함 (normal/silent/noisy/violating)
      - metric_distribution: metric_path 별 p50/p90/p95/p99 + 현재 값
      - recommendations: 운영자용 권고 문장 리스트
    """
    days: int = 7
    summary: Dict[str, int] = field(default_factory=dict)
    rules: List[Dict[str, Any]] = field(default_factory=list)
    metric_distribution: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class MVStats:
    name: str
    last_refresh: Optional[datetime] = None
    age_minutes: Optional[float] = None
    error: Optional[str] = None


@dataclass
class PerfStats:
    endpoints: int = 0
    p95_under_200ms: int = 0
    over_threshold: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    report_path: Optional[Path] = None


@dataclass
class QualityReport:
    target_date: date
    cache: CacheStats
    grounding: GroundingStats
    mvs: List[MVStats]
    perf: PerfStats
    llm_tier: LLMTierStatus = field(default_factory=LLMTierStatus)
    alert_ops: AlertOpsStats = field(default_factory=AlertOpsStats)
    alert_monitor: AlertMonitorStats = field(default_factory=AlertMonitorStats)
    llm_summary: Optional[str] = None


# ── 수집 함수 (각 함수는 독립적으로 실패해도 다른 신호를 막지 않음) ───────
def collect_cache_stats(base: str = DEFAULT_BASE, timeout: float = 5.0) -> CacheStats:
    """backend /_internal/cache-stats 호출."""
    url = f"{base.rstrip('/')}/api/v1/_internal/cache-stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return CacheStats(error=str(e))
    return CacheStats(
        enabled=bool(data.get("enabled", False)),
        hits=int(data.get("hits", 0) or 0),
        misses=int(data.get("misses", 0) or 0),
        ratio=(float(data["ratio"]) if data.get("ratio") is not None else None),
    )


def collect_grounding_stats(
    report_dir: Path,
    target: date,
    *,
    window_days: int = 1,
) -> GroundingStats:
    """insight_YYYY-MM-DD.md footer 의 grounding 점수 + used_tier 파싱.

    window_days=1 → 어제 1일, =7 → 최근 7일.
    """
    stats = GroundingStats()
    for back in range(window_days):
        d = target - timedelta(days=back)
        path = report_dir / f"insight_{d.isoformat()}.md"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        m = _GROUNDING_RE.search(text)
        score: Optional[float] = None
        if m:
            try:
                score = float(m.group(1))
                stats.scores.append(score)
                stats.files.append(path.name)
            except ValueError:
                score = None
        # tier (P3.7 이후 footer 만 있음; 그 이전 보고서는 'unknown')
        tm = _USED_TIER_RE.search(text)
        tier = tm.group(1) if tm else "unknown"
        if score is not None:
            stats.tier_score_pairs.append((tier, score))
        stats.days_inspected += 1

    if stats.scores:
        stats.avg = round(statistics.mean(stats.scores), 3)
        stats.minimum = round(min(stats.scores), 3)
        stats.maximum = round(max(stats.scores), 3)
    return stats


def collect_llm_tier_status(
    report_dir: Path,
    target: date,
    *,
    window_days: int = 7,
) -> LLMTierStatus:
    """high tier 가용 여부(env) + 최근 N일 insight 의 used_tier 마지막 사용 일자."""
    has_anth = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    oai_key = os.getenv("OPENAI_API_KEY", "").strip()
    has_oai_sk = oai_key.startswith("sk-")
    status = LLMTierStatus(
        high_available=(has_anth or has_oai_sk),
        has_anthropic_key=has_anth,
        has_openai_sk_key=has_oai_sk,
    )
    for back in range(window_days):
        d = target - timedelta(days=back)
        path = report_dir / f"insight_{d.isoformat()}.md"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        tm = _USED_TIER_RE.search(text)
        if not tm:
            continue
        tier = tm.group(1).lower()
        iso = d.isoformat()
        if tier == "high" and status.last_high_used_date is None:
            status.last_high_used_date = iso
        elif tier == "fast" and status.last_fast_used_date is None:
            status.last_fast_used_date = iso
    return status


def collect_alert_ops(
    base: str = DEFAULT_BASE, *, days: int = 7, timeout: float = 5.0,
) -> AlertOpsStats:
    """backend /_internal/alert-trends 호출 — 활성 룰 발화 추이 + cooldown 위반."""
    url = f"{base.rstrip('/')}/api/v1/_internal/alert-trends?days={int(days)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return AlertOpsStats(days=days, error=str(e))
    return AlertOpsStats(
        days=int(data.get("days", days)),
        cooldown_violations_24h=int(data.get("cooldown_violations_24h", 0) or 0),
        rules=list(data.get("rules", []) or []),
    )


def collect_alert_monitor(
    base: str = DEFAULT_BASE, *, days: int = 7, timeout: float = 8.0,
) -> AlertMonitorStats:
    """backend /_internal/alert-monitor 호출 — health 판정 + 권고 자동 생성."""
    url = f"{base.rstrip('/')}/api/v1/_internal/alert-monitor?days={int(days)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return AlertMonitorStats(days=days, error=str(e))
    return AlertMonitorStats(
        days=int(data.get("days", days)),
        summary=dict(data.get("summary", {}) or {}),
        rules=list(data.get("rules", []) or []),
        metric_distribution=dict(data.get("metric_distribution", {}) or {}),
        recommendations=list(data.get("recommendations", []) or []),
    )


def collect_mv_stats(mvs: Tuple[str, ...] = WATCHED_MVS) -> List[MVStats]:
    """pg_stat_all_tables 의 last_vacuum/last_analyze 대신 pg_stat_user_tables 사용.

    MV refresh 시각 자체는 표준 카탈로그에 직접 노출되지 않으므로
    pg_stat_user_tables.last_analyze (REFRESH 직후 갱신) 와 stats_reset 을 사용한다.
    실패 시 error 만 채우고 다른 MV 는 계속.
    """
    out: List[MVStats] = []
    psql = [
        "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.getenv("POSTGRES_PORT", "5434"),
        "-U", os.getenv("POSTGRES_USER", "signalforge"),
        "-d", os.getenv("POSTGRES_DB", "signalforge"),
        "-t", "-A", "-F", "|", "-v", "ON_ERROR_STOP=1",
    ]
    env = {**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")}
    for mv in mvs:
        # MV last refresh — pg_stat_user_tables.last_analyze 로 근사
        sql = (
            "SELECT GREATEST(COALESCE(last_analyze,'epoch'),"
            " COALESCE(last_autoanalyze,'epoch'),"
            " COALESCE(last_vacuum,'epoch')) AS ts "
            f"FROM pg_stat_user_tables WHERE relname='{mv}';"
        )
        try:
            res = subprocess.run(
                psql + ["-c", sql],
                env=env, capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            out.append(MVStats(name=mv, error="psql timeout"))
            continue
        if res.returncode != 0:
            out.append(MVStats(name=mv, error=res.stderr.strip()[:200]))
            continue
        raw = res.stdout.strip()
        if not raw:
            out.append(MVStats(name=mv, error="not found"))
            continue
        try:
            # 예: "2026-06-02 00:30:00.123456+00"
            ts = _parse_pg_ts(raw)
            now = datetime.now(timezone.utc)
            age_min = (now - ts).total_seconds() / 60.0 if ts else None
            out.append(MVStats(name=mv, last_refresh=ts, age_minutes=age_min))
        except Exception as e:
            out.append(MVStats(name=mv, error=f"parse: {e}"))
    return out


def _parse_pg_ts(raw: str) -> Optional[datetime]:
    """psql -t -A 가 뱉는 timestamptz 문자열 파싱."""
    raw = raw.strip()
    if not raw or raw == "epoch":
        return None
    # postgres "2026-06-02 00:30:00+00" → ISO 처럼 정규화
    s = raw.replace(" ", "T")
    # +00 → +00:00
    s = re.sub(r"([+-]\d{2})$", r"\1:00", s)
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def collect_perf_stats(
    base: str,
    out_dir: Path,
    *,
    runs: int = 10,
    threshold_ms: float = 200.0,
) -> PerfStats:
    """tests.qa.perf_check 재사용 (fast 모드 runs=10).

    backend 미기동 등 외부 에러 시 error 만 채우고 perf 신호 생략.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tests.qa.perf_check import run as _perf_run, _discover_product  # type: ignore
    except Exception as e:
        return PerfStats(error=f"perf_check import 실패: {e}")

    try:
        product = _discover_product(base, fallback="GS25U")
        report_path = _perf_run(
            base=base, runs=runs,
            product=product, products_csv=f"{product},{product}",
            out_dir=out_dir, timeout=30.0,
        )
    except Exception as e:
        return PerfStats(error=f"perf 실행 실패: {e}")

    # 생성된 perf_YYYY-MM-DD.md 를 파싱해 p95 SLO 충족 endpoint 수 추출
    over: List[Dict[str, Any]] = []
    endpoints = 0
    try:
        text = report_path.read_text(encoding="utf-8")
        # 표 행: | `name` | OK | p50 | p95 | ...
        row_re = re.compile(
            r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
        )
        for line in text.splitlines():
            m = row_re.match(line)
            if not m:
                continue
            endpoints += 1
            name, ok, _p50, p95 = m.group(1), int(m.group(2)), float(m.group(3)), float(m.group(4))
            if ok > 0 and p95 > threshold_ms:
                over.append({"endpoint": name, "p95": p95})
    except Exception as e:
        return PerfStats(error=f"perf 파싱 실패: {e}", report_path=report_path)

    return PerfStats(
        endpoints=endpoints,
        p95_under_200ms=endpoints - len(over),
        over_threshold=over,
        report_path=report_path,
    )


# ── 보고서 렌더링 ─────────────────────────────────────────────────────────
def render_markdown(r: QualityReport) -> str:
    L: List[str] = []
    L.append(f"# SignalForge 운영 품질 일일 보고 — {r.target_date.isoformat()}")
    L.append("")
    L.append(f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    L.append("")

    # LLM 요약 (있을 때만)
    if r.llm_summary:
        L.append("## 요약 (LLM)")
        L.append("")
        L.append(r.llm_summary.strip())
        L.append("")
        L.append("---")
        L.append("")

    # 1) 캐시
    L.append("## 1. Redis 캐시")
    L.append("")
    if r.cache.error:
        L.append(f"- ⚠ 수집 실패: `{r.cache.error}`")
    elif not r.cache.enabled:
        L.append("- Redis 비활성 (cache passthrough)")
    else:
        ratio_str = f"{r.cache.ratio:.2%}" if r.cache.ratio is not None else "n/a"
        L.append(f"- hits = **{r.cache.hits:,}**")
        L.append(f"- misses = **{r.cache.misses:,}**")
        L.append(f"- ratio = **{ratio_str}**")
    L.append("")

    # 2) Grounding
    L.append("## 2. LLM grounding 점수")
    L.append("")
    g = r.grounding
    if not g.scores:
        L.append(f"- insight 보고서 {g.days_inspected}일치 확인, grounding 점수 없음")
    else:
        L.append(f"- 표본 {len(g.scores)}건 (`{', '.join(g.files)}`)")
        L.append(f"- 평균 = **{g.avg:.3f}**, min = {g.minimum:.3f}, max = {g.maximum:.3f}")
    L.append("")

    # 2-1) tier 분포 (P3.7)
    if g.tier_score_pairs:
        # tier 별 평균/카운트
        from collections import defaultdict
        by_tier: Dict[str, List[float]] = defaultdict(list)
        for tier, sc in g.tier_score_pairs:
            by_tier[tier].append(sc)
        L.append("### 2-1. tier 분포")
        L.append("")
        L.append("| tier | 표본 | 평균 grounding |")
        L.append("|---|---:|---:|")
        for tier in sorted(by_tier.keys()):
            arr = by_tier[tier]
            avg_t = sum(arr) / len(arr)
            L.append(f"| `{tier}` | {len(arr)} | {avg_t:.3f} |")
        L.append("")

    # 2-2) high tier 가용성 (P3.7)
    t = r.llm_tier
    L.append("### 2-2. high tier 가용성")
    L.append("")
    L.append(
        f"- `ANTHROPIC_API_KEY`: {'있음' if t.has_anthropic_key else '없음'}"
    )
    L.append(
        f"- `OPENAI_API_KEY` (sk-*): {'있음' if t.has_openai_sk_key else '없음'}"
    )
    L.append(
        f"- high 호출 가능: **{'예' if t.high_available else '아니오'}**"
    )
    if t.last_high_used_date:
        L.append(f"- 최근 high 사용: `{t.last_high_used_date}`")
    else:
        L.append("- 최근 high 사용: (없음 — fast 만 사용 중)")
    if t.last_fast_used_date:
        L.append(f"- 최근 fast 사용: `{t.last_fast_used_date}`")
    L.append("")

    # 3) MV 신선도
    L.append("## 3. 머티리얼라이즈드 뷰 신선도")
    L.append("")
    L.append("| MV | 마지막 갱신 (UTC) | 지연 (분) | 비고 |")
    L.append("|---|---|---:|---|")
    for mv in r.mvs:
        if mv.error:
            L.append(f"| `{mv.name}` | - | - | ⚠ {mv.error} |")
            continue
        ts_str = mv.last_refresh.isoformat(timespec="seconds") if mv.last_refresh else "-"
        age_str = f"{mv.age_minutes:.1f}" if mv.age_minutes is not None else "-"
        flag = ""
        if mv.age_minutes is not None and mv.age_minutes > 60:
            flag = "⚠ 1시간 초과"
        L.append(f"| `{mv.name}` | {ts_str} | {age_str} | {flag} |")
    L.append("")

    # 5) 알림 운영 (Track E2) — section 4 앞에 놓으면 perf 가 마지막에 빠지므로
    # 4 다음으로 둔다. 여기서는 4 직전에 추가하지 않고 4 다음에 추가.
    # 4) Endpoint p95
    L.append("## 4. Endpoint p95 (≤ 200ms 기준)")
    L.append("")
    p = r.perf
    if p.error:
        L.append(f"- ⚠ {p.error}")
    else:
        L.append(f"- 측정 endpoint: **{p.endpoints}**")
        L.append(f"- p95 ≤ 200ms 충족: **{p.p95_under_200ms} / {p.endpoints}**")
        if p.over_threshold:
            L.append(f"- 초과 endpoint ({len(p.over_threshold)}):")
            for e in p.over_threshold:
                L.append(f"  - `{e['endpoint']}` p95={e['p95']}ms")
        if p.report_path:
            L.append(f"- 원본: `{p.report_path.name}`")
    L.append("")

    # 5) 알림 운영 (Track E2)
    a = r.alert_ops
    L.append(f"## 5. 알림 운영 (활성 룰 {a.days}일 추이)")
    L.append("")
    if a.error:
        L.append(f"- ⚠ 수집 실패: `{a.error}`")
    elif not a.rules:
        L.append("- 활성 룰 0")
    else:
        L.append(
            f"- cooldown 위반 의심 (24h): **{a.cooldown_violations_24h}**"
            f"  (0 이어야 정상; 양수면 `cooldown_sec` 과대평가 또는 평가 주기 너무 짧음)"
        )
        L.append("")
        L.append("| 룰 | metric | 임계 | cooldown | 7d 발화 | 24h 발화 | avg | max | 마지막 발화 |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
        for rule in a.rules:
            avg = "-" if rule.get("avg_value") is None else f"{rule['avg_value']}"
            mx = "-" if rule.get("max_value") is None else f"{rule['max_value']}"
            last = rule.get("last_fired_at") or "-"
            L.append(
                f"| `{rule['name']}` | `{rule['metric_path']}` | "
                f"{rule['threshold']} | {rule['cooldown_sec']}s | "
                f"{rule['fires_window']} | {rule['fires_24h']} | "
                f"{avg} | {mx} | {last} |"
            )
        L.append("")
        # 임계 조정 권고 — 7일간 0건 룰은 임계 낮추기 후보
        silent_rules = [rule for rule in a.rules if rule.get("silent_window")]
        if silent_rules:
            L.append("### 5-1. 임계 조정 권고")
            L.append("")
            for rule in silent_rules:
                L.append(
                    f"- `{rule['name']}` ({rule['metric_path']}): "
                    f"{a.days}일간 0회 발화 — 임계 {rule['threshold']} 가 너무 높을 가능성. "
                    f"실측 metric 분포 점검 후 하향 조정 검토."
                )
            L.append("")
    L.append("")

    # 6) 알림 운영 모니터링 (Track A — health 분류 + metric 분포 + 권고)
    am = r.alert_monitor
    L.append(f"## 6. 알림 운영 모니터링 ({am.days}일 health 분류)")
    L.append("")
    if am.error:
        L.append(f"- ⚠ 수집 실패: `{am.error}`")
    elif not am.rules:
        L.append("- 활성 룰 0 — /alert-monitor 응답에 룰 없음")
    else:
        s = am.summary or {}
        L.append(
            f"- 활성 룰 **{s.get('active_rules', 0)}** · "
            f"24h 발화 **{s.get('fires_24h', 0)}** · "
            f"7d 발화 **{s.get('fires_7d', 0)}** · "
            f"cooldown 위반 **{s.get('cooldown_violations_24h', 0)}**"
        )
        L.append("")
        L.append("| 룰 | health | metric | 임계 | 24h | 7d | avg | max | violations |")
        L.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for rule in am.rules:
            avg = "-" if rule.get("avg_value_7d") is None else f"{rule['avg_value_7d']}"
            mx = "-" if rule.get("max_value_7d") is None else f"{rule['max_value_7d']}"
            L.append(
                f"| `{rule['name']}` | **{rule['health']}** | "
                f"`{rule['metric_path']}` | {rule['threshold']} | "
                f"{rule['fires_24h']} | {rule['fires_7d']} | "
                f"{avg} | {mx} | {rule['cooldown_violations_24h']} |"
            )
        L.append("")
        # metric 분포 표 — p50/p90/p95/p99 + 현재
        if am.metric_distribution:
            L.append("### 6-1. metric 분포 (alert_events.value 윈도우)")
            L.append("")
            L.append("| metric | n | p50 | p90 | p95 | p99 | 현재 |")
            L.append("|---|---:|---:|---:|---:|---:|---:|")
            for mp, dist in sorted(am.metric_distribution.items()):
                cur = "-" if dist.get("current") is None else f"{dist['current']}"
                p50 = "-" if dist.get("p50") is None else f"{dist['p50']}"
                p90 = "-" if dist.get("p90") is None else f"{dist['p90']}"
                p95 = "-" if dist.get("p95") is None else f"{dist['p95']}"
                p99 = "-" if dist.get("p99") is None else f"{dist['p99']}"
                L.append(
                    f"| `{mp}` | {dist.get('n', 0)} | "
                    f"{p50} | {p90} | {p95} | {p99} | {cur} |"
                )
            L.append("")
        if am.recommendations:
            L.append("### 6-2. 권고")
            L.append("")
            for rec in am.recommendations:
                L.append(f"- {rec}")
            L.append("")
    L.append("")

    return "\n".join(L)


# ── LLM 요약 (선택) ───────────────────────────────────────────────────────
def maybe_llm_summary(r: QualityReport) -> Optional[str]:
    """ANTHROPIC_API_KEY / OPENAI_API_KEY 있으면 1문단 한국어 요약 생성."""
    try:
        sys.path.insert(0, str(_CRAWLER_DIR))
        from insight.llm_provider import get_provider  # type: ignore
    except Exception:
        return None
    provider = get_provider()
    if provider is None:
        return None

    # raw 표 → JSON
    payload = {
        "date": r.target_date.isoformat(),
        "cache": {
            "enabled": r.cache.enabled,
            "hits": r.cache.hits, "misses": r.cache.misses,
            "ratio": r.cache.ratio, "error": r.cache.error,
        },
        "grounding": {
            "samples": len(r.grounding.scores),
            "avg": r.grounding.avg,
            "min": r.grounding.minimum,
            "max": r.grounding.maximum,
        },
        "mv": [
            {"name": m.name,
             "age_minutes": m.age_minutes,
             "error": m.error}
            for m in r.mvs
        ],
        "perf": {
            "endpoints": r.perf.endpoints,
            "p95_ok": r.perf.p95_under_200ms,
            "over": r.perf.over_threshold[:5],
            "error": r.perf.error,
        },
        "alert_ops": {
            "days": r.alert_ops.days,
            "cooldown_violations_24h": r.alert_ops.cooldown_violations_24h,
            "rules": [
                {
                    "name": rule.get("name"),
                    "fires_window": rule.get("fires_window"),
                    "fires_24h": rule.get("fires_24h"),
                    "threshold": rule.get("threshold"),
                    "silent": rule.get("silent_window"),
                }
                for rule in r.alert_ops.rules
            ],
            "error": r.alert_ops.error,
        },
        "alert_monitor": {
            "summary": r.alert_monitor.summary,
            "rules_health": [
                {"name": rule.get("name"), "health": rule.get("health")}
                for rule in r.alert_monitor.rules
            ],
            "recommendations": r.alert_monitor.recommendations[:6],
            "error": r.alert_monitor.error,
        },
    }
    schema = (
        "SignalForge 운영 품질 일일 보고 — Redis 캐시 hit/miss, LLM grounding "
        "점수, 5개 MV 신선도(분), endpoint p95 SLO(200ms) 충족률을 포함."
    )
    instr = (
        "위 JSON 을 근거로 한국어로 3-4문장 짜리 운영 요약을 작성하세요.\n"
        "1) 캐시 히트율 / 응답 SLO / grounding 추세를 수치로 명시.\n"
        "2) 60분을 넘긴 MV 가 있으면 우선 경보.\n"
        "3) 문제 없으면 '모든 지표 정상' 명시.\n"
        "4) 추측·의견 금지, 표의 수치만 인용."
    )
    try:
        return provider.summarize_json(payload, schema, instr)
    except Exception as e:
        logger.warning("LLM summary 실패: %s", e)
        return None


# ── 엔트리 ────────────────────────────────────────────────────────────────
def run(
    target: Optional[date] = None,
    *,
    base: str = DEFAULT_BASE,
    report_dir: Optional[Path] = None,
    perf_runs: int = 10,
    skip_perf: bool = False,
    skip_llm: bool = False,
) -> Path:
    if target is None:
        target = datetime.now(timezone.utc).date()
    if report_dir is None:
        report_dir = Path(os.getenv("REPORT_DIR", str(DEFAULT_REPORT_DIR)))
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[quality_report] 수집 시작 base=%s target=%s", base, target)
    cache = collect_cache_stats(base)
    # insight_*.md 는 어제 데이터 기준.  tier 분포는 7일 윈도우 (P3.7).
    grounding = collect_grounding_stats(
        report_dir, target - timedelta(days=1), window_days=7,
    )
    tier_status = collect_llm_tier_status(
        report_dir, target - timedelta(days=1), window_days=7,
    )
    mvs = collect_mv_stats()
    alert_ops = collect_alert_ops(base, days=7)
    alert_monitor = collect_alert_monitor(base, days=7)
    if skip_perf:
        perf = PerfStats(error="skip_perf=True")
    else:
        perf = collect_perf_stats(base, report_dir, runs=perf_runs)

    report = QualityReport(
        target_date=target,
        cache=cache,
        grounding=grounding,
        mvs=mvs,
        perf=perf,
        llm_tier=tier_status,
        alert_ops=alert_ops,
        alert_monitor=alert_monitor,
    )
    if not skip_llm:
        report.llm_summary = maybe_llm_summary(report)

    md = render_markdown(report)
    out_path = report_dir / f"quality_{target.isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("[quality_report] 저장: %s", out_path)
    return out_path


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    ap = argparse.ArgumentParser(description="SignalForge 운영 품질 일일 보고")
    ap.add_argument("date", nargs="?", help="대상 일자 (기본=오늘 UTC)")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--perf-runs", type=int, default=10)
    ap.add_argument("--skip-perf", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    args = ap.parse_args(argv)

    target = _parse_date(args.date) if args.date else datetime.now(timezone.utc).date()
    out = run(
        target=target, base=args.base,
        perf_runs=args.perf_runs,
        skip_perf=args.skip_perf, skip_llm=args.skip_llm,
    )
    print(f"OK: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
