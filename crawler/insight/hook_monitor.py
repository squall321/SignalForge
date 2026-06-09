"""Hook monitor — workflow_validator_hook 의 1주 운영 통계 집계 (R27 트랙 C).

목적
====
R26 권고 3 — workflow_validator_hook 1주 운영 모니터링.  Celery beat 가 매
5분 ``tasks.run_validator_hook`` 을 호출하여 ``reports/validator_hook_state.json``
이 갱신되는데, 1주 누적 데이터에서:

1. 후크 가동 빈도 (scan_count 증분, 활성 시간대)
2. 자동 캡처 alert 누계 + alert/scan 비율
3. archive_drift 누계 (R25 사고 재발 모니터링)
4. round 별 평균 drift / max drift
5. False positive 의심 — 같은 round 가 반복 알람을 내지만 후속 라운드에서
   *해결 표식* (alerts=0) 가 없는 경우

입력 소스
=========
1. **상태 파일** ``reports/validator_hook_state.json``
   ── 현재 후크의 *최신 5개* history.  실시간 상태.
2. **workflow_validate 보고서** ``reports/workflow_validate_R*.md``
   ── validator CLI/HTTP 로 *수동* 또는 *후크 자동* 생성된 검증 보고서.
   본 모듈은 mtime 이 ``days`` 윈도우 안에 들어오는 파일만 집계 — 1주 추이.
3. **archive 디렉토리** ``reports/archive/R*/``
   ── archive_drift 가 잘못된 자동 캡처인지 (R26 이후 실제 archive 생성)
   교차 확인.

설계 원칙
=========
- 외부 의존 없음 (psql / HTTP 미사용).  파일/디렉토리 stat + 텍스트 파싱.
- graceful: 상태 파일이 없으면 ``available=False`` 응답.
- self-report drift 보정: hook_monitor 본인이 자기 출력 보고서를 *다시* 검증
  하는 경우는 ``ignore_self`` 옵션으로 제거 가능 (기본 True).
- false_positive 판정: round 가 history 안에 2회 이상 등장 + alert>=1
  *모두 동일 metric* 이면 ``persistent`` (가능한 false positive),
  *후속 entry 에서 alert=0* 이면 ``resolved``.

CLI 사용
========

.. code-block:: bash

    # 7일 통계 (기본)
    python -m insight.hook_monitor

    # 14일
    python -m insight.hook_monitor --days 14

    # JSON
    python -m insight.hook_monitor --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Dict, List, Optional, Tuple

# crawler/ sys.path 보장 (단독 실행 호환).
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_STATE_PATH = REPO_ROOT / "reports" / "validator_hook_state.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports"
DEFAULT_ARCHIVE_DIR = REPO_ROOT / "reports" / "archive"

# workflow_validate_R{N}*.md — R 식별
_RE_VALIDATE_ROUND = re.compile(r"workflow_validate_(R\d+)", re.IGNORECASE)
# 보고서 본문에서 alerts / mean drift 추출 (parse_report 출력 형식 호환).
_RE_BODY_ALERTS = re.compile(r"alerts?\s*[:=]\s*(\d+)", re.IGNORECASE)
_RE_BODY_DRIFT = re.compile(r"mean\s*\|?Δ\|?\s*%?\s*[:=]\s*([+\-]?[0-9.]+)", re.IGNORECASE)


# ── 상태 파일 IO ─────────────────────────────────────────────────────────
def _load_state(state_path: Path) -> Optional[Dict[str, Any]]:
    """상태 파일 로드. 없으면 None."""
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# ── 보고서 스캔 ──────────────────────────────────────────────────────────
def _scan_validate_reports(
    reports_dir: Path, days: int,
) -> List[Dict[str, Any]]:
    """``reports/workflow_validate_R*.md`` 중 mtime 이 ``days`` 윈도우 안인 것.

    각 항목::

        {
          "path": "reports/workflow_validate_R23_meta.md",
          "round": "R23",
          "mtime_utc": "2026-06-05T22:23:00+00:00",
          "size_bytes": 7669,
          "body_alerts": 0,        # 본문에서 추출한 alerts 카운트 (있으면)
          "body_drift_pct": 0.74,  # 본문에서 추출한 mean |Δ|% (있으면)
        }
    """
    if not reports_dir.is_dir():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for p in sorted(reports_dir.glob("workflow_validate_*.md")):
        try:
            st = p.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            continue
        m = _RE_VALIDATE_ROUND.search(p.name)
        round_id = m.group(1).upper() if m else "?"
        # 본문 alerts/drift (graceful — 없으면 None)
        body_alerts: Optional[int] = None
        body_drift: Optional[float] = None
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            ma = _RE_BODY_ALERTS.search(txt)
            if ma:
                try:
                    body_alerts = int(ma.group(1))
                except ValueError:
                    pass
            md = _RE_BODY_DRIFT.search(txt)
            if md:
                try:
                    body_drift = float(md.group(1))
                except ValueError:
                    pass
        except OSError:
            pass
        out.append({
            "path": str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p),
            "round": round_id,
            "mtime_utc": mtime.isoformat(timespec="seconds"),
            "size_bytes": int(st.st_size),
            "body_alerts": body_alerts,
            "body_drift_pct": body_drift,
        })
    return out


# ── archive 교차 확인 ────────────────────────────────────────────────────
def _existing_archive_rounds(archive_dir: Path) -> List[str]:
    """``reports/archive/R*/`` 실재 디렉토리 → round 코드 목록.

    Harvest 4 H1: ``R\\d+`` 패턴 + 명명 라운드 (``harvest3p`` / ``harvest4``)
    둘 다 인식. archive 폴더가 ``.sentinel.json`` 만 가지고 있더라도 (빈 라운드)
    여전히 존재하는 round 로 집계 — Hook validator 가 archive_drift 오탐 방지.
    """
    if not archive_dir.is_dir():
        return []
    out: List[str] = []
    for p in sorted(archive_dir.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        # R{N} 패턴 → 대문자 정규화 (기존 호환).
        if name[:1].upper() == "R" and name[1:].isdigit():
            out.append(name.upper())
        # harvest* / 기타 명명 라운드 → 원본 그대로.
        elif name.startswith("harvest") or name == "unlabeled":
            out.append(name)
    return out


# ── False positive 분류 ──────────────────────────────────────────────────
def _classify_false_positives(
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """history 안에서 같은 round 가 반복 alert 을 내는지 분석.

    분류:
      - persistent_unresolved : 같은 round 2+회 + 모두 alerts>=1
      - resolved              : 같은 round 의 마지막 entry alerts=0
      - one_shot              : 1회만 등장
      - clean                 : 모든 등장 alerts=0
    """
    by_round: Dict[str, List[Dict[str, Any]]] = {}
    for h in history:
        rid = str(h.get("round") or "?")
        by_round.setdefault(rid, []).append(h)

    persistent: List[str] = []
    resolved: List[str] = []
    one_shot: List[str] = []
    clean: List[str] = []
    for rid, entries in by_round.items():
        # mtime 또는 scanned_at_utc 기준 정렬.
        def _key(e: Dict[str, Any]) -> str:
            return str(e.get("scanned_at_utc") or e.get("report_mtime_utc") or "")
        entries_sorted = sorted(entries, key=_key)
        alerts_seq = [int(e.get("alerts") or 0) for e in entries_sorted]
        n = len(entries_sorted)
        any_alert = any(a > 0 for a in alerts_seq)
        last_alert = alerts_seq[-1] if alerts_seq else 0
        if n == 1:
            (one_shot if any_alert else clean).append(rid)
        elif not any_alert:
            clean.append(rid)
        elif last_alert == 0:
            resolved.append(rid)
        else:
            # 모두 alerts>=1 또는 마지막에 alerts>=1
            persistent.append(rid)
    return {
        "persistent_unresolved": sorted(persistent),
        "resolved": sorted(resolved),
        "one_shot_with_alerts": sorted(one_shot),
        "clean": sorted(clean),
    }


# ── 권고 생성 ────────────────────────────────────────────────────────────
def _build_recommendations(
    summary: Dict[str, Any],
    history: List[Dict[str, Any]],
    fp: Dict[str, Any],
    archive_drift_unresolved: List[str],
) -> List[str]:
    """운영자 권고 문장 (한국어)."""
    recs: List[str] = []
    sc = int(summary.get("scan_count") or 0)
    days = int(summary.get("days") or 7)
    if sc < days * 24 * 12 * 0.5:  # 5분 주기 = 일 288회 → 7일 2016회의 50% 이하
        recs.append(
            f"scan_count={sc} ({days}일 윈도우) — 5분 주기 기준 기대치의 50% 미만. "
            "Celery beat `validator-hook-5m` 가동 상태 점검."
        )
    if summary.get("alerts_total", 0) == 0 and history:
        recs.append(
            "alerts_total=0 — 1주간 자동 캡처 0건. drift threshold 0.10 가 너무 "
            "느슨할 가능성, 또는 보고서가 안정화된 상태."
        )
    if fp["persistent_unresolved"]:
        recs.append(
            f"persistent unresolved {len(fp['persistent_unresolved'])}건: "
            f"{', '.join(fp['persistent_unresolved'][:5])} — 후속 라운드에서도 "
            "alert 미해소. false positive 의심 또는 보고서 자체 drift 시정 필요."
        )
    if archive_drift_unresolved:
        recs.append(
            f"archive_drift 미해소 {len(archive_drift_unresolved)}건: "
            f"{', '.join(archive_drift_unresolved[:5])} — 보고서가 archive 경로 "
            "주장 후 디렉토리 미생성. R25 회고 패턴 재발."
        )
    if not recs:
        recs.append("이상 패턴 없음 — 후크 1주 운영 정상.")
    return recs


# ── 외부 진입점 ──────────────────────────────────────────────────────────
def compute(
    *,
    days: int = 7,
    state_path: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
    archive_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """1주 후크 운영 통계.

    응답::

        {
          "generated_at_utc": "...",
          "days": 7,
          "available": true,
          "state": {
            "hook_active": true,
            "scan_count": 42,
            "last_scan_utc": "...",
            "last_alerts_total": 1,
            "last_archive_drift_total": 1,
          },
          "summary": {
            "days": 7,
            "scan_count": 42,
            "history_entries": 5,
            "alerts_total": 3,
            "archive_drift_total": 1,
            "mean_drift_pct": 18.5,
            "max_drift_pct": 55.0,
            "validate_reports": 3
          },
          "history": [...],
          "validate_reports": [...],
          "archive_existing": ["R26"],
          "archive_drift_unresolved": ["R25"],
          "false_positive_analysis": {
            "persistent_unresolved": [...],
            "resolved": [...],
            "one_shot_with_alerts": [...],
            "clean": [...]
          },
          "recommendations": [...]
        }
    """
    state_path = state_path or DEFAULT_STATE_PATH
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    archive_dir = archive_dir or DEFAULT_ARCHIVE_DIR
    days = max(1, int(days))

    state = _load_state(state_path)
    now_utc = datetime.now(timezone.utc)
    if state is None:
        return {
            "generated_at_utc": now_utc.isoformat(timespec="seconds"),
            "days": days,
            "available": False,
            "reason": f"state file 부재: {state_path}",
        }

    history: List[Dict[str, Any]] = list(state.get("history") or [])
    # archive 실재 확인.
    archive_existing = _existing_archive_rounds(archive_dir)
    # archive_drift unresolved — history 안의 archive_drift 중 archive_existing 에
    # 포함된 round 는 *해소* 로 간주.
    drift_unresolved: List[str] = []
    for h in history:
        for raw in (h.get("archive_drift") or []):
            mm = re.search(r"R(\d+)", str(raw))
            if not mm:
                continue
            rid = f"R{mm.group(1)}"
            if rid not in archive_existing and rid not in drift_unresolved:
                drift_unresolved.append(rid)

    validate_reports = _scan_validate_reports(reports_dir, days=days)

    # 집계.
    drifts = [
        float(h.get("mean_abs_drift_pct"))
        for h in history
        if h.get("mean_abs_drift_pct") is not None
    ]
    max_drifts = [
        float(h.get("max_abs_drift_pct"))
        for h in history
        if h.get("max_abs_drift_pct") is not None
    ]
    alerts_total = sum(int(h.get("alerts") or 0) for h in history)
    archive_drift_total = sum(len(h.get("archive_drift") or []) for h in history)

    summary = {
        "days": days,
        "scan_count": int(state.get("scan_count") or 0),
        "history_entries": len(history),
        "alerts_total": alerts_total,
        "archive_drift_total": archive_drift_total,
        "mean_drift_pct": round(fmean(drifts), 2) if drifts else 0.0,
        "max_drift_pct": round(max(max_drifts), 2) if max_drifts else 0.0,
        "validate_reports": len(validate_reports),
    }

    fp = _classify_false_positives(history)
    recs = _build_recommendations(summary, history, fp, drift_unresolved)

    return {
        "generated_at_utc": now_utc.isoformat(timespec="seconds"),
        "days": days,
        "available": True,
        "state": {
            "hook_active": bool(state.get("hook_active", True)),
            "scan_count": int(state.get("scan_count") or 0),
            "last_scan_utc": state.get("last_scan_utc"),
            "last_alerts_total": int(state.get("last_alerts_total") or 0),
            "last_archive_drift_total": int(state.get("last_archive_drift_total") or 0),
        },
        "summary": summary,
        "history": history,
        "validate_reports": validate_reports,
        "archive_existing": archive_existing,
        "archive_drift_unresolved": drift_unresolved,
        "false_positive_analysis": fp,
        "recommendations": recs,
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def _fmt_table(result: Dict[str, Any]) -> str:
    """사람 가독 요약."""
    rows: List[str] = []
    rows.append("# Workflow validator hook — 1주 운영 통계")
    rows.append("")
    rows.append(f"생성: {result['generated_at_utc']}  available={result['available']}")
    if not result["available"]:
        rows.append(f"사유: {result.get('reason')}")
        return "\n".join(rows)
    st = result["state"]
    sm = result["summary"]
    rows.append(
        f"hook_active={st['hook_active']}  scan_count={st['scan_count']}  "
        f"last_scan={st['last_scan_utc']}"
    )
    rows.append("")
    rows.append("## 1주 요약")
    rows.append("")
    rows.append(f"- 윈도우: {sm['days']}일")
    rows.append(f"- history 엔트리: {sm['history_entries']}")
    rows.append(f"- alerts 누계: {sm['alerts_total']}")
    rows.append(f"- archive_drift 누계: {sm['archive_drift_total']}")
    rows.append(f"- mean drift: {sm['mean_drift_pct']}%")
    rows.append(f"- max drift: {sm['max_drift_pct']}%")
    rows.append(f"- workflow_validate 보고서 (윈도우): {sm['validate_reports']}편")
    rows.append("")
    fp = result["false_positive_analysis"]
    rows.append("## False positive 분석")
    rows.append("")
    rows.append(f"- persistent_unresolved: {fp['persistent_unresolved']}")
    rows.append(f"- resolved: {fp['resolved']}")
    rows.append(f"- one_shot_with_alerts: {fp['one_shot_with_alerts']}")
    rows.append(f"- clean: {fp['clean']}")
    rows.append("")
    rows.append(f"## archive 실재: {result['archive_existing']}")
    rows.append(f"## archive_drift 미해소: {result['archive_drift_unresolved']}")
    rows.append("")
    rows.append("## 권고")
    rows.append("")
    for r in result["recommendations"]:
        rows.append(f"- {r}")
    return "\n".join(rows)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--days", type=int, default=7,
                   help="윈도우 (1~30일, 기본 7).")
    p.add_argument("--state-path", default=str(DEFAULT_STATE_PATH),
                   help=f"기본 {DEFAULT_STATE_PATH}.")
    p.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR),
                   help=f"기본 {DEFAULT_REPORTS_DIR}.")
    p.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR),
                   help=f"기본 {DEFAULT_ARCHIVE_DIR}.")
    p.add_argument("--json", action="store_true", help="JSON 출력.")
    args = p.parse_args(argv)

    result = compute(
        days=int(args.days),
        state_path=Path(args.state_path),
        reports_dir=Path(args.reports_dir),
        archive_dir=Path(args.archive_dir),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_fmt_table(result))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
