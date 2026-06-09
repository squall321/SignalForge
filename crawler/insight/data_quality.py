"""데이터 품질 자동 검증 — R12 트랙 E2 (2026-06-04).

목적
----
매일 자동으로 수집 결과의 *데이터 품질* 을 점검하고, 임계 미달 신호가 있으면
``reports/data_quality_YYYY-MM-DD.json`` 에 보고서를 남긴다.  ``quality_report.py``
가 운영 SLO/캐시/grounding 등 *프로세스 신호* 를 담당한다면, 이 모듈은 *데이터
자체의 신호* 를 담당한다.

검증 6 metric
~~~~~~~~~~~~
1. 신규 voc 본문 길이 분포 — ``avg`` / ``p10`` / ``p90``
2. 중복 본문 비율 — content_original SHA1 hash 기준 (윈도우 한정)
3. ``product_id`` 매칭 비율 — 신규 voc 중 product_id IS NOT NULL
4. ``sentiment_label`` NULL 비율 — NLP 미통과 비율
5. topic 분류율 — ``array_length(topics,1) > 0`` 비율
6. 활성 platform 카운트 — 24h 내 voc 가 들어온 platform 수

기본 윈도우는 *최근 24h* (``--hours 24``).  매일 09:30 KST 예정.
모든 metric 에 *임계*를 부여하여 미달 시 ``alerts[]`` 에 reason 을 넣는다.

CLI::

    python -m insight.data_quality                  # 24h 윈도우, JSON stdout
    python -m insight.data_quality --hours 168      # 7d
    python -m insight.data_quality --out reports/data_quality_2026-06-04.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

# ── 경로/환경 ────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"


def _resolve_database_url() -> str:
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


# ── 임계 (운영 기본값) ───────────────────────────────────────────────────
# 모든 metric 은 *최소* 또는 *최대* 단방향 임계만 사용 (해석 단순화).
DEFAULT_THRESHOLDS: Dict[str, float] = {
    # 신규 voc 본문 길이 평균 — 너무 짧으면 (스팸/제목만) 의심
    "content_length_avg_min": 20.0,
    # 중복 비율 — 5% 초과 시 alert
    "duplicate_rate_max": 0.05,
    # product_id 매칭 비율 — 5% 미만이면 매칭 사전 작동 의심
    "product_match_rate_min": 0.05,
    # sentiment_label NULL 비율 — 30% 초과면 NLP 파이프 의심
    "sentiment_null_rate_max": 0.30,
    # topic 분류율 — 10% 미만이면 사전 너무 좁음 의심 (정책 변동성 큼)
    "topic_classified_rate_min": 0.10,
    # 활성 platform 수 — 윈도우 내 voc 들어온 사이트 (현재 운영 ~30+ 기대)
    "active_platforms_min": 10,
}


# ── 데이터 모델 ─────────────────────────────────────────────────────────
@dataclass
class LengthDist:
    n: int = 0
    avg: Optional[float] = None
    p10: Optional[float] = None
    p90: Optional[float] = None


@dataclass
class DataQualityReport:
    hours: int
    window_start: str
    window_end: str
    new_voc_count: int = 0
    length_dist: LengthDist = field(default_factory=LengthDist)
    duplicate_rate: Optional[float] = None
    duplicate_count: int = 0
    product_match_rate: Optional[float] = None
    sentiment_null_rate: Optional[float] = None
    topic_classified_rate: Optional[float] = None
    active_platforms: int = 0
    thresholds: Dict[str, float] = field(default_factory=dict)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ── 검증 로직 (DB 독립) ──────────────────────────────────────────────────
def _percentile(sorted_vals: List[int], pct: float) -> Optional[float]:
    """0-100 percentile (linear). sorted_vals 가 비면 None."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_length_dist(lengths: List[int]) -> LengthDist:
    """본문 길이 리스트 → avg/p10/p90."""
    if not lengths:
        return LengthDist()
    s = sorted(lengths)
    avg = sum(s) / len(s)
    return LengthDist(
        n=len(s),
        avg=round(avg, 1),
        p10=round(_percentile(s, 10) or 0.0, 1),
        p90=round(_percentile(s, 90) or 0.0, 1),
    )


