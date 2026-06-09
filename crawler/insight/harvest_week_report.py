"""Harvest 5 / Track V5 — 1주 누적 Harvest 보고서 + Slack 다이제스트.

목적
~~~~
Harvest 1 ~ 5 까지 *누적* 정량화 보고서를 일별 자동 생성한다.
``insight.weekly_monitor`` 가 운영 1주 단위 KPI 를 다루는 반면,
본 모듈은 **Harvest 라운드 진척** 자체를 추적한다:

    * voc 총량 / 24h / 7d
    * 활성 사이트 (24h / 7d)
    * Harvest 5 신규 도전 사이트 (xda news_tag) 진척
    * 매핑 NULL 비율 (gsmarena_forum / hardware_fr) — V2/V3 후속
    * alert_events 24h / 7d 추이
    * archive/<round> 누적 sentinel 카운트

``insight.weekly_monitor.run`` 을 *재사용* 하여 weekly_monitor MD/JSON 을
먼저 갱신하고, 그 위에 Harvest 누적 markdown 한 페이지를 덧붙인다.

Slack 정책
~~~~~~~~~~
``ALERT_WEBHOOK_URL`` 또는 ``SLACK_WEBHOOK_URL`` 가 있으면 다이제스트 1단을
POST 한다. 키가 없으면 ``status="skipped"`` 로 graceful skip — 본 실행은
깨지지 않는다 (운영 정책 유지). Slack 메시지는 weekly_monitor 와 따로
**Harvest 누적** 1줄 요약을 보낸다.

Audit
~~~~~
``audit_round("harvest5", track="V5", script="harvest_week_report")`` 로
JSONL start/end 가 보장된다 — 이는 V5 의 "audit JSONL round=harvest5
track=V5" 요건을 충족.

CLI
~~~
    python -m insight.harvest_week_report                  # 오늘(UTC)
    python -m insight.harvest_week_report 2026-06-07       # 명시 날짜
    python -m insight.harvest_week_report --no-slack       # Slack 강제 비활성화
    python -m insight.harvest_week_report --no-weekly      # weekly_monitor 재호출 생략
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

from base.audit import audit_round  # noqa: E402
from insight import weekly_monitor  # noqa: E402  (reuse run + slack 정책)

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
DEFAULT_BASE = os.getenv("SIGNALFORGE_API", "http://127.0.0.1:8000")

# Harvest 라운드 메타 (메모리/이번 5 시리즈 기준) ────────────────────────────
# 변경 시 NOTE: 각 라운드의 voc_total 은 *시점 기록* 이라 시간이 지나도 고정.
HARVEST_HISTORY: List[Dict[str, Any]] = [
    {
        "round": "harvest1",
        "date": "2026-06-06",
        "voc_total": 119739,
        "active_platforms": 65,
        "new_sites": ["notebookcheck", "zdnet_kr"],
        "note": "신규 사이트 2종 (notebookcheck/zdnet_kr) 활성화",
    },
    {
        "round": "harvest2",
        "date": "2026-06-06",
        "voc_total": 122231,
        "active_platforms": 67,
        "new_sites": ["resetera", "ifixit"],
        "note": "ResetEra/iFixit 신규 + 한국 deep 3,937",
    },
    {
        "round": "harvest3p",
        "date": "2026-06-06",
        "voc_total": 124500,
        "active_platforms": 68,
        "new_sites": [],
        "note": "운영 안정화 + Slack 결선",
    },
    {
        "round": "harvest4",
        "date": "2026-06-06",
        "voc_total": 127466,
        "active_platforms": 69,
        "new_sites": ["hardware_fr", "gsmarena_forum"],
        "note": "Hardware.fr / GSMArena forum 신규 (NULL 매핑 잔여)",
    },
]


@dataclass
class Harvest5Snapshot:
    """Harvest 5 시점의 실측 KPI (DB + 파일 시스템) — 한 번에 모두."""

    voc_total: int
    voc_24h: int
    voc_7d: int
    active_platforms_total: int    # platforms.is_active = TRUE
    active_platforms_24h: int      # 24h 내 voc 발생 사이트 수
    active_platforms_7d: int       # 7d 내 voc 발생 사이트 수
    alert_events_24h: int
    alert_events_7d: int
    archive_rounds: List[str]
    # V2/V3 매핑 NULL — Harvest 5 잔여 과제 KPI
    gsmarena_forum_null_24h: int = 0
    gsmarena_forum_total_24h: int = 0
    hardware_fr_null_24h: int = 0
    hardware_fr_total_24h: int = 0
    # V1 — XDA news_tag 도전 진척
    xda_voc_24h: int = 0
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
            "archive_rounds": list(self.archive_rounds),
            "gsmarena_forum_null_24h": self.gsmarena_forum_null_24h,
            "gsmarena_forum_total_24h": self.gsmarena_forum_total_24h,
            "hardware_fr_null_24h": self.hardware_fr_null_24h,
            "hardware_fr_total_24h": self.hardware_fr_total_24h,
            "xda_voc_24h": self.xda_voc_24h,
            "xda_voc_total": self.xda_voc_total,
        }


async def collect_snapshot(report_dir: Path = DEFAULT_REPORT_DIR) -> Harvest5Snapshot:
    """DB + archive 디렉토리에서 Harvest 5 누적 KPI 한 번에 수집."""
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

        # 매핑 NULL — gsmarena_forum / hardware_fr
        gsm_total = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='gsmarena_forum' "
            "AND v.collected_at > NOW() - interval '24 hours'"
        ) or 0)
        gsm_null = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='gsmarena_forum' "
            "AND v.collected_at > NOW() - interval '24 hours' "
            "AND v.product_id IS NULL"
        ) or 0)
        hwfr_total = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='hardware_fr' "
            "AND v.collected_at > NOW() - interval '24 hours'"
        ) or 0)
        hwfr_null = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='hardware_fr' "
            "AND v.collected_at > NOW() - interval '24 hours' "
            "AND v.product_id IS NULL"
        ) or 0)

        xda_total = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='xda'"
        ) or 0)
        xda_24h = int(await conn.fetchval(
            "SELECT count(*) FROM voc_records v JOIN platforms p ON p.id=v.platform_id "
            "WHERE p.code='xda' AND v.collected_at > NOW() - interval '24 hours'"
        ) or 0)
    finally:
        await conn.close()

    archive_dir = report_dir / "archive"
    archive_rounds: List[str] = []
    if archive_dir.is_dir():
        archive_rounds = sorted(
            p.name for p in archive_dir.iterdir() if p.is_dir()
        )

    return Harvest5Snapshot(
        voc_total=voc_total,
        voc_24h=voc_24h,
        voc_7d=voc_7d,
        active_platforms_total=active_total,
        active_platforms_24h=active_24h,
        active_platforms_7d=active_7d,
        alert_events_24h=alerts_24h,
        alert_events_7d=alerts_7d,
        archive_rounds=archive_rounds,
        gsmarena_forum_null_24h=gsm_null,
        gsmarena_forum_total_24h=gsm_total,
        hardware_fr_null_24h=hwfr_null,
        hardware_fr_total_24h=hwfr_total,
        xda_voc_24h=xda_24h,
        xda_voc_total=xda_total,
    )


def _pct(num: int, denom: int) -> str:
    if denom <= 0:
        return "—"
    return f"{(num / denom) * 100:.1f}%"


def render_harvest_week_md(
    target: date,
    snapshot: Harvest5Snapshot,
    history: List[Dict[str, Any]] = HARVEST_HISTORY,
) -> str:
    """Harvest 1-5 누적 markdown 1페이지 보고서.

    구조:
        1. 헤더 (날짜 / 라운드 수 / 누적 voc)
        2. Harvest 1-5 진척 표
        3. 현재 KPI 스냅샷 (voc/사이트/알림/archive)
        4. 잔여 과제 진척 (V1 XDA / V2 GSMArena / V3 HW.fr)
    """
    h5_row = {
        "round": "harvest5",
        "date": target.isoformat(),
        "voc_total": snapshot.voc_total,
        "active_platforms": snapshot.active_platforms_total,
        "new_sites": [],
        "note": "V5 누적 보고 + V1~V3 잔여 — 진행 중",
    }
    rounds = list(history) + [h5_row]

    lines: List[str] = []
    lines.append(f"# Harvest 1주 누적 보고 — {target.isoformat()}")
    lines.append("")
    lines.append(f"- 생성: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`")
    lines.append(f"- 라운드 수: **{len(rounds)}** (Harvest 1 ~ {rounds[-1]['round']})")
    lines.append(f"- 누적 voc: **{snapshot.voc_total:,}** "
                 f"(24h={snapshot.voc_24h:,} / 7d={snapshot.voc_7d:,})")
    lines.append(f"- 활성 사이트: total **{snapshot.active_platforms_total}** "
                 f"/ 24h **{snapshot.active_platforms_24h}** "
                 f"/ 7d **{snapshot.active_platforms_7d}**")
    lines.append("")

    # 1. Harvest 진척 표
    lines.append("## 1. Harvest 1-5 진척")
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

    # 2. 현재 KPI 스냅샷
    lines.append("## 2. 현재 KPI 스냅샷")
    lines.append("")
    lines.append(f"- voc: total **{snapshot.voc_total:,}** / "
                 f"24h {snapshot.voc_24h:,} / 7d {snapshot.voc_7d:,}")
    lines.append(f"- 사이트: total **{snapshot.active_platforms_total}** active / "
                 f"24h 활성 {snapshot.active_platforms_24h} / "
                 f"7d 활성 {snapshot.active_platforms_7d}")
    lines.append(f"- alert_events: 24h={snapshot.alert_events_24h:,} / "
                 f"7d={snapshot.alert_events_7d:,}")
    arch = snapshot.archive_rounds
    lines.append(f"- archive 라운드: **{len(arch)}** "
                 f"({', '.join(arch) if arch else '—'})")
    lines.append("")

    # 3. Harvest 5 잔여 과제 (V1~V3)
    lines.append("## 3. Harvest 5 잔여 과제")
    lines.append("")
    lines.append("| 트랙 | KPI | 현재 | 비고 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| V1 XDA news_tag | xda voc 24h | "
        f"{snapshot.xda_voc_24h:,} (total {snapshot.xda_voc_total:,}) | "
        f"forum 차단 → news_tag 정식 도입 평가 중 |"
    )
    gsm_null_pct = _pct(snapshot.gsmarena_forum_null_24h, snapshot.gsmarena_forum_total_24h)
    lines.append(
        f"| V2 GSMArena 매핑 | NULL 24h | "
        f"{snapshot.gsmarena_forum_null_24h}/{snapshot.gsmarena_forum_total_24h} "
        f"({gsm_null_pct}) | A57/A37/A78 단독 토큰 패턴 필요 |"
    )
    hwfr_null_pct = _pct(snapshot.hardware_fr_null_24h, snapshot.hardware_fr_total_24h)
    lines.append(
        f"| V3 Hardware.fr 매핑 | NULL 24h | "
        f"{snapshot.hardware_fr_null_24h}/{snapshot.hardware_fr_total_24h} "
        f"({hwfr_null_pct}) | forum thread title 추가 매핑 필요 |"
    )
    lines.append("")

    # 4. 정책 안내
    lines.append("## 4. 안전장치")
    lines.append("")
    lines.append("- DRY_RUN + PRESERVE_EXISTING + ON CONFLICT + audit JSONL round=harvest5 track=V5")
    lines.append("- archive/<round> 자동 sentinel — 4 라운드 폴더 유지")
    lines.append("- regression baseline 11/11 — Harvest 4 부터 hardware_fr_voc 포함")
    lines.append("- self-report drift ±10% 가드 유지")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_slack_digest(
    target: date,
    snapshot: Harvest5Snapshot,
    *,
    dashboard_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Harvest 5 1단 다이제스트 — voc 누적 + 활성 + 잔여 NULL 비율 요약."""
    dash = (dashboard_url
            or os.getenv("SIGNALFORGE_DASHBOARD_URL")
            or "http://localhost:3000").rstrip("/")
    summary = (
        f"Harvest1-5 누적 — voc_total={snapshot.voc_total:,} • "
        f"voc24h={snapshot.voc_24h:,} • "
        f"active={snapshot.active_platforms_total} • "
        f"alerts24h={snapshot.alert_events_24h} • "
        f"GSM_null={_pct(snapshot.gsmarena_forum_null_24h, snapshot.gsmarena_forum_total_24h)} • "
        f"HWfr_null={_pct(snapshot.hardware_fr_null_24h, snapshot.hardware_fr_total_24h)}"
    )
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"[SignalForge] Harvest 1주 누적 {target.isoformat()}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"<{dash}|대시보드 열기> • round=`harvest5` • track=`V5`"},
        ]},
    ]
    return {
        "text": f"[SignalForge][harvest-week] {target.isoformat()} — {summary}",
        "attachments": [{"color": "#1f77b4", "blocks": blocks}],
    }


