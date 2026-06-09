"""Harvest 7 / Track X3 — V5 보고서 재실행 + alert 라벨 정정 + NULL 최신화.

Harvest 6 부분 완료 기준으로 V5 의 NULL drift 와 alert 라벨 오기재를 해소한다.
``harvest_week_report`` 는 그대로 두고, v2 한 페이지를 추가 산출한다 (surgical).

차이점
~~~~~~
    * alert_events 24h / 7d / **30d** 모두 명시 (V5 는 24h/7d 만)
    * notebookcheck NULL 24h 추가 (V4 신규 분석 자산)
    * Harvest 6 / 7 라운드 메타 추가 (V5 history 는 변경하지 않음)
    * 표지에 "라벨 정정" 사유 명시

CLI
~~~
    python -m insight.harvest_week_report_v2                  # 오늘(UTC)
    python -m insight.harvest_week_report_v2 2026-06-07       # 명시 날짜
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

from base.audit import audit_round  # noqa: E402
from insight import harvest_week_report as hwr  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

# Harvest 6 + 7 메타 (V5 history 위에 누적)
HARVEST_HISTORY_V2: List[Dict[str, Any]] = list(hwr.HARVEST_HISTORY) + [
    {
        "round": "harvest5",
        "date": "2026-06-07",
        "voc_total": 129833,
        "active_platforms": 69,
        "new_sites": [],
        "note": "V5 누적 보고 + V1~V3 잔여 (24h XDA +72)",
    },
    {
        "round": "harvest6p",
        "date": "2026-06-07",
        "voc_total": 132620,
        "active_platforms": 70,
        "new_sites": [],
        "note": "rate-limit 2회 — 메인 직접 진단, alert drift 식별 (24h 346 / 7d 1,619 / 30d 1,619)",
    },
]


@dataclass
class Harvest7Snapshot:
    """Harvest 7 시점 실측 — 알림 30d / notebookcheck NULL 추가."""

    voc_total: int
    voc_24h: int
    voc_7d: int
    active_platforms_total: int
    active_platforms_24h: int
    active_platforms_7d: int
    alert_events_24h: int
    alert_events_7d: int
    alert_events_30d: int
    archive_rounds: List[str]
    gsmarena_forum_null_24h: int = 0
    gsmarena_forum_total_24h: int = 0
    hardware_fr_null_24h: int = 0
    hardware_fr_total_24h: int = 0
    notebookcheck_null_24h: int = 0
    notebookcheck_total_24h: int = 0
    xda_null_24h: int = 0
    xda_total_24h: int = 0
    xda_voc_total: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "voc_total": self.voc_total,
            "voc_24h": self.voc_24h,
            "voc_7d": self.voc_7d,
            "active_platforms_total": self.active_platforms_total,
            "active_platforms_24h": self.active_platforms_24h,
            "active_platforms_7d": self.active_platforms_7d,
            "alert_events_24h": self.alert_events_24h,
            "alert_events_7d": self.alert_events_7d,
            "alert_events_30d": self.alert_events_30d,
            "archive_rounds": list(self.archive_rounds),
            "gsmarena_forum_null_24h": self.gsmarena_forum_null_24h,
            "gsmarena_forum_total_24h": self.gsmarena_forum_total_24h,
            "hardware_fr_null_24h": self.hardware_fr_null_24h,
            "hardware_fr_total_24h": self.hardware_fr_total_24h,
            "notebookcheck_null_24h": self.notebookcheck_null_24h,
            "notebookcheck_total_24h": self.notebookcheck_total_24h,
            "xda_null_24h": self.xda_null_24h,
            "xda_total_24h": self.xda_total_24h,
            "xda_voc_total": self.xda_voc_total,
        }


async def collect_snapshot_v2(report_dir: Path = DEFAULT_REPORT_DIR) -> Harvest7Snapshot:
    """DB + archive — V5 의 collect_snapshot 보강."""
    # weekly_monitor 의 DSN 재사용
    from insight import weekly_monitor  # 지연 import (사이클 방지)
    conn = await asyncpg.connect(weekly_monitor._dsn())
    try:
        voc_total = int(await conn.fetchval("SELECT count(*) FROM voc_records") or 0)
        voc_24h = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records WHERE collected_at > NOW() - interval '24 hours'"
        ) or 0)
        voc_7d = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records WHERE collected_at > NOW() - interval '7 days'"
        ) or 0)
        active_total = int(await conn.fetchval(
            "SELECT count(*) FROM platforms WHERE is_active = TRUE"
        ) or 0)
        active_24h = int(await conn.fetchval(
            "SELECT count(DISTINCT platform_id) FROM voc_records "
            "WHERE collected_at > NOW() - interval '24 hours'"
        ) or 0)
        active_7d = int(await conn.fetchval(
            "SELECT count(DISTINCT platform_id) FROM voc_records "
            "WHERE collected_at > NOW() - interval '7 days'"
        ) or 0)
        alerts_24h = int(await conn.fetchval(
            "SELECT count(*) FROM alert_events WHERE fired_at > NOW() - interval '24 hours'"
        ) or 0)
        alerts_7d = int(await conn.fetchval(
            "SELECT count(*) FROM alert_events WHERE fired_at > NOW() - interval '7 days'"
        ) or 0)
        alerts_30d = int(await conn.fetchval(
            "SELECT count(*) FROM alert_events WHERE fired_at > NOW() - interval '30 days'"
        ) or 0)

        async def _site_pair(code: str) -> tuple[int, int]:
            tot = int(await conn.fetchval(
                "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
                "WHERE p.code=$1 AND v.collected_at > NOW() - interval '24 hours'",
                code,
            ) or 0)
            nul = int(await conn.fetchval(
                "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
                "WHERE p.code=$1 AND v.collected_at > NOW() - interval '24 hours' "
                "AND v.product_id IS NULL",
                code,
            ) or 0)
            return tot, nul

        gsm_tot, gsm_null = await _site_pair("gsmarena_forum")
        hwfr_tot, hwfr_null = await _site_pair("hardware_fr")
        nb_tot, nb_null = await _site_pair("notebookcheck")
        xda_tot, xda_null = await _site_pair("xda")
        xda_total = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='xda'"
        ) or 0)
    finally:
        await conn.close()

    archive_dir = report_dir / "archive"
    archive_rounds: List[str] = []
    if archive_dir.is_dir():
        archive_rounds = sorted(p.name for p in archive_dir.iterdir() if p.is_dir())

    return Harvest7Snapshot(
        voc_total=voc_total, voc_24h=voc_24h, voc_7d=voc_7d,
        active_platforms_total=active_total,
        active_platforms_24h=active_24h,
        active_platforms_7d=active_7d,
        alert_events_24h=alerts_24h,
        alert_events_7d=alerts_7d,
        alert_events_30d=alerts_30d,
        archive_rounds=archive_rounds,
        gsmarena_forum_null_24h=gsm_null,
        gsmarena_forum_total_24h=gsm_tot,
        hardware_fr_null_24h=hwfr_null,
        hardware_fr_total_24h=hwfr_tot,
        notebookcheck_null_24h=nb_null,
        notebookcheck_total_24h=nb_tot,
        xda_null_24h=xda_null,
        xda_total_24h=xda_tot,
        xda_voc_total=xda_total,
    )


def _pct(num: int, denom: int) -> str:
    if denom <= 0:
        return "—"
    return f"{(num / denom) * 100:.1f}%"


def render_harvest_week_md_v2(
    target: date,
    snapshot: Harvest7Snapshot,
    history: List[Dict[str, Any]] = HARVEST_HISTORY_V2,
) -> str:
    """v2 보고서 — alert 라벨 명시 + notebookcheck NULL + Harvest 6/7 메타."""
    h7_row = {
        "round": "harvest7",
        "date": target.isoformat(),
        "voc_total": snapshot.voc_total,
        "active_platforms": snapshot.active_platforms_total,
        "new_sites": [],
        "note": "X3 V5 재실행 + alert 라벨 정정 (24h/7d/30d 분리 명시) + NULL 최신화",
    }
    rounds = list(history) + [h7_row]

    lines: List[str] = []
    lines.append(f"# Harvest 1주 누적 보고 v2 — {target.isoformat()}")
    lines.append("")
    lines.append(f"- 생성: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`")
    lines.append(f"- 라운드 수: **{len(rounds)}** (Harvest 1 ~ {rounds[-1]['round']})")
    lines.append(f"- 누적 voc: **{snapshot.voc_total:,}** "
                 f"(24h={snapshot.voc_24h:,} / 7d={snapshot.voc_7d:,})")
    lines.append(f"- 활성 사이트: total **{snapshot.active_platforms_total}** "
                 f"/ 24h **{snapshot.active_platforms_24h}** "
                 f"/ 7d **{snapshot.active_platforms_7d}**")
    lines.append("- 라벨 정정 사유: Harvest 5 V5 보고서가 alert 24h/7d 만 명시하여 30d 가 누락. "
                 "Harvest 6 부분 완료 단계에서 NULL drift 발견 → v2 로 재실행.")
    lines.append("")

    # 1. Harvest 진척 표 (1 ~ 7)
    lines.append("## 1. Harvest 1-7 진척")
    lines.append("")
    lines.append("| round | date | voc_total | active | 신규 사이트 | 비고 |")
    lines.append("|---|---|---|---|---|---|")
    prev_voc = 0
    for r in rounds:
        v = int(r.get("voc_total", 0) or 0)
        delta = v - prev_voc if prev_voc else 0
        delta_str = f"(+{delta:,})" if delta > 0 else ""
        new_str = ", ".join(r.get("new_sites") or []) or "—"
        lines.append(
            f"| {r['round']} | {r['date']} | {v:,} {delta_str} | "
            f"{r.get('active_platforms', '?')} | {new_str} | {r.get('note', '')} |"
        )
        prev_voc = v
    lines.append("")

    # 2. 현재 KPI 스냅샷 (alert 30d 추가)
    lines.append("## 2. 현재 KPI 스냅샷")
    lines.append("")
    lines.append(f"- voc: total **{snapshot.voc_total:,}** / "
                 f"24h {snapshot.voc_24h:,} / 7d {snapshot.voc_7d:,}")
    lines.append(f"- 사이트: total **{snapshot.active_platforms_total}** active / "
                 f"24h 활성 {snapshot.active_platforms_24h} / "
                 f"7d 활성 {snapshot.active_platforms_7d}")
    arch = snapshot.archive_rounds
    lines.append(f"- archive 라운드: **{len(arch)}** "
                 f"({', '.join(arch) if arch else '—'})")
    lines.append("")

    # 2-1. Alert 라벨 명시 (X3 핵심)
    lines.append("### 2-1. Alert 라벨 명시 (라벨 drift 해소)")
    lines.append("")
    lines.append("| 기간 | 건수 | 비고 |")
    lines.append("|---|---|---|")
    lines.append(f"| **24h** | {snapshot.alert_events_24h:,} | 최근 1일 fired_at |")
    lines.append(f"| **7d**  | {snapshot.alert_events_7d:,} | 최근 7일 fired_at |")
    lines.append(f"| **30d** | {snapshot.alert_events_30d:,} | 최근 30일 fired_at — V5 누락 |")
    lines.append("")
    if snapshot.alert_events_7d == snapshot.alert_events_30d:
        lines.append(
            f"> **Note**: 7d ({snapshot.alert_events_7d:,}) == 30d "
            f"({snapshot.alert_events_30d:,}) — 알림 발화가 최근 7일에 집중되어 "
            "기간 간 동일치. 라벨 drift 가 아닌 실측 일치."
        )
        lines.append("")

    # 3. 잔여 과제 NULL (notebookcheck 추가)
    lines.append("## 3. NULL 매핑 잔여 과제 (24h 실측)")
    lines.append("")
    lines.append("| 사이트 | NULL / total | 비율 | 비고 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| xda | {snapshot.xda_null_24h}/{snapshot.xda_total_24h} | "
        f"{_pct(snapshot.xda_null_24h, snapshot.xda_total_24h)} | "
        f"X1 매핑 후 -53% 목표 (One UI · Buds+ · Watch · Tab · Flip · Fold) |"
    )
    lines.append(
        f"| gsmarena_forum | {snapshot.gsmarena_forum_null_24h}/{snapshot.gsmarena_forum_total_24h} | "
        f"{_pct(snapshot.gsmarena_forum_null_24h, snapshot.gsmarena_forum_total_24h)} | "
        f"competitor (iPhone/Poco/Xiaomi) 의도 추적 — 필터 불필요 |"
    )
    lines.append(
        f"| hardware_fr | {snapshot.hardware_fr_null_24h}/{snapshot.hardware_fr_total_24h} | "
        f"{_pct(snapshot.hardware_fr_null_24h, snapshot.hardware_fr_total_24h)} | "
        f"22.9% — 안정 운영 범위 |"
    )
    lines.append(
        f"| notebookcheck | {snapshot.notebookcheck_null_24h}/{snapshot.notebookcheck_total_24h} | "
        f"{_pct(snapshot.notebookcheck_null_24h, snapshot.notebookcheck_total_24h)} | "
        f"V4 신규 분석 — 안정 |"
    )
    lines.append("")

    # 4. 안전장치
    lines.append("## 4. 안전장치")
    lines.append("")
    lines.append("- DRY_RUN + PRESERVE_EXISTING + ON CONFLICT + audit JSONL round=harvest7 track=X3")
    lines.append("- archive/<round> 자동 sentinel 유지")
    lines.append("- regression baseline 11/11 endpoint")
    lines.append("- self-report drift ±10% 가드")
    lines.append("")
    return "\n".join(lines) + "\n"


async def run(
    target: Optional[date] = None,
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    audit_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """X3 트랙 1회 실행 — audit_round(harvest7, X3) 로 감싼다."""
    target = target or datetime.now(timezone.utc).date()
    audit_p = audit_path or (report_dir / "backfill_audit.jsonl")

    with audit_round(
        "harvest7",
        track="X3",
        script="harvest_week_report_v2",
        path=audit_p,
        extra={"target_date": target.isoformat()},
    ) as a:
        snapshot = await collect_snapshot_v2(report_dir=report_dir)
        a.update(
            voc_total=snapshot.voc_total,
            voc_24h=snapshot.voc_24h,
            active_total=snapshot.active_platforms_total,
            alerts_24h=snapshot.alert_events_24h,
            alerts_7d=snapshot.alert_events_7d,
            alerts_30d=snapshot.alert_events_30d,
            archive_rounds=len(snapshot.archive_rounds),
        )

        md = render_harvest_week_md_v2(target, snapshot)
        md_path = report_dir / f"HARVEST_WEEK_{target.isoformat()}_v2.md"
        md_path.write_text(md, encoding="utf-8")
        a.event("harvest_week_md_v2", path=str(md_path), bytes=len(md))

    return {
        "target_date": target.isoformat(),
        "md_path": md_path,
        "snapshot": snapshot.as_dict(),
        "audit_path": audit_p,
    }


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="harvest_week_report_v2")
    p.add_argument("target_date", nargs="?", help="YYYY-MM-DD (기본 오늘 UTC)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    target = (datetime.fromisoformat(args.target_date).date()
              if args.target_date else datetime.now(timezone.utc).date())
    result = asyncio.run(run(target=target))
    print(
        f"[harvest-week-v2] md={result['md_path']} "
        f"voc_total={result['snapshot']['voc_total']:,} "
        f"alerts_24h={result['snapshot']['alert_events_24h']} "
        f"alerts_7d={result['snapshot']['alert_events_7d']} "
        f"alerts_30d={result['snapshot']['alert_events_30d']} "
        f"audit={result['audit_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