def compute_duplicate_rate(contents: List[str]) -> tuple[float, int]:
    """동일 SHA1(본문) 가 2회 이상 등장하는 *중복분* 비율.

    반환: (중복분 비율 0-1, 중복분 행 수).  본문이 비면 (0.0, 0).
    """
    if not contents:
        return 0.0, 0
    counts: Dict[str, int] = {}
    for c in contents:
        if not c:
            continue
        h = hashlib.sha1(c.encode("utf-8", errors="ignore")).hexdigest()
        counts[h] = counts.get(h, 0) + 1
    dup = sum(n - 1 for n in counts.values() if n > 1)
    return (dup / len(contents) if contents else 0.0, dup)


def evaluate_alerts(
    report: DataQualityReport,
    thresholds: Dict[str, float],
) -> List[Dict[str, Any]]:
    """임계 미달 → alerts 목록.  보고서 자체 mutation 없이 list 만 반환."""
    out: List[Dict[str, Any]] = []

    if report.new_voc_count == 0:
        out.append({
            "metric": "new_voc_count",
            "level": "warning",
            "value": 0,
            "reason": "윈도우 내 신규 voc 0건 — 수집 파이프 확인",
        })
        return out

    if (report.length_dist.avg or 0) < thresholds["content_length_avg_min"]:
        out.append({
            "metric": "content_length_avg",
            "level": "warning",
            "value": report.length_dist.avg,
            "threshold": thresholds["content_length_avg_min"],
            "reason": "본문 평균 길이 미달 — 제목만/스팸 의심",
        })

    if (report.duplicate_rate or 0) > thresholds["duplicate_rate_max"]:
        out.append({
            "metric": "duplicate_rate",
            "level": "warning",
            "value": report.duplicate_rate,
            "threshold": thresholds["duplicate_rate_max"],
            "reason": "중복 본문 비율 초과 — 크롤러 페이지네이션/dedup 점검",
        })

    if (report.product_match_rate or 0) < thresholds["product_match_rate_min"]:
        out.append({
            "metric": "product_match_rate",
            "level": "warning",
            "value": report.product_match_rate,
            "threshold": thresholds["product_match_rate_min"],
            "reason": "product_id 매칭 비율 저조 — 사전/relink 점검",
        })

    if (report.sentiment_null_rate or 0) > thresholds["sentiment_null_rate_max"]:
        out.append({
            "metric": "sentiment_null_rate",
            "level": "warning",
            "value": report.sentiment_null_rate,
            "threshold": thresholds["sentiment_null_rate_max"],
            "reason": "sentiment NULL 비율 초과 — NLP 파이프 확인",
        })

    if (report.topic_classified_rate or 0) < thresholds["topic_classified_rate_min"]:
        out.append({
            "metric": "topic_classified_rate",
            "level": "info",
            "value": report.topic_classified_rate,
            "threshold": thresholds["topic_classified_rate_min"],
            "reason": "topic 분류율 저조 — topic_classifier 사전 확장 검토",
        })

    if report.active_platforms < thresholds["active_platforms_min"]:
        out.append({
            "metric": "active_platforms",
            "level": "warning",
            "value": report.active_platforms,
            "threshold": thresholds["active_platforms_min"],
            "reason": "활성 platform 수 미달 — 수집 헬스 진단 필요",
        })

    return out