def post_slack(
    target: date,
    snapshot: Harvest5Snapshot,
    *,
    force_skip: bool = False,
    _opener: Optional[Any] = None,
    timeout_sec: float = 5.0,
) -> Dict[str, Any]:
    """Slack 다이제스트 POST — 키 없으면 graceful skip.

    weekly_monitor.post_slack_digest 와 동일한 정책 — 키 우선순위,
    HTTP 200~299 sent, 그 외 failed, 예외도 raise 안함.
    """
    if force_skip:
        return {"status": "skipped", "reason": "force_skip", "http_status": None}
    url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip() or \
          (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not url:
        return {"status": "skipped", "reason": "no webhook", "http_status": None}
    import urllib.error
    import urllib.request

    digest = build_slack_digest(target, snapshot)
    data = json.dumps(digest, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
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


async def run(
    target: Optional[date] = None,
    *,
    base: str = DEFAULT_BASE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    slack: bool = True,
    invoke_weekly: bool = True,
    audit_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """V5 트랙 1회 실행 — audit_round 로 감싸서 weekly_monitor + Harvest MD 동시 산출.

    Returns
    -------
    ``{
        "target_date": "YYYY-MM-DD",
        "md_path": Path,
        "weekly_result": {...} | None,
        "snapshot": dict,
        "slack": {"status": "...", ...},
        "audit_path": Path,
    }``
    """
    target = target or datetime.now(timezone.utc).date()
    audit_p = audit_path or (report_dir / "backfill_audit.jsonl")

    with audit_round(
        "harvest5",
        track="V5",
        script="harvest_week_report",
        path=audit_p,
        extra={"target_date": target.isoformat()},
    ) as a:
        weekly_result: Optional[Dict[str, Any]] = None
        if invoke_weekly:
            try:
                weekly_result = await weekly_monitor.run(
                    target=target, days=7, base=base,
                    report_dir=report_dir, slack=False,  # weekly slack 은 따로 안 보냄
                )
                a.update(weekly_alerts=int(weekly_result.get("alerts", 0)))
            except Exception as e:  # noqa: BLE001
                # weekly_monitor 가 깨져도 Harvest 보고는 계속 — 결함 격리.
                weekly_result = {"error": str(e)}
                a.update(weekly_error=str(e))

        snapshot = await collect_snapshot(report_dir=report_dir)
        a.update(
            voc_total=snapshot.voc_total,
            voc_24h=snapshot.voc_24h,
            active_total=snapshot.active_platforms_total,
            alerts_24h=snapshot.alert_events_24h,
            archive_rounds=len(snapshot.archive_rounds),
        )

        md = render_harvest_week_md(target, snapshot)
        md_path = report_dir / f"HARVEST_WEEK_{target.isoformat()}.md"
        md_path.write_text(md, encoding="utf-8")
        a.event("harvest_week_md", path=str(md_path), bytes=len(md))

        slack_result = post_slack(target, snapshot, force_skip=not slack)
        a.update(slack_status=slack_result.get("status"))

    return {
        "target_date": target.isoformat(),
        "md_path": md_path,
        "weekly_result": weekly_result,
        "snapshot": snapshot.as_dict(),
        "slack": slack_result,
        "audit_path": audit_p,
    }


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="harvest_week_report")
    p.add_argument("target_date", nargs="?", help="YYYY-MM-DD (기본 오늘 UTC)")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--no-slack", action="store_true")
    p.add_argument("--no-weekly", action="store_true",
                   help="weekly_monitor.run 재호출 생략 (이미 최신 MD 가 있을 때)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    target = (datetime.fromisoformat(args.target_date).date()
              if args.target_date else datetime.now(timezone.utc).date())
    result = asyncio.run(run(
        target=target,
        base=args.base,
        slack=not args.no_slack,
        invoke_weekly=not args.no_weekly,
    ))
    print(
        f"[harvest-week] md={result['md_path']} "
        f"voc_total={result['snapshot']['voc_total']:,} "
        f"slack={result['slack'].get('status')} "
        f"audit={result['audit_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
