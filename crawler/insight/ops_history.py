"""운영 상태 일별 누적 적재 — ops-status 스냅샷 history (R18 트랙 D).

목적
----
``operations_monitor`` 가 *매시 30분* 호출되어 실시간 SLO 위반을 ``alert_events`` 에
INSERT 하지만, **일별 운영 추세** (어제 vs 오늘 voc/grounding/regression) 는 휘발성으로
남아 있지 않다. 이 모듈은 매일 09:30 KST (= 00:30 UTC) Celery beat 가 호출하여
``operations_monitor.collect_status`` 결과를 ``reports/ops_status_YYYY-MM-DD.json`` 으로
파일 한 개씩 적재한다.

이 파일들은 backend ``/api/v1/_internal/ops-trend?days=N`` endpoint 가 읽어 일별 시계열
+ 7일 이동 평균 + 변화율을 응답한다.

저장 포맷 (1일 1파일)::

    {
      "captured_at": "2026-06-05T00:30:00+00:00",
      "target_date": "2026-06-05",
      "status": "ok" | "warning" | "critical",
      "voc_last": 5234,           # voc.days[0].n (어제)
      "voc_prev": 6120,           # voc.days[1].n (그제)
      "sentiment_null_rate": 0.02,
      "topic_rate": 0.89,
      "grounding_last": 0.42,
      "regression_ok_ratio": 1.0,
      "regression_failed": 0,
      "violations_count": 1,
      "violations": [...]          # 원본 보존 (드릴다운용)
    }

CLI::

    python -m insight.ops_history                # 1회 실행 (오늘 UTC 날짜)
    python -m insight.ops_history --date 2026-06-04
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# crawler/ sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from insight.operations_monitor import collect_status  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"


def summarize(payload: Dict[str, Any], target: date) -> Dict[str, Any]:
    """``collect_status`` 원본 payload → 일별 시계열용 슬림 dict 로 압축.

    원본 violations 는 드릴다운/감사용으로 그대로 보존하지만, voc·sentiment·topic
    등 핵심 수치는 stable key 로 끌어올린다 (ops-trend endpoint 가 응답 시 사용).
    """
    voc_days = ((payload.get("voc") or {}).get("days") or [])
    voc_last_n: Optional[int] = None
    voc_prev_n: Optional[int] = None
    sent_null: Optional[float] = None
    topic_rate: Optional[float] = None
    if voc_days:
        head = voc_days[0]
        voc_last_n = int(head.get("n") or 0)
        sent_null = head.get("sentiment_null_rate")
        topic_rate = head.get("topic_rate")
    if len(voc_days) >= 2:
        voc_prev_n = int(voc_days[1].get("n") or 0)

    reg = payload.get("regression") or {}
    return {
        "captured_at": payload.get("generated_at")
                       or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "status": payload.get("status"),
        "voc_last": voc_last_n,
        "voc_prev": voc_prev_n,
        "sentiment_null_rate": sent_null,
        "topic_rate": topic_rate,
        "grounding_last": payload.get("grounding_last"),
        "regression_ok_ratio": reg.get("ok_ratio"),
        "regression_failed": reg.get("failed"),
        "violations_count": len(payload.get("violations") or []),
        "violations": payload.get("violations") or [],
    }


def save_summary(
    summary: Dict[str, Any],
    target: date,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """``reports/ops_status_YYYY-MM-DD.json`` 으로 저장. 같은 날짜 재실행 시 덮어쓴다."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"ops_status_{target.isoformat()}.json"
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


async def run(
    *,
    target: Optional[date] = None,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """ops-status 1회 평가 → 슬림 요약 JSON 저장. 저장된 경로 반환.

    Celery task ``run_ops_history`` 에서 호출.
    """
    target = target or datetime.now(timezone.utc).date()
    payload = await collect_status()
    summary = summarize(payload, target)
    path = save_summary(summary, target, report_dir=report_dir)
    logger.info(
        "[ops-history] saved %s status=%s violations=%d",
        path, summary.get("status"), summary.get("violations_count"),
    )
    return path


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ops_history")
    p.add_argument("--date", dest="target_date",
                   help="YYYY-MM-DD (기본: 오늘 UTC)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    target = (datetime.fromisoformat(args.target_date).date()
              if args.target_date else datetime.now(timezone.utc).date())
    path = asyncio.run(run(target=target))
    print(f"[ops-history] saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