# ── DB 수집 ─────────────────────────────────────────────────────────────
async def collect_report(
    hours: int = 24,
    *,
    dsn: Optional[str] = None,
    thresholds: Optional[Dict[str, float]] = None,
    sample_cap: int = 50_000,
) -> DataQualityReport:
    """DB 에서 윈도우 데이터 추출 → 보고서 생성.

    ``sample_cap`` 은 중복/길이 분석을 위해 메모리에 로드하는 최대 행 수.
    윈도우가 24h 면 보통 수만 건 — 50k 안전 한도.
    """
    dsn = dsn or _resolve_database_url()
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    now = datetime.now(timezone.utc)
    report = DataQualityReport(
        hours=hours,
        window_start=(now.timestamp() - hours * 3600).__str__(),
        window_end=now.isoformat(timespec="seconds"),
        thresholds=thresholds,
        generated_at=now.isoformat(timespec="seconds"),
    )
    # window_start 는 ISO 8601 로 다시 채움
    report.window_start = datetime.fromtimestamp(
        now.timestamp() - hours * 3600, tz=timezone.utc
    ).isoformat(timespec="seconds")

    conn = await asyncpg.connect(dsn)
    try:
        # 1) count + 분포 (collected_at 기준 — 신규 입수 신호)
        count_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS n,
                COUNT(*) FILTER (WHERE product_id IS NOT NULL) AS linked,
                COUNT(*) FILTER (WHERE sentiment_label IS NULL) AS sent_null,
                COUNT(*) FILTER (WHERE array_length(topics,1) > 0) AS topics_filled,
                COUNT(DISTINCT platform_id) FILTER (WHERE platform_id IS NOT NULL) AS platforms
            FROM voc_records
            WHERE collected_at >= NOW() - ($1 * INTERVAL '1 hour')
            """,
            hours,
        )
        n = int(count_row["n"] or 0)
        report.new_voc_count = n
        if n > 0:
            report.product_match_rate = round(int(count_row["linked"] or 0) / n, 4)
            report.sentiment_null_rate = round(int(count_row["sent_null"] or 0) / n, 4)
            report.topic_classified_rate = round(
                int(count_row["topics_filled"] or 0) / n, 4
            )
        report.active_platforms = int(count_row["platforms"] or 0)

        # 2) 길이 분포 + 중복 비율 — 표본 cap
        if n > 0:
            rows = await conn.fetch(
                """
                SELECT content_original
                FROM voc_records
                WHERE collected_at >= NOW() - ($1 * INTERVAL '1 hour')
                  AND content_original IS NOT NULL
                LIMIT $2
                """,
                hours, sample_cap,
            )
            lengths: List[int] = []
            contents: List[str] = []
            for r in rows:
                c = r["content_original"] or ""
                lengths.append(len(c))
                contents.append(c)
            report.length_dist = compute_length_dist(lengths)
            dup_rate, dup_count = compute_duplicate_rate(contents)
            report.duplicate_rate = round(dup_rate, 4)
            report.duplicate_count = dup_count
    finally:
        await conn.close()

    report.alerts = evaluate_alerts(report, thresholds)
    return report


# ── 엔트리 ──────────────────────────────────────────────────────────────
def _default_out_path(report_dir: Path, target_date: str) -> Path:
    return report_dir / f"data_quality_{target_date}.json"


async def run_async(
    hours: int,
    *,
    out_path: Optional[Path] = None,
    report_dir: Optional[Path] = None,
) -> Path:
    if report_dir is None:
        report_dir = Path(os.getenv("REPORT_DIR", str(DEFAULT_REPORT_DIR)))
    report_dir.mkdir(parents=True, exist_ok=True)

    report = await collect_report(hours=hours)
    payload = report.to_dict()

    if out_path is None:
        target_date = datetime.now(timezone.utc).date().isoformat()
        out_path = _default_out_path(report_dir, target_date)

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[data_quality] 저장: %s (alerts=%d)", out_path, len(report.alerts))
    return out_path


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    ap = argparse.ArgumentParser(description="SignalForge 데이터 품질 자동 검증")
    ap.add_argument("--hours", type=int, default=24, help="윈도우 시간 (기본 24)")
    ap.add_argument("--out", type=str, default=None, help="출력 JSON 경로")
    args = ap.parse_args(argv)

    out_path = Path(args.out) if args.out else None
    result = asyncio.run(run_async(hours=args.hours, out_path=out_path))
    print(f"OK: {result}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
