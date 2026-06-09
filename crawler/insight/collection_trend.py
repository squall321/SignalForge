"""수집 7일 누적 트렌드 분석 (Track F).

목적
----
``collection_health.py`` 가 *시점(현재 24h)* 의 위반을 평가한다면, 이 모듈은
*기간(최근 N일)* 의 사이트별 수집 추이를 누적·분석한다.

산출
----
* 사이트별 일별 voc 추이 (matrix: 사이트 × 날짜)
* 사이트별 통계 (total, mean_per_day, stddev, max, min)
* 변동 큰 사이트 자동 식별 — 변동계수(CV = stddev/mean) ≥ 1.0 또는
  최근 절반 / 직전 절반 비율이 ±50% 이상 변한 사이트
* 전체 일별 총량 series

CLI::

    python -m insight.collection_trend                 # 7일 (기본) stdout
    python -m insight.collection_trend --days 14
    python -m insight.collection_trend --json out.json # 파일 저장
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

# ── 변동 식별 임계 ───────────────────────────────────────────────────────
# 변동계수 (stddev / mean) — 높을수록 들쭉날쭉
THRESH_CV_VOLATILE = 1.0
# 후반 절반 vs 전반 절반 비율 변화 (절대값) — 0.5 = 50% 변동
THRESH_HALF_RATIO_DELTA = 0.5
# 분석 대상 최소 평균 — 평균이 너무 작으면 (잡음) 변동 후보에서 제외
MIN_MEAN_FOR_VOLATILITY = 1.0

# ── 사이트 상태 자동 분류 임계 (v2) ───────────────────────────────────────
# 운영 표준: 7일 평균을 기준으로
#   healthy     : 일평균 ≥ 50  (정상)
#   moderate    : 일평균 10~50 (보통, 모니터 대상)
#   low         : 일평균 1~10  (저조, 사이트별 점검 필요)
#   dying       : 일평균 < 1 그러나 첫 절반엔 수집이 있었음 (감쇠)
#   dead        : 전 기간 0건 (수집 중단)
CLASS_HEALTHY_MIN   = 50.0
CLASS_MODERATE_MIN  = 10.0
CLASS_LOW_MIN       = 1.0


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


# ── DB: 사이트별 일별 카운트 ─────────────────────────────────────────────
async def fetch_daily_matrix(
    conn: asyncpg.Connection,
    days: int,
) -> Dict[str, Any]:
    """활성 사이트별 일별 voc 카운트 매트릭스.

    Returns
    -------
    ``{"dates": ["YYYY-MM-DD", ...],          # 오래된 → 최신, 총 days 개
        "matrix": {code: [n_day0, n_day1, ...]}}``
    """
    end = datetime.now(timezone.utc).date() + timedelta(days=1)   # exclusive
    start = end - timedelta(days=days)

    rows = await conn.fetch(
        """
        SELECT p.code,
               date_trunc('day', v.collected_at)::date AS day,
               count(*) AS n
        FROM platforms p
        JOIN voc_records v ON v.platform_id = p.id
        WHERE p.is_active = TRUE
          AND v.collected_at >= $1::timestamp
          AND v.collected_at <  $2::timestamp
        GROUP BY p.code, day
        ORDER BY p.code, day
        """,
        start, end,
    )
    # platforms 전체 (수집 0건 사이트도 포함)
    code_rows = await conn.fetch(
        "SELECT code FROM platforms WHERE is_active = TRUE ORDER BY code"
    )
    codes = [r["code"] for r in code_rows]

    # 날짜 인덱스
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    idx_of = {d: i for i, d in enumerate(dates)}

    matrix: Dict[str, List[int]] = {c: [0] * days for c in codes}
    for r in rows:
        code = r["code"]
        d_str = r["day"].isoformat()
        if code in matrix and d_str in idx_of:
            matrix[code][idx_of[d_str]] = int(r["n"] or 0)
    return {"dates": dates, "matrix": matrix}


# ── 사이트별 통계 ─────────────────────────────────────────────────────────
def compute_site_stats(matrix: Dict[str, List[int]]) -> List[Dict[str, Any]]:
    """사이트별 통계 (total, mean, stddev, max, min, cv, half_ratio_delta)."""
    out: List[Dict[str, Any]] = []
    for code, series in matrix.items():
        n = len(series)
        total = int(sum(series))
        mean = total / n if n else 0.0
        if n >= 2:
            try:
                stdev = statistics.pstdev(series)
            except statistics.StatisticsError:
                stdev = 0.0
        else:
            stdev = 0.0
        cv = (stdev / mean) if mean > 0 else 0.0
        # 전반/후반 절반 비교 (홀수면 후반 쪽이 +1)
        half = n // 2
        if half >= 1 and n - half >= 1:
            first_sum = sum(series[:half])
            second_sum = sum(series[half:])
            first_avg = first_sum / half
            second_avg = second_sum / (n - half)
            if first_avg > 0:
                half_ratio_delta = (second_avg - first_avg) / first_avg
            elif second_avg > 0:
                # 첫 절반 0, 후반 > 0 → 급증 (대형 양수)
                half_ratio_delta = float("inf")
            else:
                half_ratio_delta = 0.0
        else:
            half_ratio_delta = 0.0

        out.append({
            "code": code,
            "total": total,
            "mean_per_day": round(mean, 3),
            "stddev": round(stdev, 3),
            "cv": round(cv, 3),
            "max": int(max(series)) if series else 0,
            "min": int(min(series)) if series else 0,
            "half_ratio_delta": (
                round(half_ratio_delta, 3)
                if math.isfinite(half_ratio_delta)
                else None
            ),
            "series": series,
        })
    # 총량 큰 순서 정렬
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


# ── 변동 큰 사이트 식별 ───────────────────────────────────────────────────
def identify_volatile_sites(
    site_stats: List[Dict[str, Any]],
    cv_threshold: float = THRESH_CV_VOLATILE,
    half_delta_threshold: float = THRESH_HALF_RATIO_DELTA,
    min_mean: float = MIN_MEAN_FOR_VOLATILITY,
) -> List[Dict[str, Any]]:
    """변동 큰 사이트 = 평균이 충분하면서 CV 또는 후반 비율 변화가 임계 초과.

    종류
    ----
    * ``volatile_swing``: CV ≥ cv_threshold (들쭉날쭉)
    * ``trend_up``:       half_ratio_delta ≥ +half_delta_threshold (50% 이상 증가)
    * ``trend_down``:     half_ratio_delta ≤ -half_delta_threshold (50% 이상 감소)
    """
    out: List[Dict[str, Any]] = []
    for s in site_stats:
        mean = float(s.get("mean_per_day") or 0.0)
        if mean < min_mean:
            continue
        reasons: List[str] = []
        cv = float(s.get("cv") or 0.0)
        delta = s.get("half_ratio_delta")
        kind: Optional[str] = None
        if cv >= cv_threshold:
            reasons.append(f"CV={cv:.2f} (변동계수 ≥ {cv_threshold})")
            kind = "volatile_swing"
        if delta is not None:
            if delta >= half_delta_threshold:
                reasons.append(f"후반 +{delta*100:.0f}% (반토막 비교)")
                kind = "trend_up"
            elif delta <= -half_delta_threshold:
                reasons.append(f"후반 {delta*100:.0f}% (반토막 비교)")
                kind = "trend_down"
        if reasons:
            out.append({
                "code": s["code"],
                "kind": kind,
                "mean_per_day": s["mean_per_day"],
                "cv": s["cv"],
                "half_ratio_delta": s["half_ratio_delta"],
                "reasons": reasons,
            })
    # 변동성 큰 순서: trend_down 우선 → trend_up → volatile_swing, 다음 CV desc
    order = {"trend_down": 0, "trend_up": 1, "volatile_swing": 2}
    out.sort(key=lambda x: (order.get(x.get("kind") or "", 9), -float(x.get("cv") or 0.0)))
    return out


def daily_totals(matrix: Dict[str, List[int]], dates: List[str]) -> List[Dict[str, Any]]:
    """일별 전체 voc 총량."""
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(dates):
        total = sum(series[i] for series in matrix.values() if i < len(series))
        out.append({"date": d, "total": int(total)})
    return out


# ── v2: 사이트 상태 자동 분류 ────────────────────────────────────────────
def classify_site(stat: Dict[str, Any]) -> str:
    """단일 site_stat 행을 healthy/moderate/low/dying/dead 로 분류.

    근거
    ----
    * total == 0 → ``dead`` (분석 기간 전체 0건; 사이트 죽음)
    * mean < 1   :
        - 직전 절반에 수집이 있었고 후반 절반에서 급감 → ``dying``
        - 그 외 → ``low`` (계속 적게 수집되는 정상 저조)
    * mean ∈ [1, 10)  → ``low``
    * mean ∈ [10, 50) → ``moderate``
    * mean ≥ 50       → ``healthy``
    """
    total = int(stat.get("total") or 0)
    mean = float(stat.get("mean_per_day") or 0.0)
    series = stat.get("series") or []
    if total <= 0:
        return "dead"
    if mean < CLASS_LOW_MIN:
        # 첫 절반에 수집이 있었는데 후반 절반엔 거의 없으면 "dying"
        n = len(series)
        half = n // 2
        if half >= 1 and n - half >= 1:
            first_sum = sum(series[:half])
            second_sum = sum(series[half:])
            if first_sum > 0 and second_sum == 0:
                return "dying"
        return "low"
    if mean < CLASS_MODERATE_MIN:
        return "low"
    if mean < CLASS_HEALTHY_MIN:
        return "moderate"
    return "healthy"


def classify_sites(site_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """전체 site_stats 를 5범주(healthy/moderate/low/dying/dead)로 그룹화.

    반환
    ----
    ``{"healthy": ["code1", ...], "moderate": [...], ...,
        "counts": {"healthy": n, ...}, "by_code": {"code": "class", ...}}``
    """
    buckets = {"healthy": [], "moderate": [], "low": [], "dying": [], "dead": []}
    by_code: Dict[str, str] = {}
    for s in site_stats:
        c = classify_site(s)
        buckets[c].append(s["code"])
        by_code[s["code"]] = c
    counts = {k: len(v) for k, v in buckets.items()}
    return {**buckets, "counts": counts, "by_code": by_code}


# ── v2: Markdown 보고서 ───────────────────────────────────────────────────
def render_markdown(payload: Dict[str, Any]) -> str:
    """``payload`` 를 운영자용 일별 markdown 보고서로 변환.

    구조
    ----
    1. 헤더 (생성 일시·days·active_sites·총 voc)
    2. 일별 총량 (체크 라인)
    3. 사이트 분류 카운트
    4. 변동 사이트 표 (kind, code, mean, cv, reasons)
    5. 사이트별 상위 10 (total desc)
    6. dying / dead 사이트 전수 (조치 대상)
    """
    lines: List[str] = []
    generated_at = payload.get("generated_at") or ""
    days = int(payload.get("days") or 0)
    summary = payload.get("summary") or {}
    classification = payload.get("classification") or {}
    counts = classification.get("counts") or {}

    lines.append(f"# 수집 트렌드 보고서 ({days}d)")
    lines.append("")
    lines.append(f"- 생성: `{generated_at}`")
    lines.append(f"- 활성 사이트: **{payload.get('active_sites')}**")
    lines.append(f"- 총 voc: **{summary.get('total_voc'):,}** (일평균 {summary.get('mean_per_day')})")
    lines.append(
        f"- 변동: {summary.get('volatile_count')} "
        f"(down={summary.get('trend_down_count')} "
        f"up={summary.get('trend_up_count')} "
        f"swing={summary.get('volatile_swing_count')})"
    )
    lines.append("")
    # 분류 카운트
    lines.append("## 사이트 상태 분류")
    lines.append("")
    lines.append(
        f"| healthy | moderate | low | dying | dead |"
    )
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| {counts.get('healthy', 0)} | {counts.get('moderate', 0)} | "
        f"{counts.get('low', 0)} | {counts.get('dying', 0)} | "
        f"{counts.get('dead', 0)} |"
    )
    lines.append("")
    # 일별 총량
    lines.append("## 일별 총량")
    lines.append("")
    lines.append("| 날짜 | voc |")
    lines.append("|---|---|")
    for t in payload.get("daily_totals") or []:
        lines.append(f"| {t['date']} | {int(t['total']):,} |")
    lines.append("")
    # 변동 사이트
    vol = payload.get("volatile_sites") or []
    if vol:
        lines.append("## 변동 사이트")
        lines.append("")
        lines.append("| kind | code | mean/day | cv | half_delta | reasons |")
        lines.append("|---|---|---|---|---|---|")
        for v in vol[:30]:
            reasons = "; ".join(v.get("reasons") or [])
            lines.append(
                f"| {v.get('kind')} | {v.get('code')} | "
                f"{v.get('mean_per_day')} | {v.get('cv')} | "
                f"{v.get('half_ratio_delta')} | {reasons} |"
            )
        lines.append("")
    # 상위 10
    site_stats = payload.get("site_stats") or []
    if site_stats:
        lines.append("## 상위 10 사이트 (total desc)")
        lines.append("")
        lines.append("| code | total | mean/day | cv | max | min | class |")
        lines.append("|---|---|---|---|---|---|---|")
        by_code = classification.get("by_code") or {}
        for s in site_stats[:10]:
            klass = by_code.get(s.get("code"), "-")
            lines.append(
                f"| {s.get('code')} | {int(s.get('total') or 0):,} | "
                f"{s.get('mean_per_day')} | {s.get('cv')} | "
                f"{int(s.get('max') or 0)} | {int(s.get('min') or 0)} | {klass} |"
            )
        lines.append("")
    # 조치 대상: dying + dead
    dying = classification.get("dying") or []
    dead = classification.get("dead") or []
    if dying or dead:
        lines.append("## 조치 대상 (dying / dead)")
        lines.append("")
        if dying:
            lines.append(f"### dying ({len(dying)})")
            lines.append("")
            for code in dying:
                lines.append(f"- `{code}`")
            lines.append("")
        if dead:
            lines.append(f"### dead ({len(dead)})")
            lines.append("")
            for code in dead:
                lines.append(f"- `{code}`")
            lines.append("")
    return "\n".join(lines) + "\n"


# ── 실행 ─────────────────────────────────────────────────────────────────
async def collect_payload(
    days: int = 7,
    *,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """N일 누적 트렌드 payload 생성 (DB 1회 연결)."""
    conn = await asyncpg.connect(dsn or _dsn())
    try:
        m = await fetch_daily_matrix(conn, days)
    finally:
        await conn.close()
    site_stats = compute_site_stats(m["matrix"])
    volatile = identify_volatile_sites(site_stats)
    totals = daily_totals(m["matrix"], m["dates"])
    classification = classify_sites(site_stats)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": int(days),
        "dates": m["dates"],
        "active_sites": len(m["matrix"]),
        "daily_totals": totals,
        "site_stats": site_stats,
        "volatile_sites": volatile,
        "classification": classification,
        "thresholds": {
            "cv_volatile": THRESH_CV_VOLATILE,
            "half_ratio_delta": THRESH_HALF_RATIO_DELTA,
            "min_mean_for_volatility": MIN_MEAN_FOR_VOLATILITY,
            "class_healthy_min": CLASS_HEALTHY_MIN,
            "class_moderate_min": CLASS_MODERATE_MIN,
            "class_low_min": CLASS_LOW_MIN,
        },
        "summary": {
            "total_voc": sum(t["total"] for t in totals),
            "mean_per_day": (
                round(sum(t["total"] for t in totals) / days, 1)
                if days > 0 else 0.0
            ),
            "volatile_count": len(volatile),
            "trend_down_count": sum(1 for v in volatile if v.get("kind") == "trend_down"),
            "trend_up_count": sum(1 for v in volatile if v.get("kind") == "trend_up"),
            "volatile_swing_count": sum(1 for v in volatile if v.get("kind") == "volatile_swing"),
            "class_counts": classification.get("counts") or {},
        },
    }


def save_snapshot(
    payload: Dict[str, Any],
    report_dir: Path = DEFAULT_REPORT_DIR,
    *,
    write_md: bool = True,
) -> Dict[str, Path]:
    """``reports/collection_trend_YYYY-MM-DD.{json,md}`` (당일 덮어쓰기).

    Returns
    -------
    ``{"json": Path, "md": Path | None}``
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    json_path = report_dir / f"collection_trend_{today}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path: Optional[Path] = None
    if write_md:
        md_path = report_dir / f"collection_trend_{today}.md"
        md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": json_path, "md": md_path}


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="collection_trend")
    p.add_argument("--days", type=int, default=7, help="누적 일수 (기본 7)")
    p.add_argument("--json", type=str, default=None, help="결과 JSON 저장 경로")
    p.add_argument("--save", action="store_true",
                   help="reports/collection_trend_YYYY-MM-DD.json 스냅샷 적재")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    payload = asyncio.run(collect_payload(days=int(args.days)))
    print(
        f"[collection_trend] days={payload['days']} "
        f"sites={payload['active_sites']} "
        f"total_voc={payload['summary']['total_voc']} "
        f"volatile={payload['summary']['volatile_count']} "
        f"(down={payload['summary']['trend_down_count']} "
        f"up={payload['summary']['trend_up_count']} "
        f"swing={payload['summary']['volatile_swing_count']})"
    )
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    if args.save:
        sp = save_snapshot(payload)
        print(f"snapshot: json={sp.get('json')} md={sp.get('md')}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
